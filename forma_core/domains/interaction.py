"""Portable external-agent continuations for the existing generation control plane.

The adapter pauses StructuredProvider calls, then replays accepted answers through
generate_engineering. It owns no domain selection, authoring sequence or registry.
Checkpoints are caller-owned data, not authenticated server-side revision locks.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, model_validator

from .base import DomainModel, InteractionRequired, Text, fingerprint
from .composition import ProductBundle, validate_product
from .generation import (
    CompositionDraft, ContextDraft, EngineeringRequest, EngineeringResult,
    generate_engineering, reject_invented_measurements,
)
from .providers import Digest, DomainRegistry
from .registry import get_registry, validate_domain
from .planning import context_errors
from .selection import SelectionDecision, SelectionProject, apply_selection


class AgentAction(DomainModel):
    """One response contract, containing everything needed without source inspection."""

    action_id: Digest
    kind: Literal["select_domains", "provide_context", "author_artifact", "compose"]
    domain: str | None = None
    instructions: Text
    inputs: dict[str, Any]
    response_schema: dict[str, Any]
    findings: list[dict[str, Any]] = Field(default_factory=list)
    can_defer: bool = True


class AgentAnswer(DomainModel):
    """An authored response or an explicit inability to complete this action."""

    response: dict[str, Any] | None = None
    defer: Text | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> AgentAnswer:
        """Do not silently prefer authored content over a declared blocker."""
        if (self.response is None) == (self.defer is None):
            raise ValueError("Supply exactly one of response or defer.")
        return self


class GenerationCheckpoint(DomainModel):
    """Serializable interaction journal; completed artifacts remain ordinary domain IRs."""

    format: Literal["forma.generation-checkpoint"] = "forma.generation-checkpoint"
    schema_version: Literal["1.0"] = "1.0"
    run_id: Text
    revision: int = Field(0, ge=0)
    started_at: Text
    registry_sha256: Digest
    request: EngineeringRequest
    answers: dict[str, AgentAnswer] = Field(default_factory=dict)
    activities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    receipts: dict[str, Digest] = Field(default_factory=dict)
    pending: AgentAction | None = None
    result: EngineeringResult | None = None


class Continuation(DomainModel):
    """Checksum detects damaged/mismatched checkpoints, not malicious modification."""

    state: GenerationCheckpoint
    sha256: Digest

    @model_validator(mode="after")
    def intact(self) -> Continuation:
        """Reject edited state rather than replaying it as the previous checkpoint."""
        if fingerprint(self.state) != self.sha256:
            raise ValueError("Continuation checksum differs; use the unchanged returned continuation.")
        return self


class Submission(AgentAnswer):
    """Optimistic action submission with a stable retry key supplied by OpenForma."""

    action_id: Digest
    expected_revision: int = Field(ge=0)
    submission_id: Text


class ResumeInput(DomainModel):
    """Resume without submitting, or answer the pending action against its checkpoint."""

    continuation: Continuation
    submission: Submission | None = None


def _remove_fields(schema: dict[str, Any], fields: set[str]) -> None:
    """Hide machine-owned fields in the agent's response schema only."""
    for key in fields:
        schema.get("properties", {}).pop(key, None)
    if "required" in schema:
        schema["required"] = [key for key in schema["required"] if key not in fields]


class ExternalAgent:
    """Adapt existing structured authoring calls to typed, resumable handoffs."""

    def __init__(self, state: GenerationCheckpoint, registry: DomainRegistry) -> None:
        """Reuse the selected registry and the caller-owned answer journal."""
        self.state = state
        self.registry = registry

    def generate_structured(self, prompt: str, schema_class: type[BaseModel]) -> BaseModel:
        """Replay a validated response or expose the exact current authoring action."""
        instructions, raw_inputs = prompt.rsplit("\n", 1)
        inputs = json.loads(raw_inputs)
        kind = ("select_domains" if schema_class is SelectionDecision else
                "provide_context" if schema_class is ContextDraft else
                "compose" if schema_class is CompositionDraft else "author_artifact")
        schema = schema_class.model_json_schema()
        action_inputs = {key: value for key, value in inputs.items()
                         if key not in {"decision_schema", "schema_definition", "instructions", "submit_operation"}}
        if kind == "select_domains":
            _remove_fields(schema, {"request_sha256", "registry_sha256"})
        elif kind == "author_artifact":
            _remove_fields(schema, {"artifact_id", "revision", "domain"})
        elif kind == "compose":
            _remove_fields(schema.get("$defs", {}).get("LinkEndpoint", {}), {"revision", "sha256"})
        action = AgentAction(
            action_id=fingerprint({"run_id": self.state.run_id, "prompt": prompt,
                                   "schema": schema_class.model_json_schema()}),
            kind=kind, domain=inputs.get("domain"), instructions=instructions +
            "\nYou are the authoring agent. Return a JSON object matching response_schema. "
            "OpenForma fills selection digests, artifact identity/revision/domain and new link pins. "
            "Do not call another model or read repository source. Use only the objective, supplied "
            "context and schemas. Use explicit unknowns instead of invented measurements. "
            "If this action cannot be completed, submit defer with the reason; independent domains may continue.",
            inputs=action_inputs, response_schema=schema, can_defer=kind != "select_domains")
        answer = self.state.answers.get(action.action_id)
        if answer is None:
            raise InteractionRequired(action.model_dump(mode="json"))
        if answer.defer:
            if not action.can_defer:
                action.findings = [{"code": "selection_required", "message": "Select the required domains before deferring domain work."}]
                raise InteractionRequired(action.model_dump(mode="json"))
            raise ValueError("Agent deferred: " + answer.defer)
        try:
            payload = self._normalize(action, answer.response or {})
            parsed = schema_class.model_validate(payload)
            self._validate(action, payload, parsed)
            return parsed
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            action.findings = (exc.errors(include_url=False, include_context=False, include_input=False)
                               if isinstance(exc, ValidationError) else
                               [{"code": "invalid_response", "message": str(exc)}])
            raise InteractionRequired(action.model_dump(mode="json")) from exc

    def _normalize(self, action: AgentAction, response: dict[str, Any]) -> dict[str, Any]:
        """Allocate exact identity/pins; reject supplied conflicts rather than repinning."""
        payload = deepcopy(response)
        supplied: dict[str, Any] = {}
        if action.kind == "select_domains":
            supplied = {key: action.inputs[key] for key in ("request_sha256", "registry_sha256")}
        elif action.kind == "author_artifact":
            supplied = {key: action.inputs[key] for key in ("artifact_id", "revision", "domain")}
        for key, value in supplied.items():
            if key in payload and payload[key] != value:
                raise ValueError(f"{key} conflicts with OpenForma's allocated value.")
            payload[key] = value
        if action.kind == "compose":
            refs = {ref["artifact_id"]: ref for ref in action.inputs["product"]["manifest"]["artifacts"]}
            for link in payload.get("links", []):
                for side in ("source", "target"):
                    endpoint = link.get(side, {})
                    reference = refs.get(endpoint.get("artifact_id"))
                    if reference is None:
                        raise ValueError(f"Unknown {side} artifact; use the supplied manifest handles.")
                    for key in ("revision", "sha256"):
                        if endpoint.get(key) is not None and endpoint[key] != reference[key]:
                            raise ValueError(f"Stale {side} {key}; review the supplied current artifact.")
                        endpoint[key] = reference[key]
        return payload

    def _validate(self, action: AgentAction, raw: dict[str, Any], parsed: BaseModel) -> None:
        """Reuse existing semantic validators before advancing past an agent action."""
        if action.kind == "select_domains":
            apply_selection(SelectionProject.model_validate(action.inputs["project"]), parsed, registry=self.registry)
        elif action.kind == "provide_context":
            missing = context_errors(action.domain, parsed.context, registry=self.registry)
            if missing and not parsed.missing:
                raise ValueError("Missing context: " + "; ".join(missing) + ". Supply it or explicitly report it in missing.")
        elif action.kind == "author_artifact":
            reject_invented_measurements(raw, action.inputs.get("previous") or {})
            errors = [f.model_dump(mode="json") for f in validate_domain(raw) if f.severity == "error"]
            if errors:
                raise ValueError(json.dumps(errors))
        elif action.kind == "compose":
            bundle = ProductBundle.model_validate(action.inputs["product"])
            bundle.manifest.links = parsed.links
            errors = [f.model_dump(mode="json") for f in validate_product(bundle, registry=self.registry).composition
                      if f.severity == "error"]
            if errors:
                raise ValueError(json.dumps(errors))
            domains = {ref.domain for ref in bundle.manifest.artifacts}
            if {"chemical", "process"} <= domains and not any(link.kind == "uses_reaction" for link in parsed.links):
                raise ValueError("Link the process reaction operation to its chemical reaction with uses_reaction, or defer composition explicitly.")

    def activity(self, name: str, inputs: dict[str, Any], execute: Callable[[], Any]) -> Any:
        """Reuse owner authoring references; their bytes/identity are still owner-validated.

        Evaluations deliberately are not cached in caller-owned checkpoints.
        """
        key = fingerprint({"registry": self.state.registry_sha256, "name": name, "inputs": inputs})
        if key not in self.state.activities:
            try:
                output = execute()
                if isinstance(output, BaseModel):
                    output = output.model_dump(mode="json")
                fingerprint(output)  # Require finite, portable JSON before journaling an owner result.
                self.state.activities[key] = {"output": deepcopy(output)}
            except Exception as exc:
                self.state.activities[key] = {"error": str(exc)}
        record = self.state.activities[key]
        if "error" in record:
            raise ValueError(record["error"])
        return deepcopy(record["output"])


def _response(state: GenerationCheckpoint) -> dict[str, Any]:
    """Return a ready-to-use submission template alongside the portable checkpoint."""
    result: dict[str, Any] = {
        "format": "forma.generation-turn", "schema_version": "1.0", "run_id": state.run_id,
        "revision": state.revision, "status": "completed" if state.result else "awaiting_agent",
        "continuation": {"state": state.model_dump(mode="json"), "sha256": fingerprint(state)},
        "next_action": state.pending.model_dump(mode="json") if state.pending else None,
        "result": state.result.model_dump(mode="json") if state.result else None,
        "instructions": "Continue with the same generate operation. Copy continuation unchanged and fill submission.response using next_action.response_schema. "
                        "To resume without answering, send only continuation. A completed turn may contain a partial engineering result; inspect findings and unresolved information.",
    }
    if state.pending:
        result["submission_template"] = {
            "action_id": state.pending.action_id, "expected_revision": state.revision,
            "submission_id": f"{state.pending.action_id}:{state.revision}", "response": {},
        }
    return result


def advance_generation(payload: dict[str, Any], *, registry: DomainRegistry | None = None) -> dict[str, Any]:
    """Start from only a brief, submit one typed action, or resume without a provider.

The same generate_engineering function drives internal and external authoring.
Portable snapshots allow forks; they do not implement server-side concurrency
control. Publishing must still use the canonical project's revision guard.
"""
    registry = get_registry(registry)
    if "continuation" in payload:
        resume = ResumeInput.model_validate(payload)
        state = resume.continuation.state.model_copy(deep=True)
        if state.registry_sha256 != fingerprint(registry.catalog()):
            raise ValueError("Domain capabilities changed; start a fresh generation with the current registry.")
        if resume.submission is not None:
            submission = resume.submission
            digest = fingerprint(submission)
            receipt = state.receipts.get(submission.submission_id)
            if receipt is not None:
                if receipt != digest:
                    raise ValueError("Submission ID was already used with different content.")
            elif (state.result is not None or state.pending is None
                    or submission.expected_revision != state.revision
                    or submission.action_id != state.pending.action_id):
                raise ValueError("Stale action or revision; use the latest returned continuation and submission_template.")
            else:
                state.answers[submission.action_id] = AgentAnswer(response=submission.response, defer=submission.defer)
                state.receipts[submission.submission_id] = digest
                state.revision += 1
    else:
        request = EngineeringRequest.model_validate({"product_id": str(uuid4()), **payload})
        state = GenerationCheckpoint(run_id=str(uuid4()), started_at=datetime.now(timezone.utc).isoformat(),
                                     registry_sha256=fingerprint(registry.catalog()), request=request)
    agent = ExternalAgent(state, registry)
    # Checkpoints are portable caller data, not signed attestations. Reconstruct
    # instructions and recompute validation/evaluations even for completed runs;
    # never echo a caller-edited cached result as a newly verified result.
    state.pending = None
    state.result = None
    try:
        state.result = generate_engineering(state.request, provider=agent, registry=registry, activity_runner=agent.activity)
        # A logical run has one timestamp; replaying an identical checkpoint must
        # not mint different provenance solely because the wall clock advanced.
        state.result.provenance["created_at"] = state.started_at
        state.result.provenance["interaction_run_id"] = state.run_id
        state.pending = None
    except InteractionRequired as exc:
        state.pending = AgentAction.model_validate(exc.action)
    return _response(state)

"""Stateless project selection handoff for any calling agent, human or program."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import Domain, DomainModel, Text, fingerprint
from .planning import CompleteProductScope, ProductPlan, select_domains


SELECTION_INSTRUCTIONS = (
    "Interpret the requested project work and explicitly decide all six scope capabilities. "
    "Respect exclusions; an existing motor does not itself request wiring design. "
    "Missing specifications never remove a required domain; they block its later generation. "
    "Software-only work can select no physical domains. Do not substitute workflow stages for domains. "
    "Return a decision matching decision_schema, with the supplied request_sha256 and a brief-grounded "
    "reason for each selected domain (and no reasons for excluded domains). "
    "Call plan again with the returned project fields plus decision. "
    "You are the selecting agent; OpenForma will validate your decision without calling another model."
)


class SelectionProject(DomainModel):
    """The project inputs covered by a selection decision's content digest."""

    product_id: Text
    brief: Text = Field(max_length=12_000)
    contexts: dict[Domain, dict[str, Any]] = Field(default_factory=dict)


class SelectionDecision(DomainModel):
    """An external decision bound to project inputs, with explicit scope and reasons."""

    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: CompleteProductScope
    reasons: dict[Domain, Text]


def prepare_selection(project: SelectionProject) -> dict[str, Any]:
    """Expose everything a caller needs to select IRs, without resolving a provider."""
    return {
        "format": "forma.domain-selection-request",
        "schema_version": "1.0",
        "status": "awaiting_selection",
        "project": project.model_dump(mode="json"),
        "request_sha256": fingerprint(project),
        "instructions": SELECTION_INSTRUCTIONS,
        "domains": {
            "mechanical": {"ir_type": "MechanicalIR", "any_scope": ["physical_structure", "mechanical_motion"]},
            "electrical": {"ir_type": "ElectricalIR", "any_scope": ["wiring", "electrical_control"]},
            "biological": {"ir_type": "BiologicalIR", "any_scope": ["biological_experiment"]},
            "fluidic": {"ir_type": "FluidicIR", "any_scope": ["fluid_network"]},
        },
        "decision_schema": SelectionDecision.model_json_schema(),
        "submit_operation": "plan",
    }


def apply_selection(project: SelectionProject, decision: SelectionDecision) -> ProductPlan:
    """Validate the handoff and compile selected domains with their supplied reasons."""
    if decision.request_sha256 != fingerprint(project):
        raise ValueError("Selection inputs changed; call plan again to obtain a new request_sha256.")
    plan = select_domains(project.product_id, project.brief, scope=decision.scope, contexts=project.contexts)
    if set(decision.reasons) != {task.domain for task in plan.tasks}:
        raise ValueError("Decision reasons must cover exactly the selected domains.")
    for task in plan.tasks:
        task.reason = decision.reasons[task.domain]
    return plan

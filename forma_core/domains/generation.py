"""Independent authoring calls, with explicit projections for cross-domain inputs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .base import Domain, DomainModel, Text, fingerprint, unique
from .composition import ArtifactReference, DomainLink, LinkEndpoint, ProductBundle, compose_product, validate_product
from .planning import ProductPlan, context_errors
from .providers import DomainRegistry, NATIVE_PROVIDER
from .registry import domain_model, entities, get_registry, parse_domain, validate_domain
from .selection import SelectionDecision

if TYPE_CHECKING:
    from .agents import StructuredProvider


class DeclaredInput(DomainModel):
    """One explicitly requested upstream field, pinned to an object and revision."""

    target_domain: Domain
    source: LinkEndpoint
    field: Text = Field(description="Top-level domain field to project, for example parts or system.")


class GenerationRequest(DomainModel):
    """Exactly what an author sees: one domain's context and declared upstream data."""

    domain: Domain
    artifact_id: Text
    revision: int
    context: dict[str, Any]
    schema_definition: dict[str, Any]
    cross_domain_inputs: list[dict[str, Any]] = Field(default_factory=list)
    previous: dict[str, Any] | None = None


DomainAuthor = Callable[[GenerationRequest], dict[str, Any] | BaseModel]


def generate_product(
    plan: ProductPlan,
    author: DomainAuthor,
    *,
    existing: ProductBundle | None = None,
    inputs: list[DeclaredInput] | None = None,
    links: list[DomainLink] | None = None,
    update_domains: set[Domain] | None = None,
    references: list[ArtifactReference] | None = None,
    registry: DomainRegistry | None = None,
) -> ProductBundle:
    """Generate only selected ready domains; keep independent successes on failure.

    The caller supplies a provider callback or an offline fixture author. No
    all-domain prompt, fabricated missing specification, or placeholder artifact
    is produced. Existing domains update only when explicitly requested.
    """
    plan = plan.model_copy(deep=True)
    registry = get_registry(registry)
    if existing is not None and existing.manifest.product_id != plan.product_id:
        raise ValueError("Existing product identity differs from the plan.")
    if existing is not None:
        integrity_codes = {"artifact_hash", "artifact_metadata", "missing_artifact", "unreferenced_artifact"}
        native_ids = {r.artifact_id for r in existing.manifest.artifacts if r.provider_id == NATIVE_PROVIDER}
        if any(f.code in integrity_codes and (f.location in native_ids or f.code == "unreferenced_artifact")
               for f in validate_product(existing, registry=registry).composition):
            raise ValueError("Existing artifact integrity failed; reconcile its content and revision before updating.")
    existing_refs = existing.manifest.artifacts if existing else []
    previous = {r.domain: existing.artifacts[r.artifact_id] for r in existing_refs
                if r.provider_id == NATIVE_PROVIDER and r.artifact_id in existing.artifacts}
    external = {r.domain: r for r in existing_refs if r.provider_id != NATIVE_PROVIDER}
    unique([r.domain for r in references or []], "supplied external domain")
    for reference in references or []:
        if reference.provider_id == NATIVE_PROVIDER:
            raise ValueError("Native generation references are produced by the existing document author.")
        external[reference.domain] = reference
    selected = {t.domain for t in plan.tasks}
    if (set(previous) | set(external)) - selected:
        raise ValueError("Existing artifacts cannot be silently dropped by a narrower plan.")
    if update_domains is not None and update_domains - selected:
        raise ValueError("Updates must target selected domains.")
    supplied_references = {r.domain for r in references or []}
    if update_domains and (update_domains & set(external)) - supplied_references:
        raise ValueError("Updating an external domain requires an updated pinned reference from its provider.")
    documents: dict[Domain, BaseModel] = {}
    pending = []
    for task in plan.tasks:
        task.errors = []
        task.provider = registry.metadata(task.domain)
        task.missing_inputs = context_errors(task.domain, task.context, registry=registry)
        task.unresolved_dependencies = []
        if task.provider is None or task.provider.provider_id != NATIVE_PROVIDER:
            if task.domain in external:
                task.status = "generated"
            else:
                task.status = "blocked"
                task.unresolved_dependencies = [f"External domain '{task.domain}' needs a pinned artifact reference from its owning provider."]
            if registry.provider(task.domain) is None:
                task.status = "blocked"
                task.unresolved_dependencies.append(f"Provider for '{task.domain}' is unavailable.")
            continue
        if registry.provider(task.domain) is None:
            task.status = "blocked"
            task.unresolved_dependencies = [f"Provider for '{task.domain}' is unavailable."]
            if task.domain in previous:
                documents[task.domain] = parse_domain(previous[task.domain])
            continue
        old = previous.get(task.domain)
        if old is not None and (update_domains is None or task.domain not in update_domains):
            documents[task.domain] = parse_domain(old)
            task.status = "generated" if not any(i.severity == "error" for i in validate_domain(old)) else "invalid"
            continue
        if old is not None:
            documents[task.domain] = parse_domain(old)
        if task.missing_inputs:
            task.status = "blocked"
        else:
            pending.append(task)
    # Dependency scheduling: retry pending tasks only after another domain completes.
    while pending:
        progressed = False
        waiting = []
        for task in pending:
            declared = [i for i in (inputs or []) if i.target_domain == task.domain]
            projections = []
            dependency_errors = []
            for item in declared:
                source = next((d for d in documents.values() if d.artifact_id == item.source.artifact_id), None)
                source_task = next((t for t in plan.tasks if source is not None and t.domain == source.domain), None)
                if (source is None or source.domain == task.domain or source_task.status != "generated"
                        or (item.source.revision is not None and source.revision != item.source.revision)
                        or (item.source.sha256 is not None and fingerprint(source) != item.source.sha256)
                        or item.source.object_id not in entities(source)):
                    dependency_errors.append(f"Unavailable or stale declared input: {item.source.artifact_id}")
                    continue
                payload = source.model_dump(mode="json")
                if item.field not in payload:
                    dependency_errors.append(f"Unknown declared field '{item.field}'.")
                    continue
                if item.source.port_id is not None and item.source.port_id not in entities(source)[item.source.object_id]:
                    dependency_errors.append(f"Unknown declared port '{item.source.port_id}'.")
                    continue
                projections.append({"reference": item.source.model_dump(mode="json"),
                                    "field": item.field, "value": payload[item.field]})
            if dependency_errors:
                task.errors = dependency_errors
                waiting.append(task)
                continue
            old = previous.get(task.domain)
            request = GenerationRequest(
                domain=task.domain, artifact_id=old["artifact_id"] if old else f"{plan.product_id}.{task.domain}",
                revision=old["revision"] + 1 if old else 1,
                context=domain_model(task.domain, context=True).model_validate(task.context).model_dump(mode="json"),
                schema_definition=domain_model(task.domain).model_json_schema(),
                cross_domain_inputs=projections, previous=old,
            )
            try:
                output = author(request.model_copy(deep=True))
                if isinstance(output, BaseModel):
                    output = output.model_dump(mode="json")
                if not isinstance(output, dict):
                    raise TypeError("Domain author must return a document object.")
                document = parse_domain(output)
                if (document.domain, document.artifact_id, document.revision) != (
                    request.domain, request.artifact_id, request.revision,
                ):
                    raise ValueError("Author changed the requested domain, artifact identity or revision.")
                findings = validate_domain(document)
                task.errors = [i.message for i in findings if i.severity == "error"]
                task.status = "invalid" if task.errors else "generated"
                if task.status == "generated":
                    documents[task.domain] = document
            except Exception as exc:
                # The author is an external callback/provider boundary. One
                # failure must not cancel unrelated selected domains.
                task.status = "failed"
                task.errors = [str(exc)]
            progressed = True
        if not progressed:
            for task in waiting:
                task.status = "blocked"
            break
        pending = waiting
    bundle = compose_product(plan.product_id, list(documents.values()), plan=plan,
                             links=links if links is not None else (existing.manifest.links if existing else []),
                             references=list(external.values()))
    if existing is not None:
        bundle.compatibility = existing.compatibility
        for reference in external.values():
            if (any(r.artifact_id == reference.artifact_id and r.sha256 == reference.sha256 for r in existing_refs)
                    and reference.artifact_id in existing.artifacts):
                # Preserve explicitly cached external payloads unchanged; references stay authoritative.
                bundle.artifacts[reference.artifact_id] = deepcopy(existing.artifacts[reference.artifact_id])
    return bundle


def fixture_author(designs: dict[Domain, dict[str, Any]]) -> DomainAuthor:
    """Create an offline author from explicitly supplied designs, never inferred data."""
    def author(request: GenerationRequest) -> dict[str, Any]:
        """Add requested identity metadata to the selected domain's supplied design."""
        if request.domain not in designs:
            raise ValueError(f"No supplied design for {request.domain}.")
        return {**designs[request.domain], "artifact_id": request.artifact_id,
                "revision": request.revision, "domain": request.domain,
                "schema_version": designs[request.domain].get("schema_version", "1.0")}
    return author


class EngineeringRequest(DomainModel):
    """Generation intent and optional caller-authored inputs, never domain semantics."""

    product_id: Text
    brief: Text = Field(max_length=12_000)
    contexts: dict[Domain, dict[str, Any]] = Field(default_factory=dict)
    decision: SelectionDecision | None = None
    designs: dict[Domain, dict[str, Any]] | None = None
    assumptions: dict[Domain, list[Text]] = Field(default_factory=dict)
    unknowns: list[Text] = Field(default_factory=list)
    existing: ProductBundle | None = None
    update_domains: set[Domain] | None = None
    inputs: list[DeclaredInput] = Field(default_factory=list)
    links: list[DomainLink] | None = None
    references: list[ArtifactReference] = Field(default_factory=list)


class ContextDraft(DomainModel):
    """An agent's domain-specific extraction, including information it could not find."""

    context: dict[str, Any]
    missing: list[Text] = Field(default_factory=list)
    assumptions: list[Text] = Field(default_factory=list)
    unknowns: list[Text] = Field(default_factory=list)


class CompositionDraft(DomainModel):
    """Advisory links; deterministic composition validation remains authoritative."""

    links: list[DomainLink] = Field(default_factory=list)
    unresolved: list[Text] = Field(default_factory=list)


class EngineeringResult(DomainModel):
    """Portable run report around the existing product bundle, not another IR."""

    format: Literal["forma.engineering-result"] = "forma.engineering-result"
    schema_version: Literal["1.0"] = "1.0"
    product: ProductBundle
    validation: dict[str, Any]
    evaluations: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: dict[Domain, list[str]] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    status: Literal["succeeded", "partial", "failed"]


def generate_engineering(
    request: EngineeringRequest,
    *,
    provider: StructuredProvider | None = None,
    registry: DomainRegistry | None = None,
) -> EngineeringResult:
    """Coordinate selection, isolated authoring, composition and supported evaluation.

    This extends generate_product rather than replacing its identity, ownership,
    revision or validation contracts. A calling agent may supply a digest-bound
    selection and independently authored designs; otherwise the configured
    structured provider performs those advisory steps. No legacy IR is created.
    """
    from datetime import datetime, timezone
    from .agents import provider_author
    from .base import canonical_json
    from .selection import SelectionDecision, SelectionProject, apply_selection, prepare_selection

    request = request.model_copy(deep=True)
    registry = get_registry(registry)
    project = SelectionProject(product_id=request.product_id, brief=request.brief, contexts=request.contexts)
    selection = prepare_selection(project, registry=registry)
    if request.decision is not None:
        decision = SelectionDecision.model_validate(request.decision)
    elif provider is not None:
        decision = SelectionDecision.model_validate(provider.generate_structured(
            "Select the engineering domains required by this objective, with explicit reasons. "
            "Chemistry and process operations require chemical and process, not mechanical geometry "
            "or electrical wiring. Select thermal only for requested heat/temperature analysis. "
            "Component presence is not requested disciplinary scope. Use the discovered registry.\n"
            + canonical_json(selection), SelectionDecision))
    else:
        raise ValueError("A configured structured provider or a caller-authored selection decision is required.")
    plan = apply_selection(project, decision, registry=registry)
    unresolved: list[str] = list(request.unknowns)
    assumptions: dict[str, list[str]] = deepcopy(request.assumptions)
    blocked_context: dict[str, list[str]] = {}
    for task in plan.tasks:
        old = request.existing
        unchanged = old is not None and any(r.domain == task.domain for r in old.manifest.artifacts) and (
            request.update_domains is None or task.domain not in request.update_domains)
        if unchanged or task.domain in request.contexts or provider is None:
            continue
        try:
            metadata = registry.metadata(task.domain)
            schema = (domain_model(task.domain, context=True).model_json_schema()
                      if metadata and metadata.provider_id == NATIVE_PROVIDER else None)
            draft = ContextDraft.model_validate(provider.generate_structured(
                "Extract only the requested domain's authoring context from the objective. "
                "Use the given context schema. Do not invent specifications, measurements, numbers or sources. "
                "Return absent required inputs in missing; do not fill required fields with placeholders. "
                "An explicit conceptual scope may state unknown performance without blocking representation. "
                "List any transparent modeling assumptions separately.\n" + canonical_json({
                    "domain": task.domain, "requirements": [r.model_dump(mode="json") for r in task.required_context],
                    "context_schema": schema, "brief": request.brief,
                }), ContextDraft))
            task.context = draft.context
            assumptions[task.domain] = draft.assumptions
            unresolved.extend(f"{task.domain}: {item}" for item in draft.unknowns)
            if draft.missing:
                blocked_context[task.domain] = draft.missing
        except Exception as exc:
            blocked_context[task.domain] = [f"Context extraction failed: {exc}"]

    # External schemas and authoring remain with their registered owner.
    external_references = list(request.references)
    supplied = {r.domain for r in external_references}
    existing_domains = {r.domain for r in request.existing.manifest.artifacts} if request.existing else set()
    for task in plan.tasks:
        metadata, adapter = registry.metadata(task.domain), registry.provider(task.domain)
        if (metadata is None or metadata.provider_id == NATIVE_PROVIDER or not metadata.authoring or adapter is None
                or task.domain in supplied or task.domain in blocked_context
                or context_errors(task.domain, task.context, registry=registry)
                or (task.domain in existing_domains and task.domain not in (request.update_domains or set()))):
            continue
        try:
            reference = ArtifactReference.model_validate(adapter.author({
                "product_id": request.product_id, "domain": task.domain, "context": deepcopy(task.context),
                "previous": next((r.model_dump(mode="json") for r in request.existing.manifest.artifacts
                                  if r.domain == task.domain), None) if request.existing else None,
            }))
            if reference.domain != task.domain or registry.reference_errors(reference):
                raise ValueError("External author returned an incompatible artifact identity.")
            external_references.append(reference)
        except Exception as exc:
            unresolved.append(f"{task.domain}: external authoring failed: {exc}")

    base_author = fixture_author(request.designs) if request.designs is not None else (
        provider_author(provider) if provider is not None else None)

    def author(item: GenerationRequest) -> dict[str, Any] | BaseModel:
        """Reject invented measurements before accepting any native artifact."""
        if item.domain in blocked_context:
            raise ValueError("Missing requirements: " + "; ".join(blocked_context[item.domain]))
        if base_author is None:
            raise ValueError("No author configured for this domain.")
        output = base_author(item)
        payload = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
        previous = item.previous or {}

        def measured(value: Any) -> set[str]:
            """Find exact existing observation claims; new generation cannot mint them."""
            if isinstance(value, dict):
                is_measurement = value.get("basis") == "measured" or (isinstance(value.get("claim"), dict) and value["claim"].get("basis") == "measured")
                own = {canonical_json(value)} if is_measurement else set()
                return own.union(*(measured(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(measured(v) for v in value))
            return set()

        if measured(payload) - measured(previous):
            raise ValueError("Generation cannot introduce experimental measurements; import sourced observations separately.")
        return output

    # An unavailable owner cannot supply a replacement pin. Retain its previous
    # revision, mark the requested update blocked, and still author other domains.
    supplied_updates = {r.domain for r in external_references}
    retained_external = {r.domain for r in request.existing.manifest.artifacts
                         if r.provider_id != NATIVE_PROVIDER and r.domain in (request.update_domains or set())
                         and r.domain not in supplied_updates} if request.existing else set()
    for domain in retained_external:
        blocked_context.setdefault(domain, []).append("External update unavailable; previous pinned artifact retained.")
    effective_updates = request.update_domains - retained_external if request.update_domains is not None else None
    bundle = generate_product(plan, author, existing=request.existing, inputs=request.inputs,
                              links=request.links, update_domains=effective_updates,
                              references=external_references, registry=registry)
    old_refs = {r.artifact_id: r for r in request.existing.manifest.artifacts} if request.existing else {}
    for ref in bundle.manifest.artifacts:
        old_ref = old_refs.get(ref.artifact_id)
        if old_ref is not None and old_ref.sha256 == ref.sha256:
            ref.provenance = deepcopy(old_ref.provenance)
        elif ref.provider_id == NATIVE_PROVIDER:
            task = next(t for t in bundle.plan.tasks if t.domain == ref.domain)
            ref.provenance = {"request_sha256": fingerprint(project), "context_sha256": fingerprint(task.context),
                              "author": "caller" if request.designs is not None else type(provider).__name__}
    for task in bundle.plan.tasks:
        if task.domain in blocked_context:
            task.status = "blocked"
            task.missing_inputs = blocked_context[task.domain]
    # Composition receives explicit frozen artifacts, never rewrites their contents,
    # and never repairs stale existing pins silently during iteration.
    if request.links is None and request.existing is None and len(bundle.manifest.artifacts) > 1:
        if provider is None:
            unresolved.append("Cross-domain relationships need caller-authored links or a configured provider.")
        else:
            try:
                draft = CompositionDraft.model_validate(provider.generate_structured(
                    "Compose supported typed links between these exact independently owned artifacts. "
                    "Pin revision AND sha256 from manifest references; use stable entity/port IDs. "
                    "Link process operations to chemical reactions with uses_reaction and material streams "
                    "to species with uses_species where applicable. A same_object link requires one shared "
                    "physical occurrence. Never invent geometry, entities, facts or measurements. "
                    "Use only each provider's advertised relationships and fact authorities; report gaps.\n"
                    + canonical_json({"product": bundle.model_dump(mode="json"), "registry": registry.catalog()}), CompositionDraft))
                bundle.manifest.links = draft.links
                unresolved.extend(draft.unresolved)
            except Exception as exc:
                unresolved.append(f"Composition authoring failed: {exc}")
    validation = validate_product(bundle, registry=registry)
    domains = {r.domain for r in bundle.manifest.artifacts}
    if {"chemical", "process"} <= domains and not any(l.kind == "uses_reaction" for l in bundle.manifest.links):
        unresolved.append("Chemical/process composition has no explicit operation-to-reaction relationship.")
    evaluations: list[dict[str, Any]] = []
    for reference in bundle.manifest.artifacts:
        metadata = registry.metadata(reference.domain)
        adapter = registry.provider(reference.domain)
        results: list[dict[str, Any]]
        try:
            if any(f.severity == "error" for f in validation.local.get(reference.artifact_id, [])) or any(
                f.location == reference.artifact_id for f in validation.composition
            ):
                results = [{"status": "unknown", "message": "Artifact integrity/local validation blocks evaluation."}]
            elif metadata and metadata.evaluation and adapter is not None:
                payload = bundle.artifacts.get(reference.artifact_id)
                if payload is None and metadata.resolution:
                    payload = adapter.resolve(reference.model_copy(deep=True))
                if payload is None or fingerprint(payload) != reference.sha256:
                    raise ValueError("Evaluator input is unavailable or differs from the pinned digest.")
                results = adapter.evaluate(deepcopy(payload), reference.model_copy(deep=True))
            else:
                results = [{"status": "unsupported", "message": "No quantitative evaluator advertised for this domain."}]
        except Exception as exc:
            results = [{"status": "unknown", "message": f"Evaluation failed: {exc}"}]
        if any(item.get("status") in {"fail", "unknown"} for item in results):
            unresolved.append(f"{reference.domain}: one or more evaluations failed or remain unknown.")
        evaluations.append({"input": reference.model_dump(mode="json"),
                            "provider_version": metadata.provider_version if metadata else None,
                            "results": results, "output_sha256": fingerprint(results)})
    for task in bundle.plan.tasks:
        unresolved.extend(f"{task.domain}: {item}" for item in (
            task.missing_inputs + task.unresolved_dependencies + task.errors))
    failed = any(t.status in {"failed", "invalid"} for t in bundle.plan.tasks)
    status = "failed" if failed and not bundle.manifest.artifacts else (
        "partial" if unresolved or not validation.is_valid else "succeeded")
    return EngineeringResult(
        product=bundle, validation={**validation.model_dump(mode="json"), "is_valid": validation.is_valid},
        evaluations=evaluations, assumptions=assumptions, unresolved=list(dict.fromkeys(unresolved)), status=status,
        provenance={"request_sha256": fingerprint(project), "registry_sha256": selection["registry_sha256"],
                    "selection": decision.model_dump(mode="json"), "created_at": datetime.now(timezone.utc).isoformat(),
                    "artifact_sha256": fingerprint(bundle), "parent_sha256": fingerprint(request.existing) if request.existing else None,
                    "author": "caller" if request.designs is not None else type(provider).__name__,
                    "scope": "Design representation and declared checks; no experimental or physical verification."},
    )

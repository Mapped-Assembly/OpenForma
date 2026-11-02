"""Independent authoring calls, with explicit projections for cross-domain inputs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from pydantic import BaseModel, Field

from .base import Domain, DomainModel, Text, fingerprint, unique
from .composition import ArtifactReference, DomainLink, LinkEndpoint, ProductBundle, compose_product, validate_product
from .planning import ProductPlan, context_errors
from .providers import DomainRegistry, NATIVE_PROVIDER
from .registry import domain_model, entities, get_registry, parse_domain, validate_domain


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
                "revision": request.revision, "domain": request.domain, "schema_version": "1.0"}
    return author

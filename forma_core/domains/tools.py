"""Shared typed operations for the local CLI and authenticated MCP surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Domain, DomainModel, Finding
from .compatibility import export_hardware_ir, import_hardware_ir
from .composition import ArtifactReference, DomainLink, ProductBundle, compose_product, validate_product
from .generation import DeclaredInput, fixture_author, generate_product
from .planning import ProductPlan, ProductScope, select_domains
from .providers import DomainRegistry, NATIVE_PROVIDER
from .registry import domain_model, get_registry, parse_domain, validate_domain
from .selection import SelectionDecision, SelectionProject, apply_selection, prepare_selection


class PlanInput(SelectionProject):
    """Use the calling agent by default; an internal model is an explicit opt-in."""

    scope: ProductScope | None = None
    decision: SelectionDecision | None = None
    selection_mode: Literal["external", "provider"] = "external"
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def unambiguous_selection(self) -> PlanInput:
        """Never ignore a supplied decision or invoke a model implicitly."""
        if self.scope is not None and self.decision is not None:
            raise ValueError("Supply either scope or decision, not both.")
        if self.selection_mode == "provider":
            if self.scope is not None or self.decision is not None:
                raise ValueError("Provider selection cannot be combined with scope or decision.")
        elif self.provider is not None or self.model is not None:
            raise ValueError("Set selection_mode='provider' to use provider/model options.")
        return self


class GenerateInput(DomainModel):
    """Compile separately authored domain designs into a portable product."""

    plan: ProductPlan
    designs: dict[Domain, dict[str, Any]]
    existing: ProductBundle | None = None
    inputs: list[DeclaredInput] = Field(default_factory=list)
    links: list[DomainLink] | None = None
    update_domains: set[Domain] | None = None
    references: list[ArtifactReference] = Field(default_factory=list)


class ComposeInput(DomainModel):
    """Compose native documents and external references without absorbing schemas."""

    product_id: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    references: list[ArtifactReference] = Field(default_factory=list)
    links: list[DomainLink] = Field(default_factory=list)
    plan: ProductPlan | None = None


class ArtifactInput(DomainModel):
    """An external provider's standalone payload plus its explicit identity contract."""

    reference: ArtifactReference
    artifact: dict[str, Any]


class ImportInput(DomainModel):
    """Legacy import with an explicit stable product identity."""

    product_id: str
    hardware_ir: dict[str, Any]


class ExportInput(DomainModel):
    """Compatibility export with an explicit acknowledgement of representation loss."""

    product: ProductBundle
    legacy_seed: dict[str, Any] | None = None
    allow_lossy: bool = False


Operation = Literal["plan", "generate", "compose", "registry", "validate", "validate-domain", "import-hardware", "export-hardware", "schema"]


def call_domain_tool(operation: Operation, payload: dict[str, Any], *, registry: DomainRegistry | None = None) -> dict[str, Any]:
    """Execute a stateless operation; no hosted revision or file is implicitly changed."""
    registry = get_registry(registry)
    if operation == "registry":
        if payload:
            raise ValueError("Registry discovery accepts an empty object; registration is application configuration.")
        return {"domains": registry.catalog()}
    if operation == "compose":
        request = ComposeInput.model_validate(payload)
        return compose_product(request.product_id, [parse_domain(d) for d in request.documents],
            references=request.references, links=request.links, plan=request.plan).model_dump(mode="json")
    if operation == "schema":
        if set(payload) != {"domain"}:
            raise ValueError("Schema input must contain only domain.")
        domain = payload["domain"]
        metadata = registry.metadata(domain)
        if metadata is None:
            raise ValueError(f"Unsupported domain '{domain}'.")
        if metadata.provider_id != NATIVE_PROVIDER:
            return {"status": "external_provider", "provider": metadata.model_dump(mode="json"),
                    "document": None, "context": None}
        return {"document": domain_model(domain).model_json_schema(),
                "context": domain_model(domain, context=True).model_json_schema()}
    if operation == "plan":
        request = PlanInput.model_validate(payload)
        if request.selection_mode == "provider":
            from .agents import configured_selector, plan_with_agent
            return plan_with_agent(configured_selector(request.provider, request.model), request.product_id,
                                   request.brief, contexts=request.contexts, registry=registry).model_dump(mode="json")
        project = SelectionProject(product_id=request.product_id, brief=request.brief, contexts=request.contexts)
        if request.decision is not None:
            return apply_selection(project, request.decision, registry=registry).model_dump(mode="json")
        if request.scope is None:
            return prepare_selection(project, registry=registry)
        return select_domains(request.product_id, request.brief, scope=request.scope,
                              contexts=request.contexts, registry=registry).model_dump(mode="json")
    if operation == "generate":
        request = GenerateInput.model_validate(payload)
        for domain in request.designs:
            metadata = registry.metadata(domain)
            if metadata is None or metadata.provider_id != NATIVE_PROVIDER:
                raise ValueError("External designs must be supplied as pinned references, not native design fields.")
        return generate_product(request.plan, fixture_author(request.designs), existing=request.existing,
                                inputs=request.inputs, links=request.links,
                                update_domains=request.update_domains, references=request.references,
                                registry=registry).model_dump(mode="json")
    if operation == "validate":
        report = validate_product(ProductBundle.model_validate(payload), registry=registry)
        return {**report.model_dump(mode="json"), "is_valid": report.is_valid}
    if operation == "validate-domain":
        if "reference" in payload or "artifact" in payload:
            request = ArtifactInput.model_validate(payload)
            unresolved = registry.reference_errors(request.reference)
            if unresolved:
                return {"is_valid": None, "status": "unresolved", "findings": [],
                        "dependencies": [f.model_dump(mode="json") for f in unresolved]}
            try:
                findings = registry.validate_artifact(request.artifact, request.reference)
            except Exception as exc:
                return {"is_valid": None, "status": "unresolved", "findings": [],
                        "dependencies": [Finding(code="provider_validation", location=request.reference.artifact_id,
                                                  message=f"Provider validation unavailable: {exc}").model_dump(mode="json")]}
            return {"is_valid": not any(f.severity == "error" for f in findings),
                    "findings": [f.model_dump(mode="json") for f in findings]}
        findings = validate_domain(payload)
        return {"is_valid": not any(f.severity == "error" for f in findings),
                "findings": [f.model_dump(mode="json") for f in findings]}
    if operation == "import-hardware":
        request = ImportInput.model_validate(payload)
        return import_hardware_ir(request.hardware_ir, product_id=request.product_id).model_dump(mode="json")
    if operation == "export-hardware":
        request = ExportInput.model_validate(payload)
        return export_hardware_ir(request.product, legacy_seed=request.legacy_seed,
                                  allow_lossy=request.allow_lossy, registry=registry).model_dump(mode="json")
    raise ValueError(f"Unknown domain operation '{operation}'.")

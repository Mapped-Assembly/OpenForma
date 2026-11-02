"""Shared typed operations for the local CLI and authenticated MCP surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import Domain, DomainModel
from .compatibility import export_hardware_ir, import_hardware_ir
from .composition import DomainLink, ProductBundle, validate_product
from .generation import DeclaredInput, fixture_author, generate_product
from .planning import ProductPlan, ProductScope, select_domains
from .registry import domain_model, validate_domain


class PlanInput(DomainModel):
    """Agent-interpreted scope plus contexts, usable entirely offline."""

    product_id: str
    brief: str
    scope: ProductScope
    contexts: dict[Domain, dict[str, Any]] = Field(default_factory=dict)


class GenerateInput(DomainModel):
    """Compile separately authored domain designs into a portable product."""

    plan: ProductPlan
    designs: dict[Domain, dict[str, Any]]
    existing: ProductBundle | None = None
    inputs: list[DeclaredInput] = Field(default_factory=list)
    links: list[DomainLink] | None = None
    update_domains: set[Domain] | None = None


class ImportInput(DomainModel):
    """Legacy import with an explicit stable product identity."""

    product_id: str
    hardware_ir: dict[str, Any]


class ExportInput(DomainModel):
    """Compatibility export with an explicit acknowledgement of representation loss."""

    product: ProductBundle
    legacy_seed: dict[str, Any] | None = None
    allow_lossy: bool = False


Operation = Literal["plan", "generate", "validate", "validate-domain", "import-hardware", "export-hardware", "schema"]


def call_domain_tool(operation: Operation, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a stateless operation; no hosted revision or file is implicitly changed."""
    if operation == "schema":
        if set(payload) != {"domain"}:
            raise ValueError("Schema input must contain only domain.")
        domain = payload["domain"]
        return {"document": domain_model(domain).model_json_schema(),
                "context": domain_model(domain, context=True).model_json_schema()}
    if operation == "plan":
        request = PlanInput.model_validate(payload)
        return select_domains(request.product_id, request.brief, scope=request.scope,
                              contexts=request.contexts).model_dump(mode="json")
    if operation == "generate":
        request = GenerateInput.model_validate(payload)
        return generate_product(request.plan, fixture_author(request.designs), existing=request.existing,
                                inputs=request.inputs, links=request.links,
                                update_domains=request.update_domains).model_dump(mode="json")
    if operation == "validate":
        report = validate_product(ProductBundle.model_validate(payload))
        return {**report.model_dump(mode="json"), "is_valid": report.is_valid}
    if operation == "validate-domain":
        findings = validate_domain(payload)
        return {"is_valid": not any(f.severity == "error" for f in findings),
                "findings": [f.model_dump(mode="json") for f in findings]}
    if operation == "import-hardware":
        request = ImportInput.model_validate(payload)
        return import_hardware_ir(request.hardware_ir, product_id=request.product_id).model_dump(mode="json")
    if operation == "export-hardware":
        request = ExportInput.model_validate(payload)
        return export_hardware_ir(request.product, legacy_seed=request.legacy_seed,
                                  allow_lossy=request.allow_lossy).model_dump(mode="json")
    raise ValueError(f"Unknown domain operation '{operation}'.")

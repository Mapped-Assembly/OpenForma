"""Lazy domain discovery; asking for one schema never loads all the others."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import Domain, Finding, canonical_json

CONTRACTS: dict[Domain, tuple[str, str, str]] = {
    "mechanical": ("forma_core.mechanics.models", "MechanicalIR", "MechanicalContext"),
    "electrical": ("forma_core.electronics.models", "ElectricalIR", "ElectricalContext"),
    "biological": ("forma_core.biology.ir", "BiologicalIR", "BiologicalContext"),
    "fluidic": ("forma_core.fluidics.ir", "FluidicIR", "FluidicContext"),
}

# A field has one domain authority even when a physical object has several views.
FACT_OWNERS: dict[str, Domain] = {
    "geometry": "mechanical", "dimensions": "mechanical", "material": "mechanical",
    "placement": "mechanical", "motion": "mechanical",
    "pins": "electrical", "connectivity": "electrical", "power": "electrical",
    "experiment": "biological", "observations": "biological",
    "flow": "fluidic", "fluid_network": "fluidic",
}


def domain_model(domain: Domain, *, context: bool = False) -> type[BaseModel]:
    """Load only the requested document or context schema."""
    if domain not in CONTRACTS:
        raise ValueError(f"Unsupported domain '{domain}'.")
    module, document_name, context_name = CONTRACTS[domain]
    return getattr(import_module(module), context_name if context else document_name)


def parse_domain(payload: dict[str, Any]) -> BaseModel:
    """Parse a versioned standalone domain document, rejecting unknown versions."""
    domain = payload.get("domain")
    document = domain_model(domain).model_validate(payload)
    # Some reused legacy value types ignore extra fields. New domain documents
    # must not silently lose those fields even though legacy parsing still can.
    def check_keys(raw: Any, parsed: Any, path: str) -> None:
        """Reject ignored nested fields after parsing reused compatibility types."""
        if isinstance(raw, dict) and isinstance(parsed, dict):
            for key, value in raw.items():
                if key not in parsed:
                    raise ValueError(f"Unsupported field '{path}{key}'.")
                check_keys(value, parsed[key], f"{path}{key}.")
        elif isinstance(raw, list) and isinstance(parsed, list):
            for index, (left, right) in enumerate(zip(raw, parsed)):
                check_keys(left, right, f"{path}{index}.")
    check_keys(payload, document.model_dump(mode="json"), "")
    canonical_json(document)  # Also checks finite values inside reused legacy leaf types.
    return document


def validate_domain(payload: dict[str, Any] | BaseModel) -> list[Finding]:
    """Reparse mutable state and run local rules, never cross-domain validation."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    try:
        document = parse_domain(payload)
    except ValidationError as exc:
        return [Finding(code="schema", location=".".join(map(str, e["loc"])), message=e["msg"])
                for e in exc.errors()]
    except ValueError as exc:
        return [Finding(code="schema", location="domain", message=str(exc))]
    domain = payload["domain"]
    if domain == "fluidic":
        from forma_core.fluidics.validation import validate_fluidic_system
        return [Finding(code=i.code, location=i.entity_id, message=i.message)
                for i in validate_fluidic_system(document.system)]
    if domain == "electrical":
        from forma_core.electronics.rules import validate_circuit
        parts = {p.part_definition_id: p for p in document.parts}
        components = [SimpleNamespace(
            ref_des=c.ref_des, pins=parts[c.part_definition_id].pins,
            name=parts[c.part_definition_id].name, part_number=parts[c.part_definition_id].part_number,
            category=parts[c.part_definition_id].category, rationale="",
        ) for c in document.components]
        return [Finding(code=i.category, location="electrical", message=i.description,
                        severity="error" if i.severity == "CRITICAL" else "warning")
                for i in validate_circuit(components, document.nets)]
    return []


def entities(document: BaseModel) -> dict[str, set[str]]:
    """Map local object IDs to declared port IDs for composition checks."""
    domain = document.domain
    if domain in {"mechanical", "electrical"}:
        parts = {p.part_definition_id: p for p in document.parts}
        return {c.object_id: ({p.pin_id for p in parts[c.part_definition_id].pins}
                             if domain == "electrical" else set()) for c in document.components}
    if domain == "fluidic":
        return {e.id: set(getattr(e, "inlet_ids", []) + getattr(e, "outlet_ids", []))
                for field in type(document.system).model_fields
                for e in getattr(document.system, field)}
    return {e.id: set() for field in (
        "compartments", "exposures", "controls", "endpoints", "sampling_points",
        "datasets", "observations", "reference_outcomes",
    ) for e in getattr(document.experiment, field)}

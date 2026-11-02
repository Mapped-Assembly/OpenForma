"""Lazy domain discovery; asking for one schema never loads all the others."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import Domain, Finding, canonical_json
from .providers import (
    ArtifactIdentity, ArtifactReference, ContextRequirement, DomainRegistry,
    NATIVE_PROVIDER, ProviderMetadata, SchemaSupport,
)

CONTRACTS: dict[Domain, tuple[str, str, str]] = {
    "mechanical": ("forma_core.mechanics.models", "MechanicalIR", "MechanicalContext"),
    "electrical": ("forma_core.electronics.models", "ElectricalIR", "ElectricalContext"),
    "biological": ("forma_core.biology.ir", "BiologicalIR", "BiologicalContext"),
    "fluidic": ("forma_core.fluidics.ir", "FluidicIR", "FluidicContext"),
    "chemical": ("forma_core.chemistry.models", "ChemicalIR", "ChemicalContext"),
    "process": ("forma_core.processes.models", "ProcessIR", "ProcessContext"),
}

# A field has one domain authority even when a physical object has several views.
FACT_OWNERS: dict[str, Domain] = {
    "geometry": "mechanical", "dimensions": "mechanical", "material": "mechanical",
    "placement": "mechanical", "motion": "mechanical",
    "pins": "electrical", "connectivity": "electrical", "power": "electrical",
    "experiment": "biological", "observations": "biological",
    "flow": "fluidic", "fluid_network": "fluidic",
    "chemical.species": "chemical", "chemical.reaction": "chemical",
    "chemical.composition": "chemical",
    "process.topology": "process", "process.stream": "process",
    "process.operation": "process",
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
    if domain == "chemical":
        from forma_core.chemistry.validation import validate_chemical
        return validate_chemical(document)
    if domain == "process":
        from forma_core.processes.validation import validate_process
        return validate_process(document)
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
    if domain == "process":
        return {operation.operation_id: {port.port_id for port in operation.ports}
                for operation in document.operations} | {
            stream.stream_id: set() for stream in document.streams
        }
    if domain == "chemical":
        return {item.species_id: set() for item in document.species} | {
            item.mixture_id: set() for item in document.mixtures
        } | {item.reaction_id: set() for item in document.reactions} | {
            item.condition_id: set() for item in document.conditions
        } | {item.model_id: set() for item in document.models}
    if domain == "fluidic":
        return {e.id: set(getattr(e, "inlet_ids", []) + getattr(e, "outlet_ids", []))
                for field in type(document.system).model_fields
                for e in getattr(document.system, field)}
    return {e.id: set() for field in (
        "compartments", "exposures", "controls", "endpoints", "sampling_points",
        "datasets", "observations", "reference_outcomes",
    ) for e in getattr(document.experiment, field)}


class NativeProvider:
    """Lazy adapter for existing native schemas; authoring stays in generation.py."""

    def __init__(self, metadata: ProviderMetadata) -> None:
        """Retain only metadata until a caller requests a native operation."""
        self.metadata = metadata

    def validate_context(self, context: dict[str, Any]) -> list[str]:
        """Delegate detailed context validation to the existing native contract."""
        try:
            domain_model(self.metadata.domain, context=True).model_validate(context)
        except ValidationError as exc:
            return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return []

    def validate(self, payload: dict[str, Any], reference: ArtifactReference) -> list[Finding]:
        """Run the existing local rules without composition or external providers."""
        return validate_domain(payload)

    def describe(self, payload: dict[str, Any], reference: ArtifactReference) -> ArtifactIdentity:
        """Read native envelope metadata without changing its wire format."""
        return ArtifactIdentity(artifact_id=payload["artifact_id"], domain=payload["domain"],
                                schema_id=f"openforma.{payload['domain']}",
                                schema_version=payload["schema_version"], revision=payload["revision"])

    def entities(self, payload: dict[str, Any], reference: ArtifactReference) -> dict[str, set[str]]:
        """Expose native entity/port identities through the generic provider API."""
        return entities(parse_domain(payload))

    def resolve(self, reference: ArtifactReference) -> dict[str, Any] | None:
        """Native bundle payloads are supplied by callers, not fetched from URLs."""
        return None

    def discover(self, query: dict[str, Any]) -> list[ArtifactReference]:
        """No native artifact catalog service is implemented in this adapter."""
        raise NotImplementedError("Native artifact discovery is not provided.")

    def author(self, request: dict[str, Any]) -> ArtifactReference:
        """Native authoring remains the existing caller-supplied generation API."""
        raise NotImplementedError("Use generate_product with a caller-supplied native author.")


def create_registry() -> DomainRegistry:
    """Register native capabilities and reserve SpaceIR without importing its schema."""
    registry = DomainRegistry()
    scopes = {"mechanical": ["physical_structure", "mechanical_motion"],
              "electrical": ["wiring", "electrical_control"],
              "biological": ["biological_experiment"], "fluidic": ["fluid_network"],
              "chemical": [], "process": []}
    required = {"mechanical": ["scope", "geometric_requirements", "loads", "manufacturing_constraints"],
                "electrical": ["scope", "supply_requirements", "load_requirements", "interface_requirements"],
                "biological": ["scope", "biological_question", "experimental_conditions", "evidence_sources"],
                "fluidic": ["scope", "fluid_properties", "operating_conditions", "flow_requirements"],
                "chemical": ["scope", "chemical_requirements", "evidence_requirements"],
                "process": ["scope", "process_requirements", "stream_requirements", "evidence_requirements"]}
    for domain, (_, name, _) in CONTRACTS.items():
        metadata = ProviderMetadata(domain=domain, ir_type=name, owner="OpenForma",
            provider_id=NATIVE_PROVIDER, provider_version="1.0",
            schemas=[SchemaSupport(schema_id=f"openforma.{domain}", versions=["1.0"])],
            validation=True, any_scope=scopes[domain],
            required_context=[ContextRequirement(name=field, description=f"Native {domain} context: {field}.") for field in required[domain]],
            reference_types=(["uses_species", "uses_reaction"] if domain == "chemical"
                             else ["uses_species", "uses_reaction", "spatial", "same_object"] if domain == "process"
                             else ["same_object", "spatial", "hosted_by"]),
            fact_authorities=[fact for fact, owner in FACT_OWNERS.items() if owner == domain],
            limitations=["Native authoring uses generate_product with caller-supplied designs; no provider-hosted author or resolver."])
        registry.register(metadata, NativeProvider(metadata))
    registry.register(ProviderMetadata(domain="space", ir_type="SpaceIR", owner="OpenIndustries",
        provider_id="openindustries.space", provider_version="unregistered", availability="unavailable",
        limitations=["Reserved external domain; no SpaceIR schema or implementation is supplied by OpenForma.",
                     "Schema identifiers, versions and required context will be advertised by the owning provider."]))
    return registry


DEFAULT_REGISTRY = create_registry()


def get_registry(registry: DomainRegistry | None = None) -> DomainRegistry:
    """Use an injected registry or the process's explicitly configured defaults."""
    return registry if registry is not None else DEFAULT_REGISTRY

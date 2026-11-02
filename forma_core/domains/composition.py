"""Reference-only product manifests and independently diagnosed cross-domain links."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from .base import Domain, DomainModel, Finding, Text, fingerprint, unique
from .planning import ProductPlan
from .providers import ArtifactIdentity, ArtifactReference, Digest, DomainRegistry, NATIVE_PROVIDER, Revision
from .registry import entities, get_registry, parse_domain
from copy import deepcopy


class LinkEndpoint(DomainModel):
    """An object/port in a pinned artifact revision, with explicit spatial context."""

    artifact_id: Text
    revision: Revision | None = None
    object_id: Text
    port_id: Text | None = None
    units: Text | None = None
    frame: Text | None = None
    sha256: Digest | None = None

    @model_validator(mode="after")
    def pinned_identity(self) -> LinkEndpoint:
        """Endpoints must pin a revision or digest, including content-addressed artifacts."""
        if self.revision is None and self.sha256 is None:
            raise ValueError("Link endpoints require a revision or content fingerprint.")
        return self


class DomainLink(DomainModel):
    """A separately validated relationship; it does not change local domain validity."""

    link_id: Text
    kind: Text
    source: LinkEndpoint
    target: LinkEndpoint
    fact_owners: dict[str, Domain] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ProductManifest(DomainModel):
    """Composition owns references, never embedded mechanical/electrical semantics."""

    schema_version: Literal["1.0"] = "1.0"
    product_id: Text
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    links: list[DomainLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> ProductManifest:
        """Reject duplicate artifact and relationship identities."""
        unique([a.artifact_id for a in self.artifacts], "artifact ID")
        unique([a.domain for a in self.artifacts], "artifact domain")
        unique([link.link_id for link in self.links], "link ID")
        return self


class CompatibilityRecord(DomainModel):
    """Preserved legacy source and revision fingerprints, outside canonical domains.

    The source is authoritative only for fields not owned by a domain. Baseline
    hashes let exporters distinguish unchanged legacy data from authored edits.
    """

    source: dict[str, Any]
    source_sha256: str
    domain_baselines: dict[str, str] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)


class ProductBundle(DomainModel):
    """Portable persistence envelope containing only the artifacts that exist."""

    format: Literal["forma.product-domains"] = "forma.product-domains"
    manifest: ProductManifest
    artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    plan: ProductPlan | None = None
    compatibility: CompatibilityRecord | None = None


class ProductValidation(DomainModel):
    """Local, composition and planning findings remain inspectable independently."""

    local: dict[str, list[Finding]] = Field(default_factory=dict)
    composition: list[Finding] = Field(default_factory=list)
    blocked_domains: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        """True only when selected work is complete and no blocking finding exists."""
        return not self.blocked_domains and not any(
            f.severity == "error" for findings in [*self.local.values(), self.composition] for f in findings
        )


def compose_product(
    product_id: str,
    documents: list[BaseModel],
    *,
    links: list[DomainLink] | None = None,
    plan: ProductPlan | None = None,
    references: list[ArtifactReference] | None = None,
) -> ProductBundle:
    """Create exact references without placeholders for omitted/blocked domains."""
    payloads = [document.model_dump(mode="json") for document in documents]
    refs = [ArtifactReference(
        artifact_id=p["artifact_id"], domain=p["domain"], schema_version=p["schema_version"],
        revision=p["revision"], sha256=fingerprint(p),
    ) for p in payloads]
    return ProductBundle(manifest=ProductManifest(product_id=product_id, artifacts=refs + (references or []), links=links or []),
                         artifacts={p["artifact_id"]: p for p in payloads}, plan=plan)


def validate_product(bundle: ProductBundle, *, registry: DomainRegistry | None = None) -> ProductValidation:
    """Check artifact integrity and links without turning link errors into local errors."""
    result = ProductValidation()
    registry = get_registry(registry)
    documents: dict[str, BaseModel] = {}
    identities: dict[str, ArtifactIdentity] = {}
    indexes: dict[str, dict[str, set[str]]] = {}
    references = {ref.artifact_id: ref for ref in bundle.manifest.artifacts}

    def add(code: str, location: str, message: str) -> None:
        """Append a composition finding while preserving all independent checks."""
        result.composition.append(Finding(code=code, location=location, message=message))

    expected = {ref.artifact_id for ref in bundle.manifest.artifacts}
    for extra in sorted(bundle.artifacts.keys() - expected):
        add("unreferenced_artifact", extra, "Artifact is absent from the composition manifest.")
    for ref in bundle.manifest.artifacts:
        provider_errors = registry.reference_errors(ref)
        if provider_errors:
            result.composition.extend(provider_errors)
            continue
        provider = registry.provider(ref.domain)
        metadata = registry.metadata(ref.domain)
        assert provider is not None and metadata is not None
        payload = bundle.artifacts.get(ref.artifact_id)
        if payload is None and metadata.resolution:
            try:
                payload = provider.resolve(ref.model_copy(deep=True))
            except Exception as exc:
                add("provider_resolution", ref.artifact_id, f"Artifact resolution failed: {exc}")
                continue
        if payload is None:
            add("missing_artifact", ref.artifact_id, "Referenced artifact is unavailable.")
            continue
        payload = deepcopy(payload)
        try:
            if fingerprint(payload) != ref.sha256:
                add("artifact_hash", ref.artifact_id, "Artifact content differs from the pinned revision digest.")
            result.local[ref.artifact_id] = registry.validate_artifact(payload, ref)
            if any(f.severity == "error" for f in result.local[ref.artifact_id]):
                continue
            identity = ArtifactIdentity.model_validate(provider.describe(deepcopy(payload), ref.model_copy(deep=True)))
            mismatch = False
            for key in type(identity).model_fields:
                if getattr(identity, key) != getattr(ref, key):
                    mismatch = True
                    add("artifact_metadata", ref.artifact_id, f"Artifact {key} differs from its manifest reference.")
            if mismatch:
                continue
            index = TypeAdapter(dict[str, set[Text]]).validate_python(provider.entities(deepcopy(payload), ref.model_copy(deep=True)))
            identities[ref.artifact_id] = identity
            indexes[ref.artifact_id] = index
            if ref.provider_id == NATIVE_PROVIDER:
                documents[ref.artifact_id] = parse_domain(payload)
        except Exception as exc:
            add("provider_validation", ref.artifact_id, f"Provider could not complete validation/indexing: {exc}")
            continue
    if bundle.plan is not None:
        if bundle.plan.product_id != bundle.manifest.product_id:
            add("plan_product", "plan", "Plan and composition product identities differ.")
        selected = {task.domain for task in bundle.plan.tasks}
        actual = {ref.domain for ref in bundle.manifest.artifacts}
        for domain in sorted(actual - selected):
            add("unselected_domain", domain, "Artifact was not selected by the product plan.")
        for task in bundle.plan.tasks:
            if task.domain not in actual or task.status != "generated":
                result.blocked_domains[task.domain] = task.missing_inputs or task.unresolved_dependencies or task.errors or ["Domain has not been generated."]
            for dependency in task.dependencies:
                if dependency.required and (dependency.domain not in actual or registry.provider(dependency.domain) is None):
                    add("unresolved_dependency", task.domain, f"Required domain '{dependency.domain}' ({dependency.relationship}) is unresolved.")
    for link in bundle.manifest.links:
        resolved = True
        for endpoint in (link.source, link.target):
            doc = identities.get(endpoint.artifact_id)
            if doc is None:
                add("link_artifact", link.link_id, f"Unavailable artifact '{endpoint.artifact_id}'.")
                resolved = False
                continue
            if endpoint.revision is not None and doc.revision != endpoint.revision:
                add("link_revision", link.link_id, "Linked artifact revision changed; recheck this relationship.")
            if endpoint.sha256 is not None and endpoint.sha256 != references[endpoint.artifact_id].sha256:
                add("link_hash", link.link_id, "Endpoint digest differs from the pinned artifact reference.")
            ports = indexes[endpoint.artifact_id].get(endpoint.object_id)
            if ports is None:
                add("link_object", link.link_id, f"Unknown object '{endpoint.object_id}'.")
                resolved = False
            elif endpoint.port_id is not None and endpoint.port_id not in ports:
                add("link_port", link.link_id, f"Unknown port '{endpoint.port_id}'.")
        if not resolved:
            continue
        source = identities[link.source.artifact_id]
        target = identities[link.target.artifact_id]
        if source.domain == target.domain:
            add("link_domain", link.link_id, "A cross-domain link must reference distinct domains.")
        for fact, owner in link.fact_owners.items():
            metadata = registry.metadata(owner)
            if metadata is None or fact not in metadata.fact_authorities or owner not in {source.domain, target.domain}:
                add("fact_ownership", link.link_id, f"Unsupported or conflicting authority for '{fact}'.")
        for domain in (source.domain, target.domain):
            metadata = registry.metadata(domain)
            if metadata is None or link.kind not in metadata.reference_types:
                add("relationship_type", link.link_id, f"Provider for '{domain}' does not advertise '{link.kind}'.")
        if link.kind in {"uses_species", "uses_reaction"}:
            chemical_endpoints = [endpoint for endpoint in (link.source, link.target)
                                  if identities[endpoint.artifact_id].domain == "chemical"]
            if len(chemical_endpoints) != 1:
                add("chemical_link_domain", link.link_id, "Chemical links require exactly one ChemicalIR endpoint.")
            else:
                endpoint = chemical_endpoints[0]
                chemical = documents.get(endpoint.artifact_id)
                if chemical is not None:
                    expected = ({item.species_id for item in chemical.species} if link.kind == "uses_species"
                                else {item.reaction_id for item in chemical.reactions})
                    if endpoint.object_id not in expected:
                        add("chemical_link_type", link.link_id,
                            f"{link.kind} must reference a chemical {'species' if link.kind == 'uses_species' else 'reaction'} entity.")
        if link.kind in {"uses_equipment", "uses_electrical_port", "uses_fluidic_port",
                         "uses_thermal_interface"}:
            target_domain = {
                "uses_equipment": "mechanical",
                "uses_electrical_port": "electrical",
                "uses_fluidic_port": "fluidic",
                "uses_thermal_interface": "thermal",
            }[link.kind]
            if source.domain != "process" or target.domain != target_domain:
                add("process_link_domain", link.link_id,
                    f"{link.kind} must run from ProcessIR to {target_domain}.")
            else:
                process = documents.get(link.source.artifact_id)
                if process is not None:
                    operation = next((item for item in process.operations
                                      if item.operation_id == link.source.object_id), None)
                    if operation is None:
                        add("process_link_operation", link.link_id,
                            "An equipment/utility binding must originate at a process operation.")
                    else:
                        port = next((item for item in operation.ports
                                     if item.port_id == link.source.port_id), None)
                        if link.kind == "uses_equipment" and link.source.port_id is not None:
                            add("process_link_port", link.link_id,
                                "A mechanical equipment binding targets a whole operation, not one port.")
                        if link.kind in {"uses_electrical_port", "uses_thermal_interface"} and (
                                port is None or port.kind != "energy"):
                            add("process_link_port", link.link_id,
                                "Electrical/thermal interfaces must originate at an energy port.")
                        if link.kind == "uses_fluidic_port" and (
                                port is None or port.kind != "material"):
                            add("process_link_port", link.link_id,
                                "Fluidic interfaces must originate at a material port.")
                if link.kind in {"uses_electrical_port", "uses_fluidic_port", "uses_thermal_interface"} and (
                        link.target.port_id is None):
                    add("process_link_port", link.link_id,
                        "The referenced external interface must have an explicit typed port.")
        if link.kind == "same_object":
            if link.source.object_id != link.target.object_id:
                add("physical_identity", link.link_id, "Shared physical views must preserve the same object_id.")
            if source.domain in {"mechanical", "electrical"} and target.domain in {"mechanical", "electrical"} and source.artifact_id in documents and target.artifact_id in documents:
                left = next(c for c in documents[source.artifact_id].components if c.object_id == link.source.object_id)
                right = next(c for c in documents[target.artifact_id].components if c.object_id == link.target.object_id)
                if left.part_definition_id != right.part_definition_id:
                    add("part_identity", link.link_id, "Shared object views reference different part definitions.")
        if link.kind == "spatial":
            if any(e.units not in {"mm", "m"} or not e.frame for e in (link.source, link.target)):
                add("spatial_context", link.link_id, "Spatial links require declared mm/m units and named frames.")
            elif link.source.units != link.target.units or link.source.frame != link.target.frame:
                add("spatial_transform", link.link_id, "Different units/frames require an external transform; no implicit conversion.")
        if link.kind == "hosted_by":
            if source.domain != "biological" or target.domain != "fluidic" or source.artifact_id not in documents or target.artifact_id not in documents:
                add("host_domains", link.link_id, "Hosted-by links run from a biological compartment to a fluidic host.")
            else:
                compartments = {c.id for c in documents[source.artifact_id].experiment.compartments}
                system = documents[target.artifact_id].system
                hosts = {c.id for c in system.channels + system.chambers}
                if link.source.object_id not in compartments or link.target.object_id not in hosts:
                    add("host_type", link.link_id, "Expected a biological compartment and fluidic channel/chamber.")
    # Existing BiologicalExperiment host IDs are cross-domain, never local failures.
    for artifact_id, doc in documents.items():
        if doc.domain != "biological":
            continue
        for compartment in doc.experiment.compartments:
            for host in compartment.fluidic_component_ids:
                if not any(link.kind == "hosted_by" and link.source.artifact_id == artifact_id
                           and link.source.object_id == compartment.id and link.target.object_id == host
                           for link in bundle.manifest.links):
                    add("host_link_missing", compartment.id, f"Declare a revision-pinned link to fluidic host '{host}'.")
    physical = [(key, doc) for key, doc in documents.items() if doc.domain in {"mechanical", "electrical"}]
    for index, (left_id, left) in enumerate(physical):
        for right_id, right in physical[index + 1:]:
            for object_id in sorted(entities(left).keys() & entities(right).keys()):
                if not any(link.kind == "same_object" and link.source.object_id == object_id
                           and link.target.object_id == object_id
                           and {link.source.artifact_id, link.target.artifact_id} == {left_id, right_id}
                           for link in bundle.manifest.links):
                    add("shared_link_missing", object_id, "Shared physical views require an explicit same_object link.")
    return result

"""Explicit, loss-reporting adapters for legacy Hardware IR 0.1/0.2."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import Field

from .base import DomainModel, Finding, fingerprint
from .composition import (
    ArtifactReference, CompatibilityRecord, DomainLink, LinkEndpoint, ProductBundle,
    ProductManifest, validate_product,
)
from .registry import validate_domain


class HardwareExport(DomainModel):
    """A compatibility payload plus every known transformation or representation loss."""

    payload: dict[str, Any] | None = None
    findings: list[Finding] = Field(default_factory=list)


def _ignored_paths(source: Any, normalized: Any, path: str = "") -> list[str]:
    """Locate source fields absent after legacy parsing, including nested extensions."""
    missing: list[str] = []
    if isinstance(source, dict) and isinstance(normalized, dict):
        for key, value in source.items():
            child = f"{path}.{key}" if path else key
            if key not in normalized:
                missing.append(child)
            else:
                missing.extend(_ignored_paths(value, normalized[key], child))
    elif isinstance(source, list) and isinstance(normalized, list):
        for index, (left, right) in enumerate(zip(source, normalized)):
            missing.extend(_ignored_paths(left, right, f"{path}.{index}"))
    return missing


def import_hardware_ir(source: dict[str, Any], *, product_id: str) -> ProductBundle:
    """Split supported facts, preserve the entire source, and report migration limits.

    No mechanical/electrical artifact is invented merely because the legacy
    document had default fields. Raw source extensions survive exact round trips.
    """
    from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

    original = deepcopy(source)
    legacy = HardwareIntermediateRepresentation.model_validate(deepcopy(source))
    normalized = legacy.model_dump(mode="json")
    findings = [Finding(code="preserved_extension", location=path,
                        message="Preserved in the compatibility source; not interpreted by a domain.", severity="warning")
                for path in _ignored_paths(original, normalized)]
    if original.get("hardware_ir_version", "0.1") != "0.2":
        findings.append(Finding(code="legacy_normalization", location="hardware_ir_version", severity="warning",
                                message="Domain views use existing 0.1-to-0.2 instance/BOM normalization; original source retained."))
    payloads: list[dict[str, Any]] = []
    mapping = (legacy.assembly_metadata or {}).get("domain_object_ids", {})
    views = [{"object_id": mapping.get(c.ref_des, c.ref_des), "ref_des": c.ref_des,
              "part_definition_id": c.part_definition_id} for c in legacy.components]
    if legacy.mechanical is not None or legacy.cad_model is not None or any(p.dimensions_mm for p in legacy.part_definitions):
        payloads.append({"artifact_id": f"{product_id}.mechanical", "domain": "mechanical", "schema_version": "1.0",
                         "revision": 1, "parts": [{"part_definition_id": p.part_definition_id,
                             "dimensions_mm": p.dimensions_mm, "material": None, "geometry_refs": []}
                            for p in legacy.part_definitions],
                         "components": views, "mechanical": normalized["mechanical"], "cad_model": normalized["cad_model"]})
    mechanical_categories = {"mechanical", "enclosure", "fastener", "fasteners", "structural", "3d print"}
    electrical_refs = {pin.ref_des for net in legacy.nets for pin in net.pins}
    electrical_refs.update(rail.source_component for rail in legacy.power_rails)
    electrical_refs.update(c.ref_des for c in legacy.components
                           if c.pins or c.category.strip().lower() not in mechanical_categories)
    electrical_parts = {c.part_definition_id for c in legacy.components if c.ref_des in electrical_refs}
    electrical_parts.update(p.part_definition_id for p in legacy.part_definitions if p.pins or p.electrical_specs)
    if electrical_refs or electrical_parts or legacy.nets or legacy.buses or legacy.pin_mappings or legacy.power_rails:
        payloads.append({"artifact_id": f"{product_id}.electrical", "domain": "electrical", "schema_version": "1.0",
                         "revision": 1, "components": [v for v in views if v["ref_des"] in electrical_refs],
                         "parts": [{"part_definition_id": p.part_definition_id, "name": p.name,
                                    "part_number": p.part_number, "category": p.category,
                                    "pins": [pin.model_dump(mode="json") for pin in p.pins],
                                    "electrical_specs": p.electrical_specs}
                                   for p in legacy.part_definitions if p.part_definition_id in electrical_parts],
                         **{key: normalized[key] for key in ("nets", "buses", "pin_mappings", "power_rails")},
                         "estimated_current_draw_ma": original.get("estimated_current_draw_ma")})
    if legacy.fluidic_system is not None:
        payloads.append({"artifact_id": f"{product_id}.fluidic", "domain": "fluidic", "schema_version": "1.0",
                         "revision": 1, "system": normalized["fluidic_system"]})
    links = []
    domains = {p["domain"] for p in payloads}
    if {"mechanical", "electrical"} <= domains:
        for view in views:
            if view["ref_des"] not in electrical_refs:
                continue
            links.append(DomainLink(
                link_id=f"shared.{view['object_id']}", kind="same_object",
                source=LinkEndpoint(artifact_id=f"{product_id}.mechanical", revision=1, object_id=view["object_id"]),
                target=LinkEndpoint(artifact_id=f"{product_id}.electrical", revision=1, object_id=view["object_id"]),
                fact_owners={"geometry": "mechanical", "pins": "electrical", "connectivity": "electrical"},
            ))
    refs = [ArtifactReference(artifact_id=p["artifact_id"], domain=p["domain"], schema_version=p["schema_version"],
                              revision=p["revision"], sha256=fingerprint(p)) for p in payloads]
    for payload in payloads:
        findings.extend(validate_domain(payload))
    return ProductBundle(
        manifest=ProductManifest(product_id=product_id, artifacts=refs, links=links),
        artifacts={p["artifact_id"]: p for p in payloads},
        compatibility=CompatibilityRecord(source=original, source_sha256=fingerprint(original),
                                          domain_baselines={p["artifact_id"]: fingerprint(p) for p in payloads},
                                          findings=findings),
    )


def export_hardware_ir(
    bundle: ProductBundle,
    *,
    legacy_seed: dict[str, Any] | None = None,
    allow_lossy: bool = False,
) -> HardwareExport:
    """Export through the compatibility format, refusing unacknowledged losses.

    New products with component inventories require an explicit legacy_seed for
    procurement/BOM/assembly metadata; missing part numbers or prices are never
    invented here. An unchanged imported document round-trips byte-equivalent
    JSON data, including unknown fields and the original schema version.
    """
    from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

    record = bundle.compatibility
    if record is not None and fingerprint(record.source) != record.source_sha256:
        return HardwareExport(findings=[Finding(code="source_hash", location="compatibility",
                                               message="Preserved legacy source was modified.")])
    current = {key: fingerprint(value) for key, value in bundle.artifacts.items()}
    if record is not None and set(record.domain_baselines) - set(current):
        return HardwareExport(findings=[Finding(code="removed_domain", location="artifacts",
                                               message="Explicitly reconcile removed domains with the legacy source before exporting.")])
    unchanged = record is not None and current == record.domain_baselines
    report = validate_product(bundle)
    # Structural composition failures must never be bypassed by allow_lossy.
    if report.composition:
        return HardwareExport(findings=report.composition)
    if unchanged:
        return HardwareExport(payload=deepcopy(record.source), findings=deepcopy(record.findings))
    findings = [item for values in report.local.values() for item in values]
    if report.blocked_domains:
        findings.append(Finding(code="incomplete_plan", location="plan", message="Selected domain work remains blocked or incomplete."))
    if any(f.severity == "error" for f in findings):
        return HardwareExport(findings=findings)
    source = deepcopy(record.source if record else (legacy_seed or {}))
    legacy = HardwareIntermediateRepresentation.model_validate(source)
    payload = legacy.model_dump(mode="json")
    # Unknown fields are never silently discarded when an edited export normalizes.
    for path in _ignored_paths(source, payload):
        findings.append(Finding(code="unmapped_legacy_field", location=path,
                                message="Edited HardwareIR normalization cannot represent this source field; source remains in the bundle."))
    definitions = {p["part_definition_id"]: p for p in payload["part_definitions"]}
    components = {c["ref_des"]: c for c in payload["components"]}
    object_ids: dict[str, str] = {}
    for document in bundle.artifacts.values():
        domain = document["domain"]
        if domain in {"mechanical", "electrical"}:
            for component in document["components"]:
                seed = components.get(component["ref_des"])
                if seed is None or seed["part_definition_id"] != component["part_definition_id"]:
                    findings.append(Finding(code="inventory_context", location=component["object_id"],
                                            message="Provide matching component and procurement identity in legacy_seed/source."))
                prior = object_ids.get(component["ref_des"])
                if prior is not None and prior != component["object_id"]:
                    findings.append(Finding(code="identity_conflict", location=component["ref_des"],
                                            message="Different physical objects share a legacy reference designator."))
                object_ids[component["ref_des"]] = component["object_id"]
            for part in document["parts"]:
                target = definitions.get(part["part_definition_id"])
                if target is None:
                    findings.append(Finding(code="inventory_context", location=part["part_definition_id"],
                                            message="Provide procurement metadata in legacy_seed/source for this part."))
                    continue
                keys = ("dimensions_mm",) if domain == "mechanical" else ("pins", "electrical_specs")
                for key in keys:
                    target[key] = deepcopy(part[key])
                if domain == "electrical":
                    for key in ("name", "part_number", "category"):
                        if part[key] != target[key]:
                            findings.append(Finding(code="shared_fact_conflict", location=part["part_definition_id"],
                                                    message=f"Electrical display {key} differs from authoritative procurement metadata."))
                if domain == "mechanical" and (part["material"] is not None or part["geometry_refs"]):
                    findings.append(Finding(code="unrepresentable", location=part["part_definition_id"],
                                            message="Legacy parts lack typed material and geometry_refs; preserved only in MechanicalIR."))
        if domain == "mechanical":
            payload.update({key: deepcopy(document[key]) for key in ("mechanical", "cad_model")})
        elif domain == "electrical":
            payload.update({key: deepcopy(document[key]) for key in ("nets", "buses", "pin_mappings", "power_rails")})
            if document["estimated_current_draw_ma"] is None:
                findings.append(Finding(code="unknown_current", location="estimated_current_draw_ma",
                                        message="Legacy HardwareIR cannot represent unknown current (its default is zero)."))
            else:
                payload["estimated_current_draw_ma"] = document["estimated_current_draw_ma"]
        elif domain == "fluidic":
            payload["fluidic_system"] = deepcopy(document["system"])
        elif domain == "biological":
            findings.append(Finding(code="unrepresentable", location=document["artifact_id"],
                                    message="HardwareIR has no BiologicalExperiment field; export BiologicalIR separately."))
    # Missing identity data is not a lossy-conversion option.
    if any(f.code in {"inventory_context", "identity_conflict", "shared_fact_conflict"} for f in findings):
        return HardwareExport(findings=findings)
    if not allow_lossy and any(f.severity == "error" for f in findings):
        return HardwareExport(findings=findings)
    if object_ids:
        payload["assembly_metadata"]["domain_object_ids"] = object_ids
    payload["validation"] = {"critical": [], "warning": [], "info": []}
    payload["is_valid"] = False
    payload["assembly_metadata"]["domain_export_validation"] = "recheck_required"
    findings.append(Finding(code="recheck_required", location="validation", severity="warning",
                            message="Changed domain export invalidates previous legacy validation; rerun the downstream validator."))
    # Validate the adapter result rather than emitting an internally inconsistent BOM.
    payload = HardwareIntermediateRepresentation.model_validate(payload).model_dump(mode="json")
    return HardwareExport(payload=payload, findings=findings)

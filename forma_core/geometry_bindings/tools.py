"""Public bounded adapters and inspection operations for geometry binding sidecars."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from forma_core.domains.base import DomainModel
from .models import (
    Binding, BindingManifest, GeometryArtifact, GeometryEntity, Pose, SemanticRef,
)
from .resolution import (
    forward_lookup, invalidate_geometry, reverse_lookup, validate_manifest,
    validate_manifest_against_product,
)


class LegacyPlacementInput(DomainModel):
    """Bridge explicit legacy placements without inventing exact CAD features."""

    product_id: str
    geometry: GeometryArtifact
    semantic_artifact_id: str
    semantic_revision: int = Field(ge=1)
    semantic_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    placements: list[dict[str, Any]]
    object_ids: dict[str, str] = Field(default_factory=dict)


def placement_envelopes(payload: LegacyPlacementInput) -> BindingManifest:
    """Create traceable *unverified* placement envelopes from legacy records.

    This adapter deliberately avoids importing HardwareIR and never claims
    that a legacy component envelope maps to a specific STEP face or body.
    Refdes only provides a migration alias; caller may provide stable IDs.
    """
    from forma_core.mechanics.primitives import MechanicalPlacement

    entities: list[GeometryEntity] = []
    bindings: list[Binding] = []
    seen: set[str] = set()
    for raw in payload.placements:
        placement = MechanicalPlacement.model_validate(raw)
        if placement.ref_des in seen:
            raise ValueError("Repeated legacy ref_des cannot be mapped to independent occurrences.")
        seen.add(placement.ref_des)
        entity_id = payload.object_ids.get(placement.ref_des, placement.ref_des)
        selector = f"legacy-envelope/{entity_id}"
        entities.append(GeometryEntity(
            entity_id=selector, selector=selector, kind="mesh_group",
            anchor=(0, 0, 0),
            bounds_mm=(abs(placement.size.x_mm), abs(placement.size.y_mm),
                       abs(placement.size.z_mm)),
            fidelity="envelope", source="agent_estimate",
            verified_from_artifact=False,
        ))
        bindings.append(Binding(
            binding_id=f"legacy/{entity_id}",
            semantic=SemanticRef(
                domain="mechanical", artifact_id=payload.semantic_artifact_id,
                revision=payload.semantic_revision, sha256=payload.semantic_sha256,
                entity_id=entity_id, occurrence_path=(entity_id,),
                label=placement.label or placement.ref_des,
            ),
            selector=selector, role="envelope",
            resolution="unsupported",
            method="approximate", fidelity="envelope", source="agent_estimate",
            occurrence_pose=Pose(
                translation_mm=(placement.position.x_mm,
                                placement.position.y_mm, placement.position.z_mm),
                euler_xyz_deg=(placement.orientation_deg.x_deg,
                               placement.orientation_deg.y_deg,
                               placement.orientation_deg.z_deg),
            ),
            assumptions=["Legacy component placement is a preview envelope; "
                         "no STEP body/face mapping has been verified."],
        ))
    return BindingManifest(product_id=payload.product_id, geometry=payload.geometry,
                           entities=entities, bindings=bindings,
                           evidence={"adapter": "legacy MechanicalPlacement; not exact CAD geometry"})


def binding_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Stateless schema/lookup/validation used by SDK, CLI and existing MCP."""
    action = payload.get("action")
    if action == "schema":
        if set(payload) != {"action"}:
            raise ValueError("Binding schema request must contain only action.")
        return {"manifest": BindingManifest.model_json_schema(),
                "legacy": LegacyPlacementInput.model_json_schema()}
    if action == "legacy":
        if set(payload) != {"action", "input"}:
            raise ValueError("Legacy adapter requires only action and input.")
        return placement_envelopes(LegacyPlacementInput.model_validate(payload["input"])).model_dump(mode="json")
    if action not in {"validate", "validate-product", "forward", "reverse", "invalidate"}:
        raise ValueError("Supported binding actions: schema, legacy, validate, validate-product, forward, reverse, invalidate.")
    manifest = BindingManifest.model_validate(payload["manifest"])
    if action == "validate":
        if set(payload) != {"action", "manifest"}:
            raise ValueError("Validate accepts only a binding manifest.")
        return validate_manifest(manifest).model_dump(mode="json")
    if action == "validate-product":
        if set(payload) != {"action", "manifest", "product"}:
            raise ValueError("Product-linked validation requires only action, manifest, and product.")
        return validate_manifest_against_product(manifest, payload["product"]).model_dump(mode="json")
    if action == "forward":
        key = payload["semantic"]
        if not isinstance(key, dict):
            raise ValueError("Semantic lookup identity must be a dictionary.")
        return {"bindings": [item.model_dump(mode="json") for item in forward_lookup(
            manifest, domain=key["domain"], artifact_id=key["artifact_id"],
            entity_id=key["entity_id"],
            occurrence_path=tuple(key["occurrence_path"]) if "occurrence_path" in key else None,
            feature_id=key.get("feature_id"),
        )]}
    if action == "reverse":
        return {"bindings": reverse_lookup(manifest, geometric_entity_id=payload["geometric_entity_id"])}
    replacement = payload["geometry"]
    if not isinstance(replacement, dict) or set(replacement) != {"revision", "sha256"}:
        raise ValueError("Geometry invalidation requires exactly revision and sha256.")
    return invalidate_geometry(
        manifest, new_revision=replacement["revision"],
        new_sha256=replacement["sha256"],
    ).model_dump(mode="json")

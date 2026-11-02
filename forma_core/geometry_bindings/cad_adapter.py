"""Opt-in, locally trusted STEP body inspection using the installed CAD kernel.

This adapter reads an explicitly supplied local STEP file. It is intentionally
SDK-only; the authenticated hosted MCP surface never accepts arbitrary paths.
No numeric face index or free-form file name is considered persistent geometry.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from .models import Binding, BindingManifest, GeometryArtifact, GeometryEntity, Pose, SemanticRef


class GeometryInspectionError(ValueError):
    """A STEP artifact cannot be verified at the requested identity/fidelity."""


def inspect_single_step_body(
    path: str | Path,
    *,
    geometry: GeometryArtifact,
    product_id: str,
    semantic: SemanticRef,
    role: Literal["physical_body", "envelope"] = "physical_body",
) -> BindingManifest:
    """Build a pinned, verified coarse BODY binding from a real single-solid STEP.

    Use only if the caller knows the source semantic occurrence produced this
    isolated part artifact. This function checks the STEP bytes and actual
    geometry, but cannot infer part semantics or identify exact mating faces.
    The tool has **no** silent fallback for multi-solid or empty exports.
    """
    source = Path(path)
    if geometry.format != "step" or geometry.units != "mm":
        raise GeometryInspectionError("The bounded STEP body adapter expects a millimetre STEP artifact.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != geometry.sha256:
        raise GeometryInspectionError("STEP bytes disagree with the pinned geometry artifact digest.")
    try:
        import cadquery as cq
    except ImportError as exc:
        raise GeometryInspectionError("CadQuery/OCCT is required to inspect STEP geometry.") from exc
    try:
        workplane = cq.importers.importStep(str(source))
        solids = workplane.solids().vals()
    except Exception as exc:
        raise GeometryInspectionError(f"Could not import pinned STEP geometry: {type(exc).__name__}") from exc
    if len(solids) != 1:
        raise GeometryInspectionError(
            f"STEP body selection is unresolved: expected one isolated solid, found {len(solids)}."
        )
    solid = solids[0]
    bounds = solid.BoundingBox()
    dimensions = (float(bounds.xlen), float(bounds.ylen), float(bounds.zlen))
    if min(dimensions) <= 0:
        raise GeometryInspectionError("Imported STEP solid has a degenerate bounding envelope.")
    selector = f"step-sha256/{digest}/solid-singleton"
    return BindingManifest(
        product_id=product_id, geometry=geometry,
        entities=[GeometryEntity(
            entity_id="body-0", selector=selector, kind="body",
            anchor=(float((bounds.xmin + bounds.xmax) / 2.0),
                    float((bounds.ymin + bounds.ymax) / 2.0),
                    float((bounds.zmin + bounds.zmax) / 2.0)),
            bounds_mm=dimensions, fidelity="detailed",
            source="supplied_cad", verified_from_artifact=True,
        )],
        bindings=[Binding(
            binding_id=f"step-body/{semantic.entity_id}/{'/'.join(semantic.occurrence_path)}",
            semantic=semantic, selector=selector, role=role,
            method="artifact_local", resolution="resolved",
            fidelity="detailed", source="supplied_cad",
            occurrence_pose=Pose(),
        )],
        evidence={"verified_geometry": "sha256 and one native CADQuery/OCCT imported STEP solid",
                  "selection_limit": "Body-level only; face/port selectors and semantic authorship unverified"},
    )

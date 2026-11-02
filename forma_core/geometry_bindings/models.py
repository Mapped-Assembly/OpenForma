"""Versioned, revision-pinned semantic-to-geometric identity contracts.

A binding describes a relationship, not an assertion that arbitrary geometry
has exact feature semantics. Source documents remain owned by their domain IRs.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from forma_core.domains.base import DomainModel, Text, unique

Digest = str
Vector3 = tuple[float, float, float]
Origin = Literal["supplied_cad", "measured", "specification", "authored", "agent_estimate", "heuristic"]
Fidelity = Literal["envelope", "simplified", "detailed", "unknown"]
Resolution = Literal["resolved", "unresolved", "ambiguous", "stale", "unsupported", "not_applicable"]
TargetKind = Literal["assembly", "body", "face", "edge", "vertex", "datum", "port", "region", "mesh_group"]
Role = Literal["physical_body", "envelope", "mounting_face", "mating_interface",
               "pin", "fluid_boundary", "clearance", "biological_host", "feature"]
Method = Literal["named_feature", "artifact_local", "approximate"]


class Pose(DomainModel):
    """Active right-handed XYZ Euler pose, applying Rx then Ry then Rz, in mm."""

    translation_mm: Vector3 = (0.0, 0.0, 0.0)
    euler_xyz_deg: Vector3 = (0.0, 0.0, 0.0)
    convention: Literal["RH_active_XYZ"] = "RH_active_XYZ"

    @model_validator(mode="after")
    def finite_pose(self) -> "Pose":
        """A translation/rotation with NaN cannot be a reproducible transform."""
        if any(not math.isfinite(v) for v in (*self.translation_mm, *self.euler_xyz_deg)):
            raise ValueError("Pose values must be finite.")
        return self


class SemanticRef(DomainModel):
    """Stable domain entity and actual occurrence; display labels are not keys."""

    domain: Text
    artifact_id: Text
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entity_id: Text
    occurrence_path: tuple[Text, ...] = Field(min_length=1)
    feature_id: Text | None = None
    part_definition_id: Text | None = None
    label: str | None = None


class GeometryArtifact(DomainModel):
    """A pinned CAD/mesh revision with one declared metric coordinate frame."""

    artifact_id: Text
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    format: Literal["step", "stl", "obj", "3mf", "opencad", "glb", "scene", "synthetic"]
    producer: Text
    producer_version: Text
    frame: Text
    units: Literal["mm", "m"] = "mm"
    handedness: Literal["right"] = "right"


class GeometryEntity(DomainModel):
    """A geometric item exposed by an adapter, with revision-local identity.

    A named selector is valid only within this exact geometry artifact revision.
    A mesh envelope has no implied exact face, contact or pin features.
    """

    entity_id: Text
    selector: Text
    kind: TargetKind
    anchor: Vector3 = (0.0, 0.0, 0.0)
    normal: Vector3 | None = None
    bounds_mm: Vector3 | None = None
    fidelity: Fidelity = "unknown"
    source: Origin
    verified_from_artifact: bool = False

    @model_validator(mode="after")
    def valid_geometry(self) -> "GeometryEntity":
        """Reject non-finite anchors, invalid dimensions and a zero normal."""
        vectors = [self.anchor] + ([self.normal] if self.normal else []) + ([self.bounds_mm] if self.bounds_mm else [])
        if any(not math.isfinite(n) for vec in vectors for n in vec):
            raise ValueError("Geometric vectors must be finite.")
        if self.bounds_mm is not None and any(v <= 0 for v in self.bounds_mm):
            raise ValueError("Geometric bounds must be positive.")
        if self.normal is not None and math.isclose(sum(v*v for v in self.normal), 0.0, abs_tol=1e-15):
            raise ValueError("Geometric face normal cannot be zero.")
        return self


class Binding(DomainModel):
    """One independently versioned semantic-to-geometric association."""

    binding_id: Text
    semantic: SemanticRef
    selector: Text | None = None
    role: Role
    method: Method = "artifact_local"
    required: bool = True
    resolution: Resolution = "unresolved"
    fidelity: Fidelity = "unknown"
    source: Origin
    occurrence_pose: Pose = Field(default_factory=Pose)
    assumptions: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def declared_status(self) -> "Binding":
        """No resolved binding without a selector or inapplicable target."""
        if self.resolution == "resolved" and not self.selector:
            raise ValueError("Resolved binding requires a geometry selector.")
        if self.resolution == "not_applicable" and self.selector is not None:
            raise ValueError("Nonspatial bindings cannot select geometric targets.")
        return self


class GeometryRequirement(DomainModel):
    """A bounded requirement, distinct from a measured geometric result."""

    requirement_id: Text
    kind: Literal["coincident", "aligned", "axis_offset", "envelope_clearance"]
    source_binding_id: Text
    target_binding_id: Text
    tolerance_mm: float = Field(0.1, ge=0, allow_inf_nan=False)
    tolerance_deg: float = Field(1.0, ge=0, allow_inf_nan=False)
    offset_mm: float | None = Field(None, allow_inf_nan=False)
    axis: Literal["x", "y", "z"] | None = None
    required: bool = True

    @model_validator(mode="after")
    def required_parameter(self) -> "GeometryRequirement":
        """Axis offsets must specify the axis and target signed displacement."""
        if self.kind == "axis_offset" and (self.axis is None or self.offset_mm is None):
            raise ValueError("Axis offset needs an axis and a signed offset_mm.")
        return self


class BindingManifest(DomainModel):
    """Portable sidecar, separate from CAD/source IR and scene/runtime state."""

    schema_version: Literal["1.0"] = "1.0"
    product_id: Text
    geometry: GeometryArtifact
    entities: list[GeometryEntity] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    requirements: list[GeometryRequirement] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def local_identity(self) -> "BindingManifest":
        """Keep manifest-local IDs stable while permitting ambiguous selectors."""
        unique([entity.entity_id for entity in self.entities], "geometric entity IDs")
        unique([binding.binding_id for binding in self.bindings], "binding IDs")
        unique([requirement.requirement_id for requirement in self.requirements], "requirement IDs")
        return self

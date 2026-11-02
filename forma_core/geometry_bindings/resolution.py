"""Pure semantic ⇄ geometry resolution, transforms, and bounded checks.

These APIs use only manifest-local entity data, never infer a face from a name,
never trust an unpinned runtime shape ID, and never silently repair stale links.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainModel, Finding, fingerprint
from .models import Binding, BindingManifest, GeometryEntity, GeometryRequirement, Pose, Vector3


class BindingResult(DomainModel):
    """Stable inspection result and transformed point in the artifact frame."""

    binding_id: str
    status: Literal["resolved", "unresolved", "ambiguous", "stale", "unsupported", "not_applicable"]
    target_ids: list[str] = Field(default_factory=list)
    anchor_mm: Vector3 | None = None
    bounds_mm: Vector3 | None = None
    fidelity: str
    origin: str
    message: str


class RequirementResult(DomainModel):
    """Verified-by-contract geometry check, not a geometry solver."""

    requirement_id: str
    status: Literal["pass", "fail", "unknown", "unsupported"]
    measured: float | None = None
    units: Literal["mm", "deg"] | None = None
    message: str


class BindingReport(DomainModel):
    """Independent resolution findings and requirement calculations."""

    is_valid: bool
    bindings: list[BindingResult]
    requirements: list[RequirementResult]
    findings: list[Finding]
    input_sha256: str


def rotate_xyz(vector: Vector3, euler_xyz_deg: Vector3) -> Vector3:
    """Apply active right-handed Rx then Ry then Rz (column vectors)."""
    x, y, z = vector
    a, b, c = (math.radians(d) for d in euler_xyz_deg)
    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
    x, z = x * math.cos(b) + z * math.sin(b), -x * math.sin(b) + z * math.cos(b)
    x, y = x * math.cos(c) - y * math.sin(c), x * math.sin(c) + y * math.cos(c)
    return x, y, z


def apply_pose(point: Vector3, pose: Pose) -> Vector3:
    """Apply a single artifact-local → occurrence-placement transform, in mm."""
    moved = rotate_xyz(point, pose.euler_xyz_deg)
    return tuple(moved[i] + pose.translation_mm[i] for i in range(3))  # type: ignore[return-value]


def composed_pose(point: Vector3, *poses: Pose) -> Vector3:
    """Apply each declared local → parent pose once, in explicit order."""
    for pose in poses:
        point = apply_pose(point, pose)
    return point


def axis_bounds(dimensions: Vector3, pose: Pose) -> Vector3:
    """Compute the AABB size of a rotated centered box, not exact BRep clearance."""
    half = [v / 2.0 for v in dimensions]
    corners = [apply_pose((x, y, z), pose)
               for x in (-half[0], half[0])
               for y in (-half[1], half[1])
               for z in (-half[2], half[2])]
    return tuple(max(p[i] for p in corners) - min(p[i] for p in corners) for i in range(3))  # type: ignore[return-value]


def _geometry_point(manifest: BindingManifest, target: GeometryEntity, binding: Binding) -> Vector3:
    """Normalize artifact meters to millimeters before the occurrence placement."""
    scale = 1000.0 if manifest.geometry.units == "m" else 1.0
    point = tuple(v * scale for v in target.anchor)
    return apply_pose(point, binding.occurrence_pose)  # type: ignore[arg-type]


def _geometry_bounds(manifest: BindingManifest, target: GeometryEntity, binding: Binding) -> Vector3 | None:
    """Transform a known envelope without pretending to have exact solid geometry."""
    if target.bounds_mm is None:
        return None
    scale = 1000.0 if manifest.geometry.units == "m" else 1.0
    return axis_bounds(tuple(v * scale for v in target.bounds_mm), binding.occurrence_pose)  # type: ignore[arg-type]


def resolve_binding(manifest: BindingManifest, binding: Binding) -> BindingResult:
    """Resolve a selector only against this exact artifact revision's catalog."""
    if binding.resolution != "resolved":
        return BindingResult(binding_id=binding.binding_id, status=binding.resolution,
                             fidelity=binding.fidelity, origin=binding.source,
                             message=f"Binding declared {binding.resolution}; no geometric claim made.")
    targets = [target for target in manifest.entities if target.selector == binding.selector]
    if not targets:
        return BindingResult(binding_id=binding.binding_id, status="unresolved",
                             fidelity=binding.fidelity, origin=binding.source,
                             message="Selector is missing in the pinned geometry catalog.")
    if len(targets) != 1:
        return BindingResult(binding_id=binding.binding_id, status="ambiguous",
                             target_ids=[target.entity_id for target in targets],
                             fidelity=binding.fidelity, origin=binding.source,
                             message="Selector identifies several geometric entities.")
    target = targets[0]
    if binding.role in {"pin", "mounting_face", "mating_interface"} and (
            target.kind not in {"port", "face", "datum"} or
            binding.fidelity == "envelope" or target.fidelity == "envelope"):
        return BindingResult(binding_id=binding.binding_id, status="unsupported",
                             target_ids=[target.entity_id], fidelity=binding.fidelity,
                             origin=binding.source,
                             message="An approximate envelope cannot resolve an exact contact, face or pin.")
    if not target.verified_from_artifact and manifest.geometry.format != "synthetic":
        return BindingResult(binding_id=binding.binding_id, status="unsupported",
                             target_ids=[target.entity_id], fidelity=binding.fidelity,
                             origin=binding.source,
                             message="Entity was not verified against the pinned CAD/mesh artifact.")
    return BindingResult(binding_id=binding.binding_id, status="resolved",
                         target_ids=[target.entity_id],
                         anchor_mm=_geometry_point(manifest, target, binding),
                         bounds_mm=_geometry_bounds(manifest, target, binding),
                         fidelity=binding.fidelity, origin=binding.source,
                         message="Resolved at declared fidelity against pinned catalog only.")


def forward_lookup(manifest: BindingManifest, *, domain: str, artifact_id: str,
                   entity_id: str, occurrence_path: tuple[str, ...] | None = None,
                   feature_id: str | None = None) -> list[BindingResult]:
    """Find a semantic object's separately scoped occurrences without label matching."""
    return [
        resolve_binding(manifest, b) for b in manifest.bindings
        if b.semantic.domain == domain and b.semantic.artifact_id == artifact_id
        and b.semantic.entity_id == entity_id
        and (occurrence_path is None or b.semantic.occurrence_path == occurrence_path)
        and (feature_id is None or b.semantic.feature_id == feature_id)
    ]


def reverse_lookup(manifest: BindingManifest, *, geometric_entity_id: str) -> list[dict[str, object]]:
    """Find semantic occurrences associated with a pinned geometry entity."""
    result: list[dict[str, object]] = []
    for binding in manifest.bindings:
        resolved = resolve_binding(manifest, binding)
        if geometric_entity_id not in resolved.target_ids or resolved.status != "resolved":
            continue
        result.append({
            "binding_id": binding.binding_id,
            "semantic": binding.semantic.model_dump(mode="json"),
            "role": binding.role,
            "fidelity": resolved.fidelity,
            "origin": resolved.origin,
        })
    return result


def _normal(manifest: BindingManifest, binding: Binding) -> Vector3 | None:
    """Resolve and normalize a supported surface normal into occurrence coordinates."""
    resolved = resolve_binding(manifest, binding)
    if resolved.status != "resolved":
        return None
    target = next((e for e in manifest.entities if e.entity_id == resolved.target_ids[0]), None)
    if target is None or target.normal is None:
        return None
    rotated = rotate_xyz(target.normal, binding.occurrence_pose.euler_xyz_deg)
    length = math.sqrt(sum(x * x for x in rotated))
    return tuple(x / length for x in rotated)  # type: ignore[return-value]


def evaluate_requirement(manifest: BindingManifest, requirement: GeometryRequirement) -> RequirementResult:
    """Check explicit point, axis and normal obligations within declared fidelity."""
    bindings = {b.binding_id: b for b in manifest.bindings}
    source = bindings.get(requirement.source_binding_id)
    target = bindings.get(requirement.target_binding_id)
    if source is None or target is None:
        return RequirementResult(requirement_id=requirement.requirement_id, status="unknown",
                                 message="A required semantic binding is missing.")
    left = resolve_binding(manifest, source)
    right = resolve_binding(manifest, target)
    if left.status != "resolved" or right.status != "resolved":
        return RequirementResult(requirement_id=requirement.requirement_id, status="unknown",
                                 message=f"Required binding unresolved: {left.status}/{right.status}.")
    if left.anchor_mm is None or right.anchor_mm is None:
        return RequirementResult(requirement_id=requirement.requirement_id, status="unknown",
                                 message="Geometric anchors are unavailable.")
    if requirement.kind == "axis_offset":
        assert requirement.axis and requirement.offset_mm is not None
        axis = {"x": 0, "y": 1, "z": 2}[requirement.axis]
        delta = right.anchor_mm[axis] - left.anchor_mm[axis]
        ok = abs(delta - requirement.offset_mm) <= requirement.tolerance_mm
        return RequirementResult(requirement_id=requirement.requirement_id,
                                 status="pass" if ok else "fail", measured=delta,
                                 units="mm", message="Signed axis displacement in assembly frame.")
    if requirement.kind == "envelope_clearance":
        return RequirementResult(requirement_id=requirement.requirement_id,
                                 status="unsupported",
                                 message="Envelope AABB is not a supported exact-solid clearance check.")
    ln = _normal(manifest, source)
    rn = _normal(manifest, target)
    if ln is None or rn is None:
        return RequirementResult(requirement_id=requirement.requirement_id,
                                 status="unknown", message="Named face/datum normals are unavailable.")
    dot = max(-1.0, min(1.0, sum(ln[i] * rn[i] for i in range(3))))
    angle = math.degrees(math.acos(abs(dot)))
    if requirement.kind == "aligned":
        return RequirementResult(requirement_id=requirement.requirement_id,
                                 status="pass" if angle <= requirement.tolerance_deg else "fail",
                                 measured=angle, units="deg",
                                 message="Acute unsigned surface-normal misalignment.")
    distance = sum((right.anchor_mm[i] - left.anchor_mm[i]) * ln[i] for i in range(3))
    ok = abs(distance) <= requirement.tolerance_mm and angle <= requirement.tolerance_deg
    return RequirementResult(requirement_id=requirement.requirement_id,
                             status="pass" if ok else "fail", measured=distance, units="mm",
                             message=f"Signed plane distance in mm; angular misalignment {angle:.6g} deg.")


def validate_manifest(manifest: BindingManifest) -> BindingReport:
    """Validate geometry selection independently of semantic domain-local validity."""
    findings: list[Finding] = []
    bindings: list[BindingResult] = []
    for binding in manifest.bindings:
        resolved = resolve_binding(manifest, binding)
        bindings.append(resolved)
        if resolved.status not in {"resolved", "not_applicable"}:
            findings.append(Finding(
                code=f"geometry_{resolved.status}", location=binding.binding_id,
                severity="error" if binding.required else "warning",
                message=resolved.message,
            ))
    checks = [evaluate_requirement(manifest, item) for item in manifest.requirements]
    for request, check in zip(manifest.requirements, checks):
        if check.status != "pass":
            findings.append(Finding(
                code=f"geometry_check_{check.status}", location=request.requirement_id,
                severity="error" if request.required and check.status == "fail" else "warning",
                message=check.message,
            ))
    return BindingReport(is_valid=not any(f.severity == "error" for f in findings),
                         bindings=bindings, requirements=checks, findings=findings,
                         input_sha256=fingerprint(manifest))


def invalidate_geometry(manifest: BindingManifest, *, new_revision: int, new_sha256: str) -> BindingManifest:
    """Invalidate all geometry bindings after a new CAD revision; no stale reuse."""
    changed = manifest.model_copy(deep=True)
    changed.geometry.revision = new_revision
    changed.geometry.sha256 = new_sha256
    changed.entities = []
    for binding in changed.bindings:
        if binding.resolution == "resolved":
            binding.resolution = "stale"
    return changed

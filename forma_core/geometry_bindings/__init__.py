"""Independent geometry binding contract shared across engineering domain IRs.

The owning domain artifacts remain versioned separately. The manifest is a
cross-domain sidecar, not a new engineering IR or a CAD kernel.
"""

from .models import Binding, BindingManifest, GeometryArtifact, GeometryEntity, GeometryRequirement, Pose, SemanticRef
from .resolution import (
    BindingReport, apply_pose, axis_bounds, composed_pose, forward_lookup,
    invalidate_geometry, reverse_lookup, rotate_xyz, validate_manifest,
)

__all__ = [
    "Binding", "BindingManifest", "BindingReport", "GeometryArtifact",
    "GeometryEntity", "GeometryRequirement", "Pose", "SemanticRef",
    "apply_pose", "axis_bounds", "composed_pose", "forward_lookup",
    "invalidate_geometry", "reverse_lookup", "rotate_xyz", "validate_manifest",
]

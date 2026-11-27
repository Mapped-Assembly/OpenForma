"""Resolve native machine joint feedback independently of any viewer."""
from __future__ import annotations
import math
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from .workcell import VirtualWorkcell


def body_transforms(spec: VirtualWorkcell, positions: dict[str, float | None]) -> dict[str, Any]:
    """Resolve the existing MachineDefinition joint hierarchy using simulated feedback."""
    import numpy as np
    axes = {a.axis_id: a for a in spec.machine.definition.axes}
    transforms: dict[str, Any] = {}

    def resolve(axis_id: str) -> Any:
        """Compose rotation/translation about each declared world rest origin."""
        if axis_id in transforms:
            return transforms[axis_id]
        joint = axes[axis_id].joint
        if joint is None:
            raise ValueError('Renderable axes require joint bindings.')
        value = positions.get(axis_id)
        if value is None or not math.isfinite(value):
            raise ValueError('Cannot render missing or nonfinite machine feedback.')
        value *= joint.sign
        matrix = np.eye(4)
        direction = np.array(joint.axis)
        if joint.kind == 'prismatic':
            matrix[:3, 3] = direction * value
        else:
            angle = math.radians(value)
            x, y, z = direction
            cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
            rotation = np.eye(3) + math.sin(angle)*cross + (1-math.cos(angle))*(cross@cross)
            matrix[:3, :3] = rotation
            origin = np.array(joint.origin_mm)
            matrix[:3, 3] = origin - rotation@origin
        transforms[axis_id] = resolve(joint.parent_axis)@matrix if joint.parent_axis else matrix
        return transforms[axis_id]

    return {body: resolve(axis.axis_id) for axis in axes.values() if axis.joint for body in axis.joint.body_ids}


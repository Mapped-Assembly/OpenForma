"""Single-joint virtual position controller for resolved Forma CAD projects."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from .primitives import MechanicalPlacement


class _Joint(BaseModel):
    """OpenCAD joint fields used to generate a physical constraint."""

    model_config = ConfigDict(allow_inf_nan=False)
    id: str = Field(min_length=1)
    type: Literal["revolute", "prismatic"]
    unit: Literal["radian", "millimeter", "mm"]
    axis: tuple[float, float, float]
    origin_mm: tuple[float, float, float]
    lower_limit: float
    upper_limit: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class _Track(BaseModel):
    """Resolved part associations, independent of saved animation samples."""

    joint: _Joint
    target_ref: str | None = None
    parent_ref: str | None = None


@dataclass(frozen=True)
class Control:
    """Discoverable virtual position input with limits in SI units."""

    joint_id: str
    target_ref: str
    unit: str
    minimum: float
    maximum: float


def _vector(values: Iterable[float]) -> str:
    """Format finite SI coordinates for MJCF."""
    values = tuple(values)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Expected finite placement coordinates.")
    return " ".join(map(str, values))


def compile_project(project: dict[str, Any]) -> tuple[str, Control]:
    """Translate one fixed-base hinge or slider and its placement envelopes."""
    kin = (project.get("cad_model") or {}).get("kinematics") or {}
    tracks = kin.get("tracks") or []
    if len(tracks) != 1 or (kin.get("mechanism") or {}).get("gear_couplings"):
        raise ValueError("Expected one resolved, independent CAD motion track.")
    if kin.get("coordinate_system", "z-up") != "z-up":
        raise ValueError("Expected z-up CAD coordinates.")
    track = _Track.model_validate(tracks[0])
    joint = track.joint
    hinge = joint.type == "revolute"
    if hinge != (joint.unit == "radian") or math.hypot(*joint.axis) == 0:
        raise ValueError("Invalid joint units or zero motion axis.")
    scale = 1.0 if hinge else 0.001
    target = track.target_ref or joint.metadata.get("forma_target_ref")
    parent = track.parent_ref or joint.metadata.get("forma_parent_ref")
    placements = [MechanicalPlacement.model_validate(item) for item in
                  (project.get("mechanical") or {}).get("component_placements", [])]
    refs = {part.ref_des for part in placements}
    if len(refs) != len(placements) or target not in refs or parent == target or (parent and parent not in refs):
        raise ValueError("Missing or ambiguous moving or parent placement.")
    control = Control(joint.id, target, "rad" if hinge else "m",
                      joint.lower_limit * scale, joint.upper_limit * scale)
    if control.minimum >= control.maximum or not control.minimum <= 0 <= control.maximum:
        raise ValueError("Joint travel must include the zero-pose placement and have positive span.")
    limits = _vector((control.minimum, control.maximum))
    root = ET.Element("mujoco", model="Forma virtual controller")
    ET.SubElement(root, "compiler", angle="radian")
    ET.SubElement(root, "option", timestep="0.002", integrator="implicitfast", gravity="0 0 0")
    world = ET.SubElement(root, "worldbody")
    for part in placements:
        # Body frames coincide with project space at rest. Geometry carries the placement.
        body = ET.SubElement(world, "body", name=part.ref_des)
        if part.ref_des == target:
            ET.SubElement(body, "joint", name=joint.id, type="hinge" if hinge else "slide",
                          axis=_vector(joint.axis), pos=_vector(v * 0.001 for v in joint.origin_mm),
                          limited="true", range=limits)
        half_size = [v * 0.0005 for v in part.size.model_dump().values()]
        if min(half_size) <= 0:
            raise ValueError(f"Expected positive dimensions for {part.ref_des}.")
        ET.SubElement(body, "geom", type="box", size=_vector(half_size),
                      pos=_vector(v * 0.001 for v in part.position.model_dump().values()),
                      euler=_vector(map(math.radians, part.orientation_deg.model_dump().values())),
                      contype="0", conaffinity="0", density="1000")
    actuators = ET.SubElement(root, "actuator")
    ET.SubElement(actuators, "position", name=joint.id, joint=joint.id, kp="1",
                  dampratio="1", ctrllimited="true", ctrlrange=limits)
    return ET.tostring(root, encoding="unicode"), control


@dataclass(frozen=True)
class State:
    """Snapshot at a simulation instant, with measured joint state in SI units."""

    running: bool
    time_s: float
    target: float
    position: float
    velocity: float


class MuJoCoRuntime:
    """Own one headless model. The caller advances time explicitly while running."""

    def __init__(self, project: dict[str, Any]) -> None:
        """Compile the Forma project and initialize a paused session at zero."""
        xml, self.control = compile_project(project)
        try:
            import mujoco
        except ModuleNotFoundError as exc:
            if exc.name != "mujoco":
                raise
            raise RuntimeError('Install the simulation extra with pip install -e ".[mujoco]".') from exc
        self._mj = mujoco
        self._model = mujoco.MjModel.from_xml_string(xml)
        self._data = mujoco.MjData(self._model)
        self._running = False

    def _require_open(self) -> None:
        """Reject operations after native objects have been released."""
        if self._data is None:
            raise RuntimeError("Session is closed.")

    def start(self) -> None:
        """Enable stepping, preserving current time and controller state."""
        self._require_open()
        self._running = True

    def pause(self) -> None:
        """Suspend stepping until the caller resumes."""
        self._require_open()
        self._running = False

    def set_target(self, value: float) -> None:
        """Set a persistent virtual servo target in radians or meters."""
        self._require_open()
        if not self.control.minimum <= value <= self.control.maximum:
            raise ValueError(f"Target outside {self.control}.")
        self._data.ctrl[0] = value

    def step(self, steps: int = 1) -> State:
        """Integrate the live model for a positive number of fixed steps."""
        self._require_open()
        if not self._running:
            raise RuntimeError("Start the session before stepping.")
        if type(steps) is not int or steps <= 0:
            raise ValueError("Steps must be a positive integer.")
        self._mj.mj_step(self._model, self._data, nstep=steps)
        return self.state()

    def state(self) -> State:
        """Read a detached snapshot without advancing time."""
        self._require_open()
        return State(self._running, float(self._data.time), float(self._data.ctrl[0]),
                     float(self._data.qpos[0]), float(self._data.qvel[0]))

    def reset(self) -> State:
        """Clear time, velocity and targets and return to the initial paused state."""
        self.pause()
        self._mj.mj_resetData(self._model, self._data)
        return self.state()

    def close(self) -> None:
        """Release this session. Repeated calls are safe."""
        self._running = False
        self._data = None
        self._model = None

    def __enter__(self) -> Self:
        """Enter an open session."""
        self._require_open()
        return self

    def __exit__(self, *exc: object) -> None:
        """Release native state when leaving a session."""
        self.close()

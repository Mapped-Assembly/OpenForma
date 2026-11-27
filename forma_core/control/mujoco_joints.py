"""Declarative MuJoCo scalar-joint execution through the shared MachineRuntime.

Robot names, axis counts, joint/actuator bindings and gains are data. This adapter
supplies joint feedback, not locomotion, IK, grasp scoring or task policies.
"""
from __future__ import annotations

import hashlib
import math
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from .models import AdapterDiagnostics, AxisFeedback, ControlModel, Feedback, MachineDefinition
from .package import AdapterContract, PackageArtifact, digest


class MujocoAxisBinding(ControlModel):
    """Bind one degree/mm axis to one native scalar joint and actuator.

    PD gains act in SI joint coordinates (radian/metre); effort_limit is Nm/N.
    Native position servos retain the gains and force limits from their MJCF.
    """
    joint: str = Field(min_length=1)
    actuator: str = Field(min_length=1)
    control: Literal["position", "pd_torque"]
    kp: FiniteFloat = Field(default=0.0, ge=0)
    kd: FiniteFloat = Field(default=0.0, ge=0)
    effort_limit: FiniteFloat | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def control_parameters(self) -> MujocoAxisBinding:
        """Reject ignored gains and unbounded software torque controllers."""
        if self.control == "pd_torque" and (self.kp <= 0 or self.effort_limit is None):
            raise ValueError("pd_torque requires positive kp and effort_limit.")
        if self.control == "position" and (self.kp or self.kd or self.effort_limit is not None):
            raise ValueError("position uses MJCF gains/limits, not software PD parameters.")
        return self


class MujocoJointConfiguration(ControlModel):
    """Versioned, hash-pinned simulation input; no executable adapter code."""
    format: Literal["forma.mujoco-joints"] = "forma.mujoco-joints"
    version: Literal[1] = 1
    definition: MachineDefinition
    model: str = Field(min_length=1)
    artifacts: list[PackageArtifact] = Field(min_length=1)
    bindings: dict[str, MujocoAxisBinding]
    maximum_advance_s: FiniteFloat = Field(default=0.02, gt=0, le=0.1)

    @model_validator(mode="after")
    def references(self) -> MujocoJointConfiguration:
        """Require exact, unique bindings and a pinned scene artifact."""
        if self.definition.backend != "virtual":
            raise ValueError("MuJoCo joint execution is virtual only.")
        if set(self.bindings) != {a.axis_id for a in self.definition.axes}:
            raise ValueError("Bindings must cover exactly the defined axes.")
        for attribute in ("joint", "actuator"):
            names = [getattr(b, attribute) for b in self.bindings.values()]
            if len(set(names)) != len(names):
                raise ValueError(f"Duplicate {attribute} binding.")
        paths = [a.path for a in self.artifacts]
        if len(set(paths)) != len(paths) or self.model not in paths:
            raise ValueError("Artifacts must be unique and include the model.")
        return self

    @property
    def sha256(self) -> str:
        """Fingerprint definitions, bindings, gains and all declared asset hashes."""
        return digest(self.model_dump(mode="json"))


def load_mujoco_configuration(path: str | Path) -> MujocoJointConfiguration:
    """Parse a declaration without importing or running MuJoCo."""
    return MujocoJointConfiguration.model_validate_json(Path(path).read_text(encoding="utf-8"))


class MujocoJointAdapter:
    """Execute independent hinge/slide targets with real simulated feedback.

    Disarmed execution pauses virtual time, matching the existing SO-101 adapter.
    A free base remains free: this adapter never fixes or teleports it to balance.
    Callers serialize access and supply the same clock to MachineRuntime.
    """
    mode: Literal["virtual"] = "virtual"

    def __init__(self, configuration: MujocoJointConfiguration, root: str | Path,
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        """Verify pinned local inputs and reject unsupported actuator semantics."""
        import mujoco
        import numpy as np

        self.config = MujocoJointConfiguration.model_validate_json(configuration.model_dump_json())
        self.root = Path(root).resolve()
        self.clock = clock
        self._mj, self._np = mujoco, np
        scene, assets = self._verified_inputs()
        # All filenames have been resolved to verified VFS keys; no filesystem fallback.
        self.model = mujoco.MjModel.from_xml_string(scene, assets=assets)
        self.data = mujoco.MjData(self.model)
        self._axes = {a.axis_id: a for a in self.config.definition.axes}
        self._ids: dict[str, tuple[int, int, int, float]] = {}
        self._targets: dict[str, float] = {}
        self.enabled = False
        self.last_tick = clock()
        self.remainder = 0.0
        if not 0 < self.model.opt.timestep <= self.config.maximum_advance_s:
            raise ValueError("MJCF timestep must fit the configured advance budget.")
        for aid, binding in self.config.bindings.items():
            axis = self._axes[aid]
            joint, actuator = self.model.joint(binding.joint).id, self.model.actuator(binding.actuator).id
            kind = int(self.model.jnt_type[joint])
            if kind not in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE):
                raise ValueError("Only scalar hinge and slide joints can bind to axes.")
            factor = math.pi / 180 if kind == mujoco.mjtJoint.mjJNT_HINGE else .001
            if axis.unit != ("degree" if kind == mujoco.mjtJoint.mjJNT_HINGE else "mm"):
                raise ValueError("Axis units do not match the native joint.")
            lo, hi = axis.minimum * factor, axis.maximum * factor
            if self.model.jnt_limited[joint] and (lo < self.model.jnt_range[joint, 0] - 1e-8 or hi > self.model.jnt_range[joint, 1] + 1e-8):
                raise ValueError("Operational limits exceed the native joint range.")
            if (self.model.actuator_trntype[actuator] != mujoco.mjtTrn.mjTRN_JOINT
                    or self.model.actuator_trnid[actuator, 0] != joint
                    or not np.array_equal(self.model.actuator_gear[actuator], [1, 0, 0, 0, 0, 0])
                    or self.model.actuator_dyntype[actuator] != mujoco.mjtDyn.mjDYN_NONE
                    or self.model.actuator_gaintype[actuator] != mujoco.mjtGain.mjGAIN_FIXED):
                raise ValueError("Requires direct unit-gear scalar joint actuation without activation dynamics.")
            gain = self.model.actuator_gainprm[actuator]
            bias = self.model.actuator_biasprm[actuator]
            if binding.control == "pd_torque":
                if gain[0] != 1 or self.model.actuator_biastype[actuator] != mujoco.mjtBias.mjBIAS_NONE:
                    raise ValueError("pd_torque requires a native unit-gain motor.")
            else:
                if (gain[0] <= 0 or self.model.actuator_biastype[actuator] != mujoco.mjtBias.mjBIAS_AFFINE
                        or bias[0] != 0 or not math.isclose(bias[1], -gain[0]) or bias[2] > 0):
                    raise ValueError("position requires a native linear position servo.")
                if self.model.actuator_ctrllimited[actuator] and (lo < self.model.actuator_ctrlrange[actuator, 0] - 1e-8 or hi > self.model.actuator_ctrlrange[actuator, 1] + 1e-8):
                    raise ValueError("Position targets exceed actuator control range.")
            qadr, dadr = int(self.model.jnt_qposadr[joint]), int(self.model.jnt_dofadr[joint])
            self._ids[aid] = (qadr, dadr, actuator, factor)
            self._targets[aid] = axis.neutral * factor
            self.data.qpos[qadr] = axis.neutral * factor
        if {v[2] for v in self._ids.values()} != set(range(self.model.nu)):
            raise ValueError("Every model actuator must have an explicit binding.")
        self._controls()
        mujoco.mj_forward(self.model, self.data)

    def _verified_inputs(self) -> tuple[str, dict[str, bytes]]:
        """Load bounded, pinned MJCF/STL assets into an isolated virtual filesystem.

        V1 accepts one XML with mesh/texture/hfield files. Includes, plugins,
        embedded models and other external-file mechanisms are refused explicitly.
        """
        blobs: dict[str, bytes] = {}
        total = 0
        for item in self.config.artifacts:
            path = (self.root / item.path).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError(f"Artifact escapes package or is missing: {item.path}")
            total += path.stat().st_size
            if total > 128 * 1024 * 1024:
                raise ValueError("MuJoCo input artifacts exceed 128 MiB.")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item.sha256:
                raise ValueError(f"Artifact hash mismatch: {item.path}")
            blobs[item.path] = raw
        xml = ET.fromstring(blobs[self.config.model])
        if xml.tag != "mujoco" or any(e.tag in {"include", "plugin", "extension", "attach", "model"} for e in xml.iter()):
            raise ValueError("V1 requires one MJCF without includes, plugins or embedded models.")
        compiler = xml.find("compiler")
        options = compiler.attrib if compiler is not None else {}
        vfs: dict[str, bytes] = {}
        for element in xml.iter():
            if any(key.startswith("file") and key != "file" for key in element.attrib):
                raise ValueError("Unsupported external MJCF file attribute.")
            if "file" not in element.attrib:
                continue
            if element.tag not in {"mesh", "texture", "hfield"}:
                raise ValueError("Unsupported external MJCF file reference.")
            folder = options.get("meshdir" if element.tag == "mesh" else "texturedir", options.get("assetdir", "")) if element.tag != "hfield" else ""
            local = Path(self.config.model).parent / folder / element.attrib["file"]
            target = (self.root / local).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("MJCF dependency escapes package.")
            key = target.relative_to(self.root).as_posix()
            if key not in blobs:
                raise ValueError(f"MJCF dependency is not hash-pinned: {key}")
            name = f"asset_{len(vfs)}{Path(key).suffix}"
            vfs[name] = blobs[key]
            element.set("file", name)
        if compiler is not None:
            for key in ("assetdir", "meshdir", "texturedir"):
                compiler.attrib.pop(key, None)
        return ET.tostring(xml, encoding="unicode"), vfs

    def definition(self) -> MachineDefinition:
        """Return the existing machine definition declared by this input."""
        return self.config.definition

    def describe(self) -> AdapterContract:
        """Bind runtime action receipts to the exact simulation configuration."""
        return AdapterContract(adapter_id="forma.mujoco-joints", mode="virtual", evidence="contact-physics",
                               configuration_sha256=self.config.sha256,
                               actuators={a: b.actuator for a, b in self.config.bindings.items()},
                               handlers=["move"])

    def _controls(self) -> None:
        """Evaluate declared PD effort or native position targets each physics step."""
        for aid, binding in self.config.bindings.items():
            qadr, dadr, actuator, _ = self._ids[aid]
            target = self._targets[aid]
            if binding.control == "pd_torque":
                effort = binding.kp * (target - self.data.qpos[qadr]) - binding.kd * self.data.qvel[dadr]
                target = float(self._np.clip(effort, -binding.effort_limit, binding.effort_limit))
            if self.model.actuator_ctrllimited[actuator]:
                target = float(self._np.clip(target, *self.model.actuator_ctrlrange[actuator]))
            self.data.ctrl[actuator] = target

    def _advance(self) -> None:
        """Advance bounded elapsed virtual time; reject unstable solver results."""
        now = self.clock()
        elapsed = min(max(now - self.last_tick, 0), self.config.maximum_advance_s)
        self.last_tick = now
        if not self.enabled:
            self.remainder = 0.0
            return
        self.remainder += elapsed
        while self.remainder + 1e-10 >= self.model.opt.timestep:
            self._controls()
            self._mj.mj_step(self.model, self.data)
            self.remainder = max(0.0, self.remainder - self.model.opt.timestep)
            if (not self._np.isfinite(self.data.qpos).all() or not self._np.isfinite(self.data.qvel).all()
                    or any(w.number for w in self.data.warning)):
                self.enabled = False
                raise RuntimeError("MuJoCo reported nonfinite state or a solver warning.")
        self._mj.mj_forward(self.model, self.data)

    def apply(self, commands: Mapping[str, float | None], enabled: bool) -> None:
        """Validate the entire command before changing any target or advancing time."""
        if set(commands) != set(self._ids):
            raise ValueError("Commands must cover exactly the declared axes.")
        targets = dict(self._targets)
        if enabled:
            for aid, value in commands.items():
                axis = self._axes[aid]
                if value is None or isinstance(value, bool) or not math.isfinite(value) or not axis.minimum <= value <= axis.maximum:
                    raise ValueError("Enabled targets must be finite and within operational limits.")
                targets[aid] = value * self._ids[aid][3]
        if enabled:
            self._advance()
        else:
            self.last_tick = self.clock()
        self._targets = targets
        self.enabled = bool(enabled)
        if not self.enabled:
            self.data.ctrl[:] = 0
            self.remainder = 0.0
        else:
            self._controls()

    def observation(self) -> dict:
        """Return ground-truth body poses for existing viewers, never measured data."""
        return dict(provenance="simulated-ground-truth", time_s=float(self.data.time),
                    engine_version=self._mj.__version__, paused=not self.enabled,
                    contacts=int(self.data.ncon), configuration_sha256=self.config.sha256,
                    bodies=[dict(index=i, position_mm=(self.data.xpos[i] * 1000).tolist(),
                                 rotation=self.data.xmat[i].tolist()) for i in range(1, self.model.nbody)])

    def read(self) -> Feedback:
        """Expose actual simulated qpos separately from commanded targets."""
        self._advance()
        return Feedback(mode="virtual", axes={aid: AxisFeedback(simulated=float(self.data.qpos[qadr] / factor))
                        for aid, (qadr, _, _, factor) in self._ids.items()},
                        diagnostics=AdapterDiagnostics(kind="mujoco-joints", status="RUNNING" if self.enabled else "PAUSED",
                                                       output_enabled=self.enabled, details=self.observation()))

    def fresh(self) -> MujocoJointAdapter:
        """Create a disarmed simulation; the host retains reset/ownership authority."""
        return MujocoJointAdapter(self.config, self.root, clock=self.clock)

    def close(self) -> None:
        """Disable this in-memory adapter when a host replaces its simulation."""
        self.enabled = False
        self.data.ctrl[:] = 0
        self.remainder = 0.0

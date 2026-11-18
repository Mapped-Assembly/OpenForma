"""SO-101 contact-physics benchmark using a pinned external MuJoCo description.

MuJoCo is an optional dependency. This module performs no network access.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from .models import AdapterDiagnostics, AxisDefinition, AxisFeedback, Feedback, MachineDefinition

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
HOME_DEGREES = (0.0, -25.0, 40.0, 75.0, 90.0, 85.0)
CUBE_HALF = .015
SOURCE_M = np.array([.22, .025, CUBE_HALF])
TARGET_M = np.array([.20, -.10, CUBE_HALF])


def build_scene(directory: Path) -> Path:
    """Derive an explicit tabletop fixture without modifying upstream assets."""
    root = ET.parse(directory/"so101.xml").getroot()
    option = root.find("option")
    option.set("timestep", "0.002")
    option.set("iterations", "50")
    visual = root.find("visual")
    ET.SubElement(visual, "headlight", diffuse="0.7 0.7 0.7", ambient="0.4 0.4 0.4", specular="0.1 0.1 0.1")
    ET.SubElement(visual, "global", offwidth="640", offheight="480")
    world = root.find("worldbody")
    ET.SubElement(world, "light", pos="0.2 -0.3 1", dir="0 0 -1", directional="true")
    ET.SubElement(world, "geom", name="table", type="plane", size="0.5 0.5 0.02", rgba="0.15 0.21 0.26 1", friction="1 0.01 0.001")
    cube = ET.SubElement(world, "body", name="task_cube", pos=" ".join(map(str, SOURCE_M)))
    ET.SubElement(cube, "freejoint", name="cube_free")
    ET.SubElement(cube, "geom", name="task_cube", type="box", size=f"{CUBE_HALF} {CUBE_HALF} {CUBE_HALF}",
                  mass="0.025", rgba="1 0.35 0.08 1", friction="1.5 0.01 0.001", condim="6", solref="0.01 1")
    ET.SubElement(world, "geom", name="target_zone", type="cylinder", pos=f"{TARGET_M[0]} {TARGET_M[1]} 0.0005",
                  size="0.035 0.0005", rgba="0.1 0.7 0.45 0.65", contype="0", conaffinity="0")
    ET.SubElement(world, "camera", name="overview", pos="0.65 -0.55 0.48", xyaxes="0.75 0.66 0 -0.32 0.36 0.88", fovy="48")
    # Named gripper-frame site is upstream; retain the actual model's kinematics.
    scene = directory/"pick-place.xml"
    ET.indent(root)
    scene.write_text(ET.tostring(root, encoding="unicode")+"\n", encoding="utf-8")
    return scene


def export_scene_asset(scene: Path, destination: Path) -> None:
    """Export compiled visual geometry once; live transforms come from physics."""
    model = mujoco.MjModel.from_xml_path(str(scene))
    meshes = {}
    geoms = []
    for i in range(model.ngeom):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, i) or f"geom_{i}"
        if model.geom_group[i] not in (0, 1, 2):
            continue
        kind = int(model.geom_type[i])
        mesh_id = int(model.geom_dataid[i])
        if kind == int(mujoco.mjtGeom.mjGEOM_MESH) and str(mesh_id) not in meshes:
            start, n = int(model.mesh_vertadr[mesh_id]), int(model.mesh_vertnum[mesh_id])
            face, nf = int(model.mesh_faceadr[mesh_id]), int(model.mesh_facenum[mesh_id])
            meshes[str(mesh_id)] = dict(positions=np.round(model.mesh_vert[start:start+n]*1000, 4).flatten().tolist(),
                                       indices=model.mesh_face[face:face+nf].flatten().tolist())
        color = model.geom_rgba[i].copy()
        if model.geom_matid[i] >= 0:
            color = model.mat_rgba[model.geom_matid[i]].copy()
        geoms.append(dict(id=name, index=i, body=int(model.geom_bodyid[i]), type=kind, mesh_id=str(mesh_id),
                          position_mm=(model.geom_pos[i]*1000).tolist(), quaternion=model.geom_quat[i].tolist(),
                          size_mm=(model.geom_size[i]*1000).tolist(), color=color.tolist()))
    bodies = [dict(index=i, parent=int(model.body_parentid[i]), name=model.body(i).name) for i in range(1, model.nbody)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(format="forma.physics-scene", units="mm", meshes=meshes, bodies=bodies, geoms=geoms), separators=(",", ":")), encoding="utf-8")


class SO101Adapter:
    """Position-actuated, contact-only manipulation. Disarmed simulation is paused."""
    mode = "virtual"

    def __init__(self, scene: Path, *, clock=time.monotonic):
        self.scene = Path(scene)
        self.clock = clock
        self.model = mujoco.MjModel.from_xml_path(str(scene))
        self.data = mujoco.MjData(self.model)
        self.ik_data = mujoco.MjData(self.model)
        self.joints = {name: self.model.joint(name).id for name in JOINTS}
        self.qadr = np.array([self.model.jnt_qposadr[self.joints[name]] for name in JOINTS])
        self.dadr = np.array([self.model.jnt_dofadr[self.joints[name]] for name in JOINTS])
        self.actuators = np.array([self.model.actuator(name).id for name in JOINTS])
        self.tcp_id = self.model.site("gripperframe").id
        self.gripper_body = self.model.body("gripper").id
        self.moving_jaw_body = self.model.body("moving_jaw_so101_v1").id
        self.cube_body = self.model.body("task_cube").id
        self.cube_geom = self.model.geom("task_cube").id
        self.cube_qadr = int(self.model.jnt_qposadr[self.model.joint("cube_free").id])
        self.cube_dof = int(self.model.jnt_dofadr[self.model.joint("cube_free").id])
        self.enabled = False
        self.last_tick = self.clock()
        self.remainder = 0.0
        self.ever_lifted = False
        self.success = False
        self.settled_seconds = 0.0
        self.max_lift_m = 0.0
        self.contact_sides: set[str] = set()
        self.data.qpos[self.qadr] = np.radians(HOME_DEGREES)
        self.data.ctrl[self.actuators] = np.radians(HOME_DEGREES)
        mujoco.mj_forward(self.model, self.data)

    def definition(self) -> MachineDefinition:
        """Expose actual upstream joint/control limits in the shared degree API."""
        axes = []
        for i, name in enumerate(JOINTS):
            lower = max(self.model.jnt_range[self.joints[name], 0], self.model.actuator_ctrlrange[self.actuators[i], 0])
            upper = min(self.model.jnt_range[self.joints[name], 1], self.model.actuator_ctrlrange[self.actuators[i], 1])
            axes.append(AxisDefinition(axis_id=name, label=name.replace("_", " ").title(), unit="degree", minimum=math.degrees(lower),
                                       maximum=math.degrees(upper), neutral=HOME_DEGREES[i], max_rate=45 if name == "gripper" else 30))
        return MachineDefinition(machine_id="so101", name="SO-101 · Pick and Place", backend="virtual", axes=axes,
                                 model_asset="so101-scene.json", description="Six-axis manual control · MuJoCo gravity, contact and friction · place the orange cube on green.")

    def _advance(self) -> None:
        now = self.clock()
        elapsed = min(max(now-self.last_tick, 0), .02)
        self.last_tick = now
        if not self.enabled:
            return
        self.remainder += elapsed
        while self.remainder+1e-10 >= self.model.opt.timestep:
            mujoco.mj_step(self.model, self.data)
            self.remainder = max(0.0, self.remainder-self.model.opt.timestep)
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                raise RuntimeError("Nonfinite robot physics state")
            self._score()
        mujoco.mj_forward(self.model, self.data)

    def _score(self) -> None:
        self.contact_sides = set()
        for contact in self.data.contact:
            if self.cube_geom not in (contact.geom1, contact.geom2):
                continue
            other = contact.geom2 if contact.geom1 == self.cube_geom else contact.geom1
            body = int(self.model.geom_bodyid[other])
            if body == self.gripper_body:
                self.contact_sides.add("fixed")
            elif body == self.moving_jaw_body:
                self.contact_sides.add("moving")
        cube = self.data.xpos[self.cube_body]
        lift = float(cube[2]-CUBE_HALF)
        self.max_lift_m = max(self.max_lift_m, lift)
        if lift > .035 and len(self.contact_sides) == 2:
            self.ever_lifted = True
        slow = float(np.linalg.norm(self.data.qvel[self.cube_dof:self.cube_dof+3])) < .01 and float(np.linalg.norm(self.data.qvel[self.cube_dof+3:self.cube_dof+6])) < .1
        released = self.data.qpos[self.qadr[-1]] > math.radians(45) and not self.contact_sides
        on_target = float(np.linalg.norm(cube[:2]-TARGET_M[:2])) < .025 and abs(cube[2]-CUBE_HALF) < .008
        self.settled_seconds = self.settled_seconds+self.model.opt.timestep if self.ever_lifted and released and on_target and slow else 0.0
        if self.settled_seconds >= .5:
            self.success = True

    def apply(self, commands, enabled: bool) -> None:
        self._advance()
        if set(commands) != set(JOINTS):
            raise ValueError("SO-101 commands must cover all six named axes.")
        self.enabled = bool(enabled)
        if self.enabled:
            values = [commands[name] if commands[name] is not None else HOME_DEGREES[i] for i, name in enumerate(JOINTS)]
            self.data.ctrl[self.actuators] = np.radians(values)

    def observation(self) -> dict:
        """Ground-truth simulated task state, explicitly not real-world perception."""
        cube = self.data.xpos[self.cube_body]
        return dict(provenance="simulated-ground-truth", time_s=float(self.data.time),
                    engine_version=mujoco.__version__, paused=not self.enabled,
                    tcp_mm=(self.data.site_xpos[self.tcp_id]*1000).tolist(), cube_mm=(cube*1000).tolist(),
                    tcp_up=self.data.xmat[self.gripper_body].reshape(3, 3)[:, 2].tolist(),
                    cube_quaternion_wxyz=self.data.xquat[self.cube_body].tolist(),
                    cube_velocity_mm_s=(self.data.qvel[self.cube_dof:self.cube_dof+3]*1000).tolist(),
                    source_mm=(SOURCE_M*1000).tolist(), target_mm=(TARGET_M*1000).tolist(),
                    contact_sides=sorted(self.contact_sides), lifted=self.ever_lifted, max_lift_mm=self.max_lift_m*1000,
                    bilateral_contact=len(self.contact_sides) == 2,
                    placement_settled=self.settled_seconds >= .5,
                    settled_s=self.settled_seconds, success=self.success, grasp_assistance=False,
                    bodies=[dict(index=i, position_mm=(self.data.xpos[i]*1000).tolist(), rotation=self.data.xmat[i].tolist())
                            for i in range(1, self.model.nbody)])

    def read(self) -> Feedback:
        self._advance()
        return Feedback(mode="virtual", axes={name: AxisFeedback(simulated=math.degrees(self.data.qpos[self.qadr[i]])) for i, name in enumerate(JOINTS)},
                        diagnostics=AdapterDiagnostics(kind="mujoco-manipulation", status="RUNNING" if self.enabled else "PAUSED", output_enabled=self.enabled,
                                                       details=self.observation()))

    def inverse_kinematics(self, target_mm: list[float], *, seed=None) -> dict[str, float]:
        """Bounded numerical top-down IK; does not mutate the live simulation."""
        target = np.asarray(target_mm, dtype=float)/1000
        if target.shape != (3,) or not np.isfinite(target).all() or not (.06 <= target[0] <= .36 and -.23 <= target[1] <= .23 and .012 <= target[2] <= .35):
            raise ValueError("Tool target is outside the benchmark workspace.")
        d = self.ik_data
        d.qpos[:] = self.data.qpos
        if seed is not None:
            d.qpos[self.qadr[:5]] = np.radians([seed[name] for name in JOINTS[:5]])
        jacp = np.zeros((3, self.model.nv))
        jacr = np.zeros((3, self.model.nv))
        for _ in range(150):
            mujoco.mj_forward(self.model, d)
            position_error = target-d.site_xpos[self.tcp_id]
            up = d.xmat[self.gripper_body].reshape(3, 3)[:, 2]
            orientation_error = np.cross(up, np.array([0., 0., 1.]))
            if np.linalg.norm(position_error) < .0008 and np.linalg.norm(orientation_error) < .025 and up[2] > .9:
                return {name: math.degrees(d.qpos[self.qadr[i]]) for i, name in enumerate(JOINTS[:5])}
            mujoco.mj_jacSite(self.model, d, jacp, jacr, self.tcp_id)
            jac = np.vstack([jacp[:, self.dadr[:5]], .08*jacr[:, self.dadr[:5]]])
            error = np.concatenate([position_error, .08*orientation_error])
            delta = jac.T @ np.linalg.solve(jac @ jac.T + np.eye(6)*1e-5, error)
            d.qpos[self.qadr[:5]] += np.clip(delta, -.12, .12)
            for i, name in enumerate(JOINTS[:5]):
                d.qpos[self.qadr[i]] = np.clip(d.qpos[self.qadr[i]], *self.model.jnt_range[self.joints[name]])
        raise ValueError("No top-down IK solution within joint limits; move closer or use joint controls.")

    def close(self) -> None:
        """No renderer, background thread or external connection is allocated."""
        self.enabled = False

    def fresh(self):
        return type(self)(self.scene, clock=self.clock)

    def describe(self):
        import hashlib
        from .package import AdapterContract, ObservationSpec, digest
        files = [self.scene, *sorted((self.scene.parent/"assets").glob("*.stl"))]
        return AdapterContract(adapter_id="forma.so101-mujoco", mode=self.mode, evidence="contact-physics",
            configuration_sha256=digest({p.relative_to(self.scene.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}),
            actuators={name: self.model.actuator(int(self.actuators[i])).name for i, name in enumerate(JOINTS)},
            handlers=["move", "tool_move"], observations={
                name: ObservationSpec(path=[name], value_type="boolean")
                for name in ("bilateral_contact", "lifted", "success", "placement_settled")})

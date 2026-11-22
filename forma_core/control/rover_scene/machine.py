"""Forma actions backed by joint-limited IK and observed simulated object custody."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import mujoco
import numpy as np

from forma_core.control.models import (
    AdapterDiagnostics,
    AxisDefinition,
    AxisFeedback,
    Feedback,
    MachineDefinition,
)
from forma_core.control.package import (
    AdapterContract,
    CompletionRule,
    MachineAction,
    MachinePackage,
    ObservationCondition,
    ObservationSpec,
)
from forma_core.control.runtime import ControlError

from .kinematics import ArmKinematics
from .robot import leg_pose
from .scene import Scene


class Rover:
    """Bind one mounted arm and mobile base to a shared scene."""

    mode = "virtual"

    def __init__(self, world: Scene, machine_id: str) -> None:
        """Create an independent control contract from this instance and the scene slots."""
        if machine_id not in world.spec.machines:
            raise ValueError(f"Unknown machine: {machine_id}")
        if machine_id in world.adapters:
            raise ValueError(f"Machine already bound: {machine_id}")
        self.world, self.machine_id = world, machine_id
        self.model, self.data = world.model, world.data
        self.prefix = machine_id + "/"
        self.ik = ArmKinematics(self.model, self.prefix)
        self.root = int(self.model.joint(self.prefix + "root").qposadr[0])
        self.fingers = [
            int(self.model.joint(self.prefix + "arm_finger_" + side).qposadr[0]) for side in ("left", "right")
        ]
        self.enabled = False
        self.gait = 0.0
        self.intent: tuple[str, str] | None = None
        self.transport_height: float | None = None
        self.commands: dict[str, tuple[str, str]] = {}
        self.home = world.homes[machine_id]
        axes = [
            AxisDefinition(
                axis_id=name,
                label=name,
                unit="mm",
                minimum=float(world.aisle[0, i] * 1000),
                maximum=float(world.aisle[1, i] * 1000),
                neutral=float(self.home[i] * 1000),
                max_rate=550.0,
            )
            for i, name in enumerate(("base_x", "base_y"))
        ]
        self.arm = [f"arm_{i + 1}" for i in range(len(self.ik.joints))]
        for name, limits in zip(self.arm, self.ik.limits):
            low, high = np.degrees(limits)
            axes.append(
                AxisDefinition(
                    axis_id=name,
                    label=name,
                    unit="degree",
                    minimum=float(low),
                    maximum=float(high),
                    neutral=float(np.clip(0.0, low, high)),
                    max_rate=75.0,
                )
            )
        axes.append(
            AxisDefinition(
                axis_id="gripper",
                label="Jaw opening",
                unit="mm",
                minimum=0.0,
                maximum=35.0,
                neutral=28.0,
                max_rate=35.0,
            )
        )
        self.definition = MachineDefinition(
            machine_id=machine_id, name=world.spec.machines[machine_id].name, axes=axes
        )
        self.positions = {a.axis_id: a.neutral for a in axes}
        self._pose()
        self.actions: list[MachineAction] = []
        self._add(
            "return_home",
            "Return to own home pad",
            "home",
            "",
            {**self.base_targets(self.home), **{name: self.positions[name] for name in self.arm}},
            "at_home",
        )
        scratch = mujoco.MjData(self.model)
        for key, station in world.spec.stations.items():
            dock = world.docks[key]
            self._add(
                "visit_" + key, "Drive to " + station.name, "drive", key, self.base_targets(dock), "at_" + key
            )
            scratch.qpos[:] = self.data.qpos
            scratch.qpos[self.root : self.root + 2] = dock
            target = world.targets[key]
            high = self.ik.solve(scratch, target + [0, 0, 0.15], np.zeros(5))
            low = self.ik.solve(scratch, target, high)
            for verb, pose in (("lift", high), ("reach", low)):
                self._add(
                    verb + "_" + key,
                    verb.title() + " at " + station.name,
                    verb,
                    key,
                    dict(zip(self.arm, np.degrees(pose).tolist())),
                    verb + "_" + key,
                )
            self._add(
                "place_" + key,
                "Release held item onto " + station.name,
                "place",
                key,
                {"gripper": 28.0},
                "placed_" + key,
            )
        for key, item in world.spec.items.items():
            self._add(
                "pick_" + key, "Close jaws on " + item.name, "pick", key, {"gripper": 10.0}, "holding_" + key
            )
        fingerprint = hashlib.sha256()
        for field in (
            self.model.names,
            self.model.body_pos,
            self.model.body_quat,
            self.model.jnt_range,
            self.model.geom_pos,
            self.model.geom_size,
            self.model.mesh_vert,
            self.model.mesh_face,
        ):
            fingerprint.update(field if isinstance(field, bytes) else field.tobytes())
        self.signature = fingerprint.hexdigest()
        world.adapters[machine_id] = self

    @staticmethod
    def base_targets(xy: np.ndarray) -> dict[str, float]:
        """Convert MuJoCo meters into Forma's declared base axes."""
        return {"base_x": float(xy[0] * 1000), "base_y": float(xy[1] * 1000)}

    def _add(
        self, action_id: str, label: str, verb: str, key: str, targets: dict[str, float], condition: str
    ) -> None:
        """Register execution metadata and an independently observed completion rule."""
        self.commands[action_id] = (verb, key)
        rates = {a.axis_id: a.max_rate for a in self.definition.axes if a.axis_id in targets}
        self.actions.append(
            MachineAction(
                action_id=action_id,
                label=label,
                handler="move",
                axes=list(targets),
                targets=targets,
                rates=rates,
                completion=CompletionRule(
                    source="simulated",
                    tolerance=0.5,
                    dwell_s=0.1,
                    conditions=[ObservationCondition(observation=condition, operator="eq", value=True)],
                ),
            )
        )

    def _pose(self) -> None:
        """Set only this rover's joints and advance its kinematic attachment."""
        d = self.data
        p = self.positions
        leg_pose(self.model, d, self.gait, self.prefix)
        d.qpos[self.root : self.root + 7] = (p["base_x"] / 1000, p["base_y"] / 1000, -0.026, 0, 0, 0, 1)
        d.qpos[self.ik.qids] = np.radians([p[name] for name in self.arm])
        d.qpos[self.fingers] = p["gripper"] / 1000
        mujoco.mj_forward(self.model, d)
        tcp = d.site_xpos[self.ik.tcp]
        world = self.world
        grip = d.qpos[self.fingers[0]]
        if self.intent:
            verb, key = self.intent
            if verb == "pick" and grip <= 0.014 and key not in world.held:
                pos = d.mocap_pos[world.item_ids[key]]
                if np.linalg.norm(tcp - pos) < 0.008 and world.claim(self.machine_id, key):
                    self.transport_height = float(pos[2]) + 0.10
            if verb == "place" and grip >= 0.024 and np.linalg.norm(tcp - world.targets[key]) < 0.008:
                if world.release(self.machine_id, key):
                    self.transport_height = None
        held = self.carrying()
        if held:
            d.mocap_pos[world.item_ids[held]] = tcp
        mujoco.mj_forward(self.model, d)

    def carrying(self) -> str | None:
        """Return the ID held by this machine, if any."""
        return next((k for k, v in self.world.held.items() if v == self.machine_id), None)

    def apply(self, commands: dict[str, float | None], enabled: bool) -> None:
        """Apply host-interpolated commands while preserving frozen custody on Stop."""
        self.enabled = enabled
        if enabled:
            before = self.positions.copy()
            self.positions.update({k: v for k, v in commands.items() if v is not None})
            if before != self.positions:
                self.gait += (
                    math.hypot(
                        self.positions["base_x"] - before["base_x"],
                        self.positions["base_y"] - before["base_y"],
                    )
                    / 250
                )
                self._pose()

    def observations(self) -> dict[str, Any]:
        """Measure base, tool and object state from the authoritative world."""
        world = self.world
        tcp = self.data.site_xpos[self.ik.tcp]
        base = self.data.qpos[self.root : self.root + 2]
        result = {
            "execution": world.execution_contract(),
            "inventory": world.inventory(),
            "holding": self.carrying(),
            "tcp_m": tcp.tolist(),
            "base_m": base.tolist(),
            "at_home": bool(np.linalg.norm(base - self.home) < 0.004),
            "provenance": "kinematic proximity attachment",
        }
        for key, point in world.targets.items():
            result["at_" + key] = bool(np.linalg.norm(base - world.docks[key]) < 0.004)
            result["reach_" + key] = bool(np.linalg.norm(tcp - point) < 0.008)
            result["lift_" + key] = bool(np.linalg.norm(tcp - point - [0, 0, 0.15]) < 0.008)
            result["placed_" + key] = any(
                slot == key and np.linalg.norm(self.data.mocap_pos[world.item_ids[item]] - point) < 0.008
                for item, slot in world.locations.items()
            )
        for key in world.spec.items:
            result["holding_" + key] = world.held.get(key) == self.machine_id
        return result

    def preflight(self, action_id: str) -> tuple[str, str]:
        """Check station, custody and reach before changing a shared resource."""
        return self._check_action(action_id, self.observations())

    def _check_action(self, action_id: str, obs: dict[str, Any]) -> tuple[str, str]:
        """Apply the same capability checks to an observed pose for discovery and dispatch."""
        if action_id not in self.commands:
            raise ControlError("unknown_action")
        verb, key = self.commands[action_id]
        if verb in ("reach", "lift", "place") and not obs["at_" + key]:
            raise ControlError("visit_station_first")
        if verb == "home" and self.carrying():
            raise ControlError("place_held_item_before_parking")
        if (
            verb == "drive"
            and self.transport_height is not None
            and self.data.site_xpos[self.ik.tcp, 2] < self.transport_height
        ):
            raise ControlError("lift_held_item_before_driving")
        if verb == "pick":
            slot = self.world.locations[key]
            if self.carrying() or not slot or not obs["at_" + slot] or not obs["reach_" + slot]:
                raise ControlError("pick_requires_free_jaws_and_reached_item")
        if verb == "place" and (
            not self.carrying() or not obs["reach_" + key] or key in self.world.locations.values()
        ):
            raise ControlError("place_requires_held_item_and_reached_empty_slot")
        return verb, key

    def available_actions(self) -> list[str]:
        """List actions allowed by this rover's current custody and pose before route reservation."""
        available = []
        observations = self.observations()
        for action_id in self.commands:
            try:
                self._check_action(action_id, observations)
            except ControlError:
                continue
            available.append(action_id)
        return available

    def read(self) -> Feedback:
        """Read the simulated axes independently of command acknowledgements."""
        actual = self.base_targets(self.data.qpos[self.root : self.root + 2])
        actual.update(dict(zip(self.arm, np.degrees(self.data.qpos[self.ik.qids]).tolist())))
        actual["gripper"] = float(self.data.qpos[self.fingers[0]] * 1000)
        return Feedback(
            mode="virtual",
            axes={k: AxisFeedback(simulated=v) for k, v in actual.items()},
            diagnostics=AdapterDiagnostics(
                kind="rover-scene", status="ready", output_enabled=self.enabled, details=self.observations()
            ),
        )

    def describe(self) -> AdapterContract:
        """Bind the package's predicates to feedback channels on this instance."""
        actuators = {key: self.prefix + key for key in self.positions}
        actuators.update(
            base_x=self.prefix + "root/x",
            base_y=self.prefix + "root/y",
            gripper=self.prefix + "arm_finger_left",
        )
        return AdapterContract(
            adapter_id="forma.astra-so101.kinematic",
            mode="virtual",
            evidence="ideal-kinematics",
            configuration_sha256=self.signature,
            actuators=actuators,
            handlers=["move"],
            observations={
                condition.observation: ObservationSpec(path=[condition.observation], value_type="boolean")
                for action in self.actions
                for condition in action.completion.conditions
            },
        )

    def action_bindings(self) -> dict[str, dict[str, str | None]]:
        """Expose operation targets without requiring clients to parse generated action IDs."""
        return {
            action_id: {"operation": verb, "target_id": key or None}
            for action_id, (verb, key) in self.commands.items()
        }

    def package(self) -> MachinePackage:
        """Publish this machine's actual stations, actions and verification criteria."""
        return MachinePackage(
            definition=self.definition,
            model_source="external",
            adapter=self.describe(),
            actions=self.actions,
            operating_notes=[
                "Each station has one slot. Observe inventory to find an item and an empty destination.",
                "To transfer: visit source, reach source, pick item, lift at source, visit destination, reach destination, place at destination. Use the discovered action IDs. Return home only if requested.",
                "Observe the named item's final station and empty gripper before reporting delivery. Stop preserves inventory and held objects. Peer routes may be temporarily busy.",
                "All evidence is simulated kinematics. Objects attach on proximity with closed jaws.",
            ],
        )

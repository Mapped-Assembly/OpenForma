"""Versioned machine control contracts, independent of UI, transport and CAD kernel."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, TypeAdapter, model_validator


class ControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class JointBinding(ControlModel):
    joint_id: str = Field(min_length=1)
    kind: Literal["revolute", "prismatic"]
    parent_axis: str | None = None
    axis: list[FiniteFloat] = Field(min_length=3, max_length=3)
    origin_mm: list[FiniteFloat] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    sign: Literal[-1, 1] = 1
    body_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_vector(self):
        if not math.isclose(sum(v*v for v in self.axis), 1.0, abs_tol=1e-6):
            raise ValueError("Joint axis must be a unit vector.")
        if len(set(self.body_ids)) != len(self.body_ids):
            raise ValueError("Joint body IDs must be unique.")
        return self


class AxisDefinition(ControlModel):
    axis_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    label: str = Field(min_length=1)
    unit: Literal["degree", "mm"]
    minimum: FiniteFloat
    maximum: FiniteFloat
    neutral: FiniteFloat
    max_rate: FiniteFloat = Field(gt=0)
    component_ref: str | None = None
    joint: JointBinding | None = None

    @model_validator(mode="after")
    def valid_range(self):
        if not self.minimum < self.maximum or not self.minimum <= self.neutral <= self.maximum:
            raise ValueError("Axis needs an ordered range containing its neutral position.")
        if self.joint and (self.joint.kind == "revolute") != (self.unit == "degree"):
            raise ValueError("Revolute joints use degrees; prismatic joints use mm.")
        return self


class Timing(ControlModel):
    update_ms: int = Field(default=20, ge=1, le=100)
    telemetry_ms: int = Field(default=50, ge=10, le=1000)
    heartbeat_ms: int = Field(default=200, ge=20)
    lease_ms: int = Field(default=500, ge=50)
    link_loss_ms: int = Field(default=1000, ge=100)

    @model_validator(mode="after")
    def ordered(self):
        if not self.heartbeat_ms < self.lease_ms < self.link_loss_ms:
            raise ValueError("Require heartbeat < lease lifetime < link-loss timeout.")
        return self


class MachineDefinition(ControlModel):
    format: Literal["forma.control-definition"] = "forma.control-definition"
    version: Literal[1] = 1
    machine_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    description: str = ""
    source_project_id: str | None = None
    backend: Literal["virtual", "physical"] = "virtual"
    axes: list[AxisDefinition] = Field(min_length=1)
    timing: Timing = Field(default_factory=Timing)
    model_asset: str | None = None

    @model_validator(mode="after")
    def references(self):
        by_id = {a.axis_id: a for a in self.axes}
        if len(by_id) != len(self.axes):
            raise ValueError("Axis IDs must be unique.")
        bodies, joints = set(), set()
        for axis in self.axes:
            joint = axis.joint
            if not joint:
                continue
            if joint.joint_id in joints or bodies.intersection(joint.body_ids):
                raise ValueError("Each joint and CAD body must have one owner.")
            joints.add(joint.joint_id)
            bodies.update(joint.body_ids)
            path = {axis.axis_id}
            parent = joint.parent_axis
            while parent is not None:
                if parent not in by_id or not by_id[parent].joint:
                    raise ValueError("Joint parent must reference an axis with a CAD binding.")
                if parent in path:
                    raise ValueError("Joint parent graph contains a cycle.")
                path.add(parent)
                parent = by_id[parent].joint.parent_axis
        return self


class CommandBase(ControlModel):
    # v2 intentionally differs from the early workspace's angle-specific protocol.
    protocol_version: Literal[2]
    session_id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=0, le=9007199254740991)


class SimpleCommand(CommandBase):
    op: Literal["arm", "disarm", "heartbeat"]


class HoldCommand(CommandBase):
    op: Literal["hold"]
    axes: list[str] | None = Field(default=None, min_length=1)


class MoveCommand(CommandBase):
    op: Literal["move"]
    targets: dict[str, FiniteFloat] = Field(min_length=1)
    rates: dict[str, FiniteFloat] = Field(default_factory=dict)
    coordinated: bool = False


class JogCommand(CommandBase):
    op: Literal["jog"]
    velocities: dict[str, FiniteFloat] = Field(min_length=1)


class ActionCommand(CommandBase):
    op: Literal["action"]
    action_id: str = Field(min_length=1)
    parameters: dict[str, FiniteFloat] = Field(default_factory=dict)


Command = Annotated[SimpleCommand | HoldCommand | MoveCommand | JogCommand | ActionCommand, Field(discriminator="op")]
COMMAND_ADAPTER: TypeAdapter = TypeAdapter(Command)


class AxisFeedback(ControlModel):
    simulated: FiniteFloat | None = None
    measured: FiniteFloat | None = None


class AdapterDiagnostics(ControlModel):
    kind: str
    status: str
    output_enabled: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class Feedback(ControlModel):
    mode: Literal["virtual", "physical"]
    axes: dict[str, AxisFeedback]
    fault: str | None = None
    diagnostics: AdapterDiagnostics | None = None

    @model_validator(mode="after")
    def provenance(self):
        for value in self.axes.values():
            if self.mode == "virtual" and value.measured is not None:
                raise ValueError("A virtual adapter cannot claim measured feedback.")
            if self.mode == "physical" and value.simulated is not None:
                raise ValueError("A physical adapter cannot claim simulated feedback.")
        return self

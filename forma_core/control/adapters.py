"""Execution boundary; a hardware adapter must implement its own local watchdog."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol

from .models import AxisFeedback, Feedback, MachineDefinition


class MachineAdapter(Protocol):
    mode: Literal["virtual", "physical"]

    def apply(self, commands: Mapping[str, float | None], enabled: bool) -> None: ...

    def read(self) -> Feedback: ...


class VirtualAdapter:
    """Ideal kinematics only. Disabled position is unknown; there is no gravity solver."""
    mode: Literal["virtual"] = "virtual"

    def __init__(self, definition: MachineDefinition):
        self.positions: dict[str, float | None] = {a.axis_id: None for a in definition.axes}

    def apply(self, commands: Mapping[str, float | None], enabled: bool) -> None:
        if set(commands) != set(self.positions):
            raise ValueError("Adapter commands must cover its defined axes.")
        self.positions = {key: value if enabled else None for key, value in commands.items()}

    def read(self) -> Feedback:
        return Feedback(mode=self.mode, axes={key: AxisFeedback(simulated=value) for key, value in self.positions.items()})

    def describe(self):
        from .package import AdapterContract
        return AdapterContract(adapter_id="forma.ideal", mode=self.mode, evidence="ideal-kinematics",
                               actuators={key: key for key in self.positions}, handlers=["move"])

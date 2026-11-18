"""Control adapter: sampled setpoints -> native firmware -> simulated I/O/plant."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import time

from forma_core.control.models import AdapterDiagnostics, AxisFeedback, Feedback, MachineDefinition
from .simulator import FirmwareProfile, NativeSimulator


class FirmwareSilAdapter:
    mode = "virtual"

    def __init__(self, definition: MachineDefinition, profile: FirmwareProfile, library: Path, *, clock=time.monotonic):
        if definition.backend != "virtual":
            raise ValueError("Firmware SIL always reports virtual feedback.")
        self.definition, self.profile, self.library = definition, profile, Path(library)
        self.clock, self.started = clock, clock()
        self.native = NativeSimulator(self.library, definition.axes, profile)
        self.sequence = 0
        self.link_connected = True
        self.injections = {a.axis_id: dict(stalled=False, minimum=False, maximum=False) for a in definition.axes}

    def _now(self):
        return round((self.clock()-self.started)*1000)

    def apply(self, commands: Mapping[str, float | None], enabled: bool):
        if set(commands) != set(self.injections):
            raise ValueError("Firmware commands must cover its defined axes.")
        now = self._now()
        self.native.tick(now)
        if not self.link_connected:
            return
        values = [commands[a.axis_id] if commands[a.axis_id] is not None else a.neutral for a in self.definition.axes]
        self.sequence += 1
        packet = f"F1 {self.sequence} {int(enabled)} {len(values)} " + " ".join(format(v, ".12g") for v in values) + "\n"
        self.native.feed(packet.encode("ascii"), now)

    def read(self):
        self.native.tick(self._now())
        native = self.native.snapshot()
        enabled = bool(native["enabled"])
        details = dict(native, link_connected=self.link_connected, watchdog_ms=self.profile.watchdog_ms,
                       source_sha256=self.native.manifest["source_sha256"], assumptions=self.profile.assumptions)
        for axis, values in details["axes"].items():
            drive = self.profile.axes[axis]
            values.update(drive=drive.kind, steps_per_unit=drive.steps_per_unit if drive.kind == "stepper" else None,
                          pwm_period_us=20000 if drive.kind == "servo" else None)
        return Feedback(mode="virtual", fault=native["fault"],
                        axes={a: AxisFeedback(simulated=value["simulated"] if enabled else None) for a, value in native["axes"].items()},
                        diagnostics=AdapterDiagnostics(kind="native-firmware-sil", status="FAULT" if native["fault"] else "RUNNING" if enabled else "DISABLED",
                                                       output_enabled=enabled, details=details))

    def inject(self, axis: str, **changes):
        if axis not in self.injections or not set(changes) <= {"stalled", "minimum", "maximum"}:
            raise ValueError("Unknown simulated I/O injection.")
        self.injections[axis].update(changes)
        self.native.inject(axis, **self.injections[axis])

    def fresh(self):
        return type(self)(self.definition, self.profile, self.library, clock=self.clock)

    def describe(self):
        from forma_core.control.package import AdapterContract, digest
        return AdapterContract(adapter_id="forma.firmware-sil", mode=self.mode, evidence="firmware-sil",
                               configuration_sha256=digest(dict(profile=self.profile.model_dump(mode="json"), firmware=self.native.manifest["source_sha256"])),
                               actuators={axis.axis_id: f"native:{i}" for i, axis in enumerate(self.definition.axes)}, handlers=["move"])

    def close(self):
        self.native.close()

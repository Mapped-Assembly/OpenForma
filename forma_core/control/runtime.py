"""Deterministic multi-axis runtime; callers serialize access on their control loop.

No network, CAD or LLM dependency. All accepted commands use a backend clock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
import uuid

from pydantic import ValidationError

from .adapters import MachineAdapter, VirtualAdapter
from .models import COMMAND_ADAPTER, Feedback, HoldCommand, JogCommand, MachineDefinition, MoveCommand


class ControlError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class MotionPlan:
    starts: dict[str, float]
    targets: dict[str, float]
    duration: float
    elapsed: float = 0.0


class MachineRuntime:
    def __init__(self, definition: MachineDefinition, adapter: MachineAdapter | None = None, *, clock=time.monotonic):
        if adapter is None and definition.backend != "virtual":
            raise ValueError("Physical machines require an explicit hardware adapter.")
        self.definition = definition
        self.adapter = adapter if adapter is not None else VirtualAdapter(definition)
        if self.adapter.mode != definition.backend:
            raise ValueError("Adapter mode differs from the machine definition.")
        self.clock = clock
        self.started = self.last_tick = self.last_valid = clock()
        self.axes = {a.axis_id: a for a in definition.axes}
        self.commanded: dict[str, float | None] = {a: None for a in self.axes}
        self.targets: dict[str, float | None] = {a: None for a in self.axes}
        self.plans: list[MotionPlan] = []
        self.state = "DISARMED"
        self.owner: dict | None = None
        self.last_seq: int | None = None
        self.leases: dict[str, float] = {}
        self.latest_lease: str | None = None
        self.lease_issued = float("-inf")
        self.events: deque[dict] = deque(maxlen=200)
        self.event_id = 0
        self.feedback = Feedback(mode=definition.backend, axes={})
        self._output()
        self._event("boot", "Runtime ready; output disabled.")

    def _event(self, kind, message, **values):
        self.event_id += 1
        self.events.append(dict(id=self.event_id, uptime_ms=round((self.clock()-self.started)*1000),
                                kind=kind, message=message, **values))

    @property
    def enabled(self):
        return self.state not in ("DISARMED", "FAULT")

    def _output(self):
        try:
            self.adapter.apply(self.commanded, self.enabled)
            feedback = self.adapter.read()
            # Validate even when an extension returns an unvalidated mapping/model.
            if isinstance(feedback, Feedback):
                feedback = feedback.model_dump(mode="json")
            feedback = Feedback.model_validate(feedback)
            if feedback.mode != self.definition.backend or set(feedback.axes) != set(self.axes):
                raise ValueError("Adapter returned the wrong mode or axis set.")
            self.feedback = feedback
            if feedback.fault:
                first = self.state != "FAULT"
                self.state = "FAULT"
                self.plans.clear()
                self.targets = {a: None for a in self.axes}
                self.adapter.apply(self.commanded, False)
                if first:
                    self._event("fault", feedback.fault)
                return False
        except Exception as error:
            self.state = "FAULT"
            self.plans.clear()
            self.targets = {a: None for a in self.axes}
            self.feedback = Feedback(mode=self.definition.backend, axes={})
            try:
                self.adapter.apply(self.commanded, False)
            except Exception:
                pass
            self._event("fault", f"Adapter failed: {type(error).__name__}. Restart after correcting adapter.")
            return False
        return True

    def _require_owner(self, session_id):
        if not self.owner or self.owner["session_id"] != session_id:
            raise ControlError("wrong_session")

    def replace_adapter(self, adapter: MachineAdapter):
        """Explicit execution reset while released/disabled; never resumes a plan."""
        if self.owner or self.enabled:
            raise ControlError("release_before_adapter_reset")
        if adapter.mode != self.definition.backend:
            raise ValueError("Adapter mode differs from machine definition.")
        self.adapter = adapter
        self.commanded = {a: None for a in self.axes}
        self.targets = dict(self.commanded)
        self.plans.clear()
        self.state = "DISARMED"
        self.last_tick = self.last_valid = self.clock()
        self._output()
        self._event("reset", "Execution reset; acquire control and explicitly Arm.")

    def claim(self, name: str, *, kind: str = "human", takeover: bool = False):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or kind not in ("human", "agent", "joystick"):
            raise ControlError("invalid_owner")
        self.tick()
        if self.owner and not takeover:
            raise ControlError("control_busy")
        self.plans.clear()
        if self.enabled:
            self.state, self.targets = "LINK_LOST", dict(self.commanded)
        self.owner = dict(session_id=uuid.uuid4().hex, name=name, kind=kind)
        self.last_seq = None
        self.last_valid = self.clock()
        self.leases.clear()
        self.latest_lease = None
        self.lease_issued = float("-inf")
        self._event("ownership", f"{name} acquired control; explicit Arm required.")
        return dict(session_id=self.owner["session_id"], **self.lease(self.owner["session_id"]))

    def lease(self, session_id):
        self._require_owner(session_id)
        now = self.clock()
        self.leases = {key: end for key, end in self.leases.items() if end > now}
        if now-self.lease_issued >= self.definition.timing.heartbeat_ms/1000 or self.latest_lease not in self.leases:
            self.latest_lease = uuid.uuid4().hex
            self.lease_issued = now
            self.leases[self.latest_lease] = now+self.definition.timing.lease_ms/1000
        return dict(lease_id=self.latest_lease, valid_for_ms=max(0, round((self.leases[self.latest_lease]-now)*1000)))

    def release(self, session_id):
        self._require_owner(session_id)
        self.plans.clear()
        if self.state != "FAULT":
            self.state = "DISARMED"
        self.targets = {a: None for a in self.axes}
        self._output()
        self.owner = None
        self.leases.clear()
        self.last_seq = None
        self._event("ownership", "Control released; output disabled.")

    def _axis_ids(self, ids):
        if not set(ids) <= self.axes.keys():
            raise ControlError("unknown_axis")

    def _cancel(self, ids):
        """Interrupting one member freezes the entire old coordinated group."""
        ids = set(ids)
        kept = []
        for plan in self.plans:
            if ids.intersection(plan.targets):
                for axis in plan.targets:
                    self.targets[axis] = self.commanded[axis]
            else:
                kept.append(plan)
        self.plans = kept
        for axis in ids:
            self.targets[axis] = self.commanded[axis]

    def _move(self, targets, rates, coordinated):
        self._cancel(targets)
        groups = [targets] if coordinated else [{a: value} for a, value in targets.items()]
        for group in groups:
            starts = {a: self.commanded[a] for a in group}
            duration = max(abs(group[a]-starts[a])/rates[a] for a in group)
            self.targets.update(group)
            if duration > 0:
                self.plans.append(MotionPlan(starts, dict(group), duration))
        self.state = "ARMED"

    def command(self, packet):
        self.tick()
        try:
            try:
                command = COMMAND_ADAPTER.validate_python(packet)
            except ValidationError:
                raise ControlError("invalid_command") from None
            self._require_owner(command.session_id)
            if self.leases.get(command.lease_id, float("-inf")) <= self.clock():
                raise ControlError("expired_or_unknown_lease")
            if self.last_seq is not None and command.seq <= self.last_seq:
                raise ControlError("stale_sequence")
            if self.state == "FAULT":
                raise ControlError("adapter_fault")
            if command.op in ("move", "jog", "hold") and self.state not in ("ARMED", "HOLD"):
                raise ControlError("invalid_state")
            if isinstance(command, MoveCommand):
                self._axis_ids(command.targets)
                if not command.rates.keys() <= command.targets.keys():
                    raise ControlError("rate_without_target")
                rates = {a: command.rates.get(a, self.axes[a].max_rate) for a in command.targets}
                for a, value in command.targets.items():
                    if not self.axes[a].minimum <= value <= self.axes[a].maximum:
                        raise ControlError("position_limit")
                    if not 0 < rates[a] <= self.axes[a].max_rate:
                        raise ControlError("rate_limit")
                self._move(command.targets, rates, command.coordinated)
            elif isinstance(command, JogCommand):
                self._axis_ids(command.velocities)
                if any(abs(v) > self.axes[a].max_rate for a, v in command.velocities.items()):
                    raise ControlError("rate_limit")
                self._cancel(command.velocities)
                targets = {a: self.axes[a].maximum if v > 0 else self.axes[a].minimum
                           for a, v in command.velocities.items() if v != 0}
                if targets:
                    self._move(targets, {a: abs(command.velocities[a]) for a in targets}, False)
                elif not self.plans:
                    self.state = "HOLD"
            elif isinstance(command, HoldCommand):
                ids = command.axes if command.axes is not None else list(self.axes)
                self._axis_ids(ids)
                self._cancel(ids)
                if not self.plans:
                    self.state = "HOLD"
            elif command.op == "arm" and self.state != "ARMED":
                self.plans.clear()
                self.commanded = {a: value if value is not None else self.axes[a].neutral for a, value in self.commanded.items()}
                self.targets, self.state = dict(self.commanded), "ARMED"
            elif command.op == "disarm":
                self.plans.clear()
                self.targets, self.state = {a: None for a in self.axes}, "DISARMED"
            if not self._output():
                raise ControlError("adapter_fault")
            self.last_seq, self.last_valid = command.seq, self.clock()
            if command.op != "heartbeat":
                self._event("command", command.op, seq=command.seq, targets=dict(self.targets))
            return dict(accepted=True, ack_seq=self.last_seq, telemetry=self.snapshot())
        except ControlError as error:
            self._event("rejected", error.reason)
            raise

    def tick(self):
        now = self.clock()
        dt = min(max(0, now-self.last_tick), self.definition.timing.update_ms/1000)
        self.last_tick = now
        if self.state == "FAULT":
            if self.feedback.fault:
                self._output()
            return
        if self.state in ("ARMED", "HOLD") and now-self.last_valid >= self.definition.timing.link_loss_ms/1000:
            self.plans.clear()
            self.targets, self.state = dict(self.commanded), "LINK_LOST"
            self._event("link_loss", "Command traffic timed out; explicit Arm required.")
        if self.state == "ARMED":
            pending = []
            for plan in self.plans:
                plan.elapsed = min(plan.duration, plan.elapsed+dt)
                progress = plan.elapsed/plan.duration
                for axis, target in plan.targets.items():
                    self.commanded[axis] = target if progress >= 1 else plan.starts[axis]+(target-plan.starts[axis])*progress
                if progress < 1:
                    pending.append(plan)
            self.plans = pending
        self._output()

    def snapshot(self):
        axes = {}
        for axis, definition in self.axes.items():
            feedback = self.feedback.axes.get(axis)
            axes[axis] = dict(unit=definition.unit, commanded=self.commanded[axis], target=self.targets[axis],
                              simulated=feedback.simulated if feedback else None, measured=feedback.measured if feedback else None)
        return dict(protocol_version=2, machine_id=self.definition.machine_id, backend=self.definition.backend,
                    state=self.state, output_enabled=self.enabled, axes=axes, ack_seq=self.last_seq,
                    owner=dict(self.owner) if self.owner else None,
                    uptime_ms=round((self.clock()-self.started)*1000),
                    active_groups=[list(p.targets) for p in self.plans],
                    command_motion_complete=self.enabled and not self.plans,
                    execution=self.feedback.diagnostics.model_dump(mode="json") if self.feedback.diagnostics else None,
                    execution_fault=self.feedback.fault)


class MachineRegistry:
    """One owner per machine; registering a second machine does not change the first."""
    def __init__(self):
        self._machines: dict[str, MachineRuntime] = {}

    def add(self, runtime: MachineRuntime):
        key = runtime.definition.machine_id
        if key in self._machines:
            raise ValueError(f"Machine '{key}' is already registered.")
        self._machines[key] = runtime

    def get(self, machine_id: str) -> MachineRuntime:
        if machine_id not in self._machines:
            raise ControlError("unknown_machine")
        return self._machines[machine_id]

    def all(self) -> tuple[MachineRuntime, ...]:
        return tuple(self._machines.values())

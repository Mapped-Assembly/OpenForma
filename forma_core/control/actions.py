"""Declared action dispatch and feedback evidence, owned by the machine loop."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import math
import uuid

from .models import MoveCommand
from .package import MachinePackage


class ActionExecution:
    def __init__(self, package: MachinePackage, adapter):
        self.package = MachinePackage.model_validate_json(package.model_dump_json())
        self.bind(adapter)
        self.actions = {action.action_id: action for action in self.package.actions}
        self.records = OrderedDict()
        self.current = None
        self.satisfied_since = None

    def bind(self, adapter):
        if not hasattr(adapter, "describe") or adapter.describe() != self.package.adapter:
            raise ValueError("Installed adapter contract differs from the machine package binding.")
        if "tool_move" in self.package.adapter.handlers and not callable(getattr(adapter, "inverse_kinematics", None)):
            raise ValueError("Bound adapter has no tool IK implementation.")

    def prepare(self, command, adapter):
        from .runtime import ControlError
        action = self.actions.get(command.action_id)
        if action is None:
            raise ControlError("unsupported_action")
        if set(command.parameters) != set(action.parameters):
            raise ControlError("action_parameters_mismatch")
        for key, value in command.parameters.items():
            p = action.parameters[key]
            if not p.minimum <= value <= p.maximum:
                raise ControlError("action_parameter_limit")
        if action.handler == "tool_move":
            try:
                targets = adapter.inverse_kinematics([command.parameters[p] for p in action.tool_parameters])
            except ValueError as error:
                raise ControlError(str(error)) from None
        else:
            targets = dict(action.targets)
            targets.update({axis: command.parameters[p] for p, axis in action.parameter_axes.items()})
        if set(targets) != set(action.axes):
            raise ControlError("action_binding_axis_mismatch")
        move = MoveCommand(protocol_version=2, session_id=command.session_id, lease_id=command.lease_id,
                           seq=command.seq, op="move", targets=targets, rates=action.rates, coordinated=True)
        return action, move

    def begin(self, command, action, targets, runtime):
        self.interrupt("superseded", runtime)
        run_id = uuid.uuid4().hex
        record = dict(run_id=run_id, action_id=action.action_id, command_seq=command.seq,
            owner_id=runtime.owner["owner_id"], status="running", reason=None,
            parameters=dict(command.parameters), targets=dict(targets),
            started_uptime_ms=round((runtime.clock()-runtime.started)*1000), completed_uptime_ms=None,
            package_sha256=self.package.sha256, adapter_id=self.package.adapter.adapter_id,
            configuration_sha256=self.package.adapter.configuration_sha256,
            adapter_version=self.package.adapter.version, provenance=self.package.adapter.evidence,
            feedback_source=action.completion.source, evidence=None)
        self.records[run_id] = record
        while len(self.records) > 100:
            self.records.popitem(last=False)
        self.current = run_id
        self.start_time = runtime.clock()
        self.initial_feedback = runtime.feedback_sequence
        self.satisfied_since = None
        return run_id

    def finish(self, status, reason, runtime, evidence=None):
        record = self.records.get(self.current)
        if record is not None and record["status"] == "running":
            record.update(status=status, reason=reason, completed_uptime_ms=round((runtime.clock()-runtime.started)*1000), evidence=evidence)
            runtime._event("action", f"{record['action_id']}: {status}", run_id=record["run_id"])
        self.satisfied_since = None

    def interrupt(self, reason, runtime):
        self.finish("interrupted", reason, runtime)

    def observe(self, runtime):
        record = self.records.get(self.current)
        if record is None or record["status"] != "running":
            return
        if runtime.state == "FAULT":
            self.finish("failed", "adapter_fault", runtime)
            return
        if runtime.state not in ("ARMED", "HOLD"):
            self.interrupt(runtime.state.lower(), runtime)
            return
        action = self.actions[record["action_id"]]
        rule = action.completion
        now = runtime.clock()
        # Completion requires a fresh successful adapter read after dispatch.
        if runtime.feedback_sequence <= self.initial_feedback:
            return
        positions = {aid: getattr(runtime.feedback.axes.get(aid), rule.source, None) for aid in record["targets"]}
        matches = not runtime.plans
        if rule.positions:
            matches = matches and all(value is not None and abs(value-record["targets"][aid]) <= rule.tolerance for aid, value in positions.items())
        observations = {}
        for condition in rule.conditions:
            spec = self.package.adapter.observations[condition.observation]
            value = runtime.feedback.diagnostics.details if runtime.feedback.diagnostics else None
            for part in spec.path:
                value = value.get(part) if isinstance(value, dict) else None
            observations[condition.observation] = value
            valid = isinstance(value, bool) if spec.value_type == "boolean" else isinstance(value, str) if spec.value_type == "string" else isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
            if not valid:
                matches = False
            elif condition.operator == "eq":
                matches = matches and value == condition.value
            elif condition.operator == "gte":
                matches = matches and value >= condition.value
            else:
                matches = matches and value <= condition.value
        if now-self.start_time >= rule.timeout_s:
            self.finish("timed_out", "feedback_completion_timeout", runtime,
                        dict(feedback_sequence=runtime.feedback_sequence, positions=positions, observations=observations))
            runtime.plans.clear()
            runtime.targets = dict(runtime.commanded)
            runtime.state = "HOLD"
        elif matches:
            if self.satisfied_since is None:
                self.satisfied_since = now
            if now-self.satisfied_since+1e-9 >= rule.dwell_s:
                self.finish("completed", "feedback_criteria_satisfied", runtime,
                    dict(feedback_sequence=runtime.feedback_sequence, positions=positions, observations=observations,
                         dwell_s=now-self.satisfied_since, command_motion_complete=True))
        else:
            self.satisfied_since = None

    def snapshot(self, run_id=None):
        return deepcopy(self.records.get(run_id or self.current))

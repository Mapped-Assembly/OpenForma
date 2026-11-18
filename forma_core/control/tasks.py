"""Persistent, feedback-backed machine tasks. Host calls on its control event loop.

The injected agent decides actions; this runner owns leases, heartbeats, evidence
and cancellation. It never substitutes a scripted policy for a failed agent.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Protocol
import uuid

from .runtime import ControlError, MachineRegistry

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class TaskAgent(Protocol):
    async def run(self, task: dict, invoke, progress) -> None: ...


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    record: dict
    session: str | None = None
    sequence: int = 0
    job: asyncio.Task | None = None
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MachineTaskRunner:
    def __init__(self, registry: MachineRegistry, agent: TaskAgent, directory: Path, *, timeout_s=300):
        self.registry, self.agent, self.directory = registry, agent, Path(directory)
        self.timeout_s = timeout_s
        self.tasks: dict[str, Task] = {}
        self.latest: dict[str, str] = {}
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime):
            record = json.loads(path.read_text(encoding="utf-8"))
            task = Task(record)
            self.tasks[record["task_id"]] = task
            self.latest[record["machine_id"]] = record["task_id"]
            if record["status"] not in TERMINAL:
                record.update(status="interrupted", reason="host_restarted", finished_at=timestamp())
                self._event(task, "interrupted", "Host restarted; execution was not resumed.")

    def _save(self, task):
        path = self.directory/(task.record["task_id"]+".json")
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(task.record, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        temp.replace(path)

    def _event(self, task, kind, message, **fields):
        record = task.record
        record["revision"] += 1
        record["events"].append(dict(id=record["revision"], time=timestamp(), kind=kind, message=message, **fields))
        record["events"] = record["events"][-500:]
        self._save(task)

    def snapshot(self, machine_id, *, full=False):
        task_id = self.latest.get(machine_id)
        return self.get(task_id, full=full) if task_id else None

    def get(self, task_id, *, full=True):
        if task_id not in self.tasks:
            raise ControlError("unknown_task")
        record = self.tasks[task_id].record
        if full:
            return deepcopy(record)
        result = {k: v for k, v in record.items() if k not in {"package", "actions", "initial_observation", "final_observation", "events"}}
        result["events"] = [{k: v for k, v in event.items() if k not in {"observation", "evidence"}} for event in record["events"][-50:]]
        return deepcopy(result)

    def _machine(self, task):
        return self.registry.get(task.record["machine_id"])

    def _require_active(self, task):
        if task.record["status"] in TERMINAL:
            raise ControlError("task_not_active")
        machine = self._machine(task)
        if not machine.owner or machine.owner["session_id"] != task.session:
            raise ControlError("task_ownership_lost")
        return machine

    def _command(self, task, op, **values):
        machine = self._require_active(task)
        lease = machine.lease(task.session)
        task.sequence += 1
        return machine.command(dict(protocol_version=2, session_id=task.session,
            lease_id=lease["lease_id"], seq=task.sequence, op=op, **values))

    def observe(self, task):
        state = self._machine(task).snapshot()
        execution = state["execution"]
        return dict(machine_id=state["machine_id"], state=state["state"], uptime_ms=state["uptime_ms"],
            axes=state["axes"], execution_fault=state["execution_fault"],
            provenance=task.record["provenance"],
            observations={k: v for k, v in (execution["details"] if execution else {}).items() if k != "bodies"})

    def start(self, machine_id, goal, *, session_id=None, reset=False, model=None):
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
            raise ControlError("invalid_task_goal")
        machine = self.registry.get(machine_id)
        previous = self.snapshot(machine_id)
        if previous and previous["status"] not in TERMINAL:
            raise ControlError("task_already_running")
        if machine.action_execution is None:
            raise ControlError("machine_has_no_action_contract")
        if machine.owner:
            if session_id is None:
                raise ControlError("control_busy")
            machine._require_owner(session_id)
        if reset and (machine.definition.backend != "virtual" or not callable(getattr(machine.adapter, "fresh", None))):
            raise ControlError("task_reset_unavailable")
        if machine.state == "FAULT" and not reset:
            raise ControlError("adapter_fault")
        # Validate a replacement before relinquishing the existing owner.
        fresh = machine.adapter.fresh() if reset else None
        if fresh is not None:
            try:
                machine.action_execution.bind(fresh)
            except Exception:
                if hasattr(fresh, "close"):
                    fresh.close()
                raise
        if machine.owner:
            machine.release(session_id)
        if fresh is not None:
            old = machine.adapter
            machine.replace_adapter(fresh)
            if hasattr(old, "close"):
                old.close()
        owner = machine.claim("Task agent", kind="agent")
        package = machine.action_execution.package
        record = dict(format="forma.machine-task", version=1, task_id=uuid.uuid4().hex,
            machine_id=machine_id, goal=goal.strip(), status="starting", reason=None, summary=None,
            created_at=timestamp(), finished_at=None, revision=0, events=[], actions=[],
            action_count=0, heartbeat_count=0, model=model, agent_session_id=None,
            package_sha256=package.sha256, package=package.model_dump(mode="json"),
            provenance=package.adapter.evidence, reset_fixture=reset)
        task = Task(record, session=owner["session_id"])
        self.tasks[record["task_id"]] = task
        self.latest[machine_id] = record["task_id"]
        record["initial_observation"] = self.observe(task)
        try:
            self._event(task, "starting", "Starting task worker; machine control acquired.")
            task.job = asyncio.create_task(self._run(task))
        except Exception:
            machine.release(task.session)
            task.session = None
            raise
        return self.get(record["task_id"], full=False)

    def _finish(self, task, status, reason, summary=None):
        if task.record["status"] in TERMINAL:
            return
        machine = self._machine(task)
        if machine.owner and machine.owner["session_id"] == task.session:
            machine.release(task.session)
        task.session = None
        # Capture an interrupted in-flight action before its coroutine is cancelled.
        current = machine.action_execution.snapshot()
        if current and any(a.get("run_id") == current["run_id"] for a in task.record["actions"]):
            for action in task.record["actions"]:
                if action.get("run_id") == current["run_id"]:
                    action["evidence"] = current
        task.record.update(status=status, reason=reason, summary=summary, finished_at=timestamp(), final_observation=self.observe(task))
        self._event(task, status, summary or reason)

    def cancel(self, machine_id, *, reason="stopped_by_user"):
        task_id = self.latest.get(machine_id)
        if task_id:
            task = self.tasks[task_id]
            if task.record["status"] not in TERMINAL:
                self._finish(task, "cancelled" if reason == "stopped_by_user" else "interrupted", reason)
                if task.job:
                    task.job.cancel()
        return self.snapshot(machine_id)

    async def close(self):
        for machine_id in list(self.latest):
            self.cancel(machine_id, reason="host_shutdown")
        await asyncio.gather(*(t.job for t in self.tasks.values() if t.job), return_exceptions=True)

    async def _heartbeat(self, task):
        interval = self._machine(task).definition.timing.heartbeat_ms / 1000 * .75
        while True:
            await asyncio.sleep(interval)
            if task.record["status"] in TERMINAL:
                return
            try:
                self._command(task, "heartbeat")
                task.record["heartbeat_count"] += 1
            except Exception as error:
                self._finish(task, "interrupted", str(error))
                task.job.cancel()
                return

    async def _run(self, task):
        heartbeat = asyncio.create_task(self._heartbeat(task))
        try:
            async with asyncio.timeout(self.timeout_s):
                await self.agent.run(deepcopy(task.record),
                    lambda name, inputs: self.tool(task, name, inputs),
                    lambda kind, data: self.progress(task, kind, data))
            if task.record["status"] not in TERMINAL:
                self._finish(task, "failed", "agent_ended_without_verified_result")
        except asyncio.CancelledError:
            self._finish(task, "interrupted", "worker_cancelled")
        except TimeoutError:
            self._finish(task, "failed", "task_timeout")
        except Exception as error:
            self._finish(task, "failed", str(error))
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    def progress(self, task, kind, data):
        self._require_active(task)
        if kind == "ready":
            task.record.update(model=data.get("model"), agent_session_id=data.get("session_id"), status="running")
            self._command(task, "arm")
            self._event(task, "ready", "Agent connected; machine armed.")
        elif kind == "thinking":
            self._event(task, "thinking", "Agent deciding the next step; heartbeat maintained.")

    async def tool(self, task, name, inputs):
        machine = self._require_active(task)
        if not isinstance(inputs, dict):
            raise ControlError("invalid_task_tool_input")
        if name == "inspect_machine":
            self._event(task, "inspect", "Agent inspected capabilities and observations.")
            return dict(package=task.record["package"], current=self.observe(task))
        if name == "observe":
            observation = self.observe(task)
            self._event(task, "observe", str(inputs.get("note", "Agent checked feedback."))[:1000], observation=observation)
            return observation
        if name == "execute_action":
            async with task.action_lock:
                self._require_active(task)
                if task.record["action_count"] >= 50:
                    raise ControlError("task_action_limit")
                entry = dict(action_id=inputs.get("action_id"), parameters=inputs.get("parameters", {}),
                    reason=str(inputs.get("reason", ""))[:1000], before=self.observe(task))
                task.record["actions"].append(entry)
                task.record["action_count"] += 1
                try:
                    reply = self._command(task, "action", action_id=entry["action_id"], parameters=entry["parameters"])
                    entry["run_id"] = reply["run_id"]
                    self._event(task, "action_started", entry["reason"] or entry["action_id"],
                                action_id=entry["action_id"], parameters=entry["parameters"], run_id=entry["run_id"])
                    while True:
                        self._require_active(task)
                        evidence = machine.action_execution.snapshot(entry["run_id"])
                        if evidence["status"] != "running":
                            break
                        await asyncio.sleep(.05)
                    entry.update(evidence=evidence, after=self.observe(task))
                    self._event(task, "action_"+evidence["status"], f"{entry['action_id']}: {evidence['status']}",
                                evidence=evidence, observation=entry["after"])
                    return dict(evidence=evidence, current=entry["after"])
                except ControlError as error:
                    entry["error"] = error.reason
                    self._event(task, "action_rejected", error.reason, action_id=entry["action_id"])
                    raise
        if name == "finish_task":
            outcome, summary = inputs.get("outcome"), inputs.get("summary")
            ids = inputs.get("evidence_run_ids")
            if outcome not in {"completed", "failed"} or not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
                raise ControlError("invalid_task_result")
            completed = {a["run_id"] for a in task.record["actions"] if a.get("evidence", {}).get("status") == "completed"}
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or not set(ids) <= completed:
                raise ControlError("unknown_task_evidence")
            if outcome == "completed" and (not ids or task.action_lock.locked()):
                raise ControlError("task_completion_needs_evidence")
            task.record["evidence_run_ids"] = ids
            self._finish(task, outcome, "agent_verified_from_action_evidence" if outcome == "completed" else "agent_reported_failure", summary)
            return self.get(task.record["task_id"], full=False)
        raise ControlError("unsupported_task_tool")

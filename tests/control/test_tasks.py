"""Task ownership, independent heartbeat, cancellation and persisted evidence."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

from forma_core.control import ControlError, MachineRegistry
from forma_core.control.tasks import MachineTaskRunner, TERMINAL
from forma_core.control.opencode_worker import OpenCodeTaskAgent
from tests.control.test_package import package_fixture


class Agent:
    def __init__(self, run):
        self.run = run


class TaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = MachineRegistry()
        self.machine = self.registry.add_package(package_fixture())
        self.runner = None

        async def ticks():
            while True:
                self.machine.tick()
                await asyncio.sleep(.01)
        self.ticker = asyncio.create_task(ticks())

    async def asyncTearDown(self):
        if self.runner:
            await self.runner.close()
        self.ticker.cancel()
        await asyncio.gather(self.ticker, return_exceptions=True)
        self.temp.cleanup()

    def make_runner(self, run, **kwargs):
        self.runner = MachineTaskRunner(self.registry, Agent(run), Path(self.temp.name), **kwargs)
        return self.runner

    async def terminal(self):
        async with asyncio.timeout(5):
            while self.runner.snapshot("fixture")["status"] not in TERMINAL:
                await asyncio.sleep(.02)
        return self.runner.snapshot("fixture", full=True)

    async def test_live_agent_result_requires_feedback_and_survives_reload(self):
        async def run(task, tool, progress):
            progress("ready", {"model": {"providerID": "test", "id": "operator"}})
            await tool("inspect_machine", {})
            # Reasoning lasts longer than the runtime's one-second watchdog.
            await asyncio.sleep(1.2)
            result = await tool("execute_action", {"action_id": "position", "parameters": {"distance": 5.}, "reason": "Move to requested position"})
            self.assertEqual(result["evidence"]["status"], "completed")
            await tool("finish_task", {"outcome": "completed", "summary": "Observed X=5 mm", "evidence_run_ids": [result["evidence"]["run_id"]]})
        self.make_runner(run)
        self.runner.start("fixture", "Move X to 5 mm")
        secret = self.machine.owner["session_id"]
        result = await self.terminal()
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["heartbeat_count"], 5)
        self.assertEqual(result["actions"][0]["evidence"]["evidence"]["positions"], {"x": 5.})
        self.assertIsNone(self.machine.owner)
        self.assertEqual(self.machine.state, "DISARMED")
        self.assertNotIn(secret, json.dumps(result))
        restored = MachineTaskRunner(self.registry, self.runner.agent, Path(self.temp.name))
        self.assertEqual(restored.snapshot("fixture", full=True), result)

    async def test_stop_interrupts_motion_and_rejects_late_tools(self):
        async def run(task, tool, progress):
            progress("ready", {})
            await tool("execute_action", {"action_id": "position", "parameters": {"distance": 20.}})
        self.make_runner(run)
        self.runner.start("fixture", "Move")
        async with asyncio.timeout(2):
            while not self.machine.plans:
                await asyncio.sleep(.01)
        result = self.runner.cancel("fixture")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.machine.enabled)
        self.assertFalse(self.machine.plans)
        internal = self.runner.tasks[result["task_id"]]
        with self.assertRaisesRegex(ControlError, "task_not_active"):
            await self.runner.tool(internal, "execute_action", {"action_id": "position", "parameters": {"distance": -20.}})
        self.assertEqual(self.runner.get(result["task_id"])["actions"][0]["evidence"]["status"], "interrupted")

    async def test_takeover_does_not_release_or_drive_the_new_owner(self):
        async def run(task, tool, progress):
            progress("ready", {})
            await asyncio.Event().wait()
        self.make_runner(run)
        self.runner.start("fixture", "Wait")
        await asyncio.sleep(.02)
        replacement = self.machine.claim("Human", takeover=True)
        result = await self.terminal()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.machine.owner["session_id"], replacement["session_id"])

    async def test_start_transfers_only_the_authorized_owner_and_excludes_duplicates(self):
        async def run(task, tool, progress):
            await asyncio.Event().wait()
        self.make_runner(run)
        owner = self.machine.claim("Browser")
        with self.assertRaisesRegex(ControlError, "control_busy"):
            self.runner.start("fixture", "Move")
        with self.assertRaisesRegex(ControlError, "wrong_session"):
            self.runner.start("fixture", "Move", session_id="not-owner")
        self.runner.start("fixture", "Move", session_id=owner["session_id"])
        self.assertEqual(self.machine.owner["kind"], "agent")
        with self.assertRaisesRegex(ControlError, "task_already_running"):
            self.runner.start("fixture", "Duplicate")

    async def test_model_failure_and_missing_finish_are_not_success(self):
        async def failure(task, tool, progress):
            raise RuntimeError("Provider unavailable")
        self.make_runner(failure)
        self.runner.start("fixture", "Move")
        result = await self.terminal()
        self.assertEqual(result["status"], "failed")
        self.assertIn("Provider unavailable", result["reason"])
        self.assertIsNone(self.machine.owner)
        async def no_result(task, tool, progress):
            progress("ready", {})
        self.runner.agent = Agent(no_result)
        self.runner.start("fixture", "Move")
        self.assertEqual((await self.terminal())["reason"], "agent_ended_without_verified_result")

    async def test_completion_cannot_reference_another_tasks_evidence(self):
        async def run(task, tool, progress):
            progress("ready", {})
            for ids in ([], ["another-task-run"]):
                with self.assertRaises(ControlError):
                    await tool("finish_task", {"outcome": "completed", "summary": "Claimed success", "evidence_run_ids": ids})
            await tool("finish_task", {"outcome": "failed", "summary": "No evidence", "evidence_run_ids": []})
        self.make_runner(run)
        self.runner.start("fixture", "Move")
        self.assertEqual((await self.terminal())["status"], "failed")

    async def test_task_timeout_releases_without_waiting_for_model(self):
        async def run(task, tool, progress):
            progress("ready", {})
            await asyncio.Event().wait()
        self.make_runner(run, timeout_s=.2)
        self.runner.start("fixture", "Wait forever")
        self.assertEqual((await self.terminal())["reason"], "task_timeout")
        self.assertIsNone(self.machine.owner)

    async def test_heartbeat_uses_the_selected_machines_timing(self):
        from forma_core.control.models import Timing
        self.machine.definition.timing = Timing(heartbeat_ms=40, lease_ms=80, link_loss_ms=120)
        async def run(task, tool, progress):
            progress("ready", {})
            await asyncio.sleep(.35)
            self.assertEqual(self.machine.state, "ARMED")
            await tool("finish_task", {"outcome": "failed", "summary": "Timing check complete", "evidence_run_ids": []})
        self.make_runner(run)
        self.runner.start("fixture", "Wait while reasoning")
        result = await self.terminal()
        self.assertEqual(result["reason"], "agent_reported_failure")
        self.assertGreaterEqual(result["heartbeat_count"], 6)

    async def test_unfinished_journal_is_interrupted_after_host_restart(self):
        async def run(task, tool, progress):
            return
        self.make_runner(run)
        self.runner.start("fixture", "Move")
        record = await self.terminal()
        record.update(status="running", finished_at=None)
        (Path(self.temp.name)/(record["task_id"]+".json")).write_text(json.dumps(record), encoding="utf-8")
        restored = MachineTaskRunner(self.registry, self.runner.agent, Path(self.temp.name))
        self.assertEqual(restored.snapshot("fixture")["reason"], "host_restarted")
        self.assertIsNone(self.machine.owner)

    async def test_subprocess_protocol_errors_success_and_cleanup(self):
        # A protocol fixture, not an LLM policy: exercise the real process bridge
        # and ensure finish tears down a worker that would otherwise hang.
        worker = Path(self.temp.name)/"protocol.py"
        worker.write_text('''import json, os, sys, time
def emit(**packet):
    print("FORMA_TASK " + json.dumps(packet), flush=True)
init = json.loads(input())
assert os.environ["FORMA_TEST_CREDENTIAL"] == "local-test-only"
assert init["model"] == "test/operator"
print("non-protocol output", flush=True)
emit(type="ready", model={"providerID":"test", "id":"operator"})
emit(type="tool", id=1, name="execute_action", input={"action_id":"unknown", "parameters":{}})
assert json.loads(input())["error"] == "unsupported_action"
emit(type="tool", id=2, name="execute_action", input={"action_id":"position", "parameters":{"distance":5.0}})
result = json.loads(input())["result"]
assert result["evidence"]["status"] == "completed"
emit(type="tool", id=3, name="finish_task", input={"outcome":"completed", "summary":"Protocol fixture done", "evidence_run_ids":[result["evidence"]["run_id"]]})
input()
time.sleep(60)
''', encoding="utf-8")
        project_env = Path(self.temp.name)/".env"
        project_env.write_text("FORMA_TEST_CREDENTIAL=local-test-only\nFORMA_TASK_MODEL=test/project-default\n", encoding="utf-8")
        (Path(self.temp.name)/".env.task").write_text("FORMA_TEST_CREDENTIAL=\nFORMA_TASK_MODEL=test/operator\n", encoding="utf-8")
        agent = OpenCodeTaskAgent(worker, Path(self.temp.name)/"workspace", env_files=[project_env])
        agent.executable = sys.executable
        agent.availability = lambda: {"available": True}
        self.runner = MachineTaskRunner(self.registry, agent, Path(self.temp.name)/"journals")
        started = self.runner.start("fixture", "Exercise process protocol")
        result = await self.terminal()
        await asyncio.wait_for(self.runner.tasks[started["task_id"]].job, 3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["actions"][0]["error"], "unsupported_action")
        self.assertNotIn("local-test-only", json.dumps(result))
        self.assertIsNone(self.machine.owner)


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"), "Optional control web dependencies not installed")
class TaskServiceTests(unittest.TestCase):
    def test_goal_start_observer_stream_stop_and_evidence(self):
        from fastapi.testclient import TestClient
        from examples.control_studio.server import create_app
        async def run(task, tool, progress):
            progress("ready", {})
            await asyncio.Event().wait()
        registry = MachineRegistry()
        machine = registry.add_package(package_fixture())
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(registry, task_agent=Agent(run), task_directory=temp)) as client:
            base = "/api/control/v2/machines/fixture"
            self.assertFalse(client.get(base+"/capabilities").json()["simulation_reset"])
            # Capability discovery follows the installed adapter, not its ID.
            machine.adapter.fresh = lambda: None
            self.assertTrue(client.get(base+"/capabilities").json()["simulation_reset"])
            owner = client.post(base+"/claim", json={"name": "Browser"}).json()
            response = client.post(base+"/tasks", json={"goal": "Move X", "session_id": owner["session_id"]})
            self.assertEqual(response.status_code, 200)
            task = response.json()
            with client.websocket_connect(base+"/stream") as ws:
                frame = ws.receive_json()
            self.assertEqual(frame["task"]["task_id"], task["task_id"])
            self.assertNotIn(machine.owner["session_id"], json.dumps(frame))
            self.assertEqual(client.post(base+"/tasks", json={"goal": "Duplicate"}).status_code, 409)
            self.assertEqual(client.post(base+"/tasks/stop").json()["status"], "cancelled")
            self.assertEqual(client.get(base+"/tasks/"+task["task_id"]).json()["reason"], "stopped_by_user")
            self.assertIsNone(machine.owner)
            self.assertEqual(client.get("/").headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()

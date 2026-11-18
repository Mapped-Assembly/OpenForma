"""Optional contact-physics tests; setup_so101.py supplies the pinned model.

No downloads happen in the test suite. An explicit benchmark run requires assets
and fails rather than skipping when they are absent.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCENE = ROOT/"examples/control_studio/generated/so101/pick-place.xml"
AVAILABLE = importlib.util.find_spec("mujoco") is not None and SCENE.is_file()


@unittest.skipUnless(AVAILABLE, "Run setup_so101.py with the robot-sim extra first")
class SO101PhysicsTests(unittest.TestCase):
    def setUp(self):
        from examples.control_studio.so101_benchmark import DeterministicClient
        self.client = DeterministicClient(SCENE)
        self.adapter = self.client.adapter
        self.addCleanup(self.client.release)

    def test_contact_only_pick_and_place_through_shared_runtime(self):
        from examples.control_studio.so101_benchmark import run_reference
        self.assertEqual(self.adapter.model.neq, 0, "No attachment/weld constraints")
        self.assertLess(self.adapter.model.opt.gravity[2], -9)
        report = run_reference(self.client, self.client.wait_step)
        self.assertTrue(report["passed"])
        self.assertGreater(report["max_lift_mm"], 35)
        lift = next(s for s in report["stages"] if s["stage"] == "verify lift")
        self.assertEqual(lift["contacts"], ["fixed", "moving"])
        final = self.adapter.observation()
        self.assertTrue(final["success"])
        self.assertEqual(final["contact_sides"], [])
        self.assertGreaterEqual(final["settled_s"], .5)
        self.assertLess(sum((a-b)**2 for a, b in zip(final["cube_mm"], final["target_mm"])), 25**2)
        self.assertTrue(all(a["measured"] is None for a in self.client.state()["axes"].values()))

    def test_ik_is_pure_bounded_and_rejects_unreachable_targets(self):
        import numpy as np
        before_q = self.adapter.data.qpos.copy()
        before_control = self.adapter.data.ctrl.copy()
        solution = self.client.solve_tool([220., 25., 70.])
        for axis in self.adapter.definition().axes[:-1]:
            self.assertTrue(axis.minimum <= solution[axis.axis_id] <= axis.maximum)
        for point in ([220., 25., 120.], [600., 0., 20.], [float("nan"), 0., 20.]):
            with self.assertRaises(ValueError):
                self.client.solve_tool(point)
        np.testing.assert_array_equal(before_q, self.adapter.data.qpos)
        np.testing.assert_array_equal(before_control, self.adapter.data.ctrl)
        self.assertEqual(self.client.state()["state"], "DISARMED")

    def test_cube_on_target_without_a_grasped_lift_is_not_success(self):
        import mujoco
        from forma_core.control.so101 import TARGET_M
        # Construct an adversarial initial condition (not an execution shortcut).
        self.adapter.data.qpos[self.adapter.cube_qadr:self.adapter.cube_qadr+3] = TARGET_M
        mujoco.mj_forward(self.adapter.model, self.adapter.data)
        self.client.command("arm")
        for _ in range(80):
            self.client.command("heartbeat")
            self.client.wait_step()
        self.assertFalse(self.adapter.ever_lifted)
        self.assertFalse(self.adapter.success)

    def test_free_cube_falls_and_disarm_pauses_physics(self):
        import mujoco
        import numpy as np
        self.adapter.data.qpos[self.adapter.cube_qadr:self.adapter.cube_qadr+3] = [.30, .15, .12]
        mujoco.mj_forward(self.adapter.model, self.adapter.data)
        self.client.command("arm")
        for _ in range(50):
            self.client.command("heartbeat")
            self.client.wait_step()
        self.assertLess(self.adapter.observation()["cube_mm"][2], 16)
        self.assertFalse(self.adapter.ever_lifted)
        self.client.command("disarm")
        pose = self.adapter.data.qpos.copy()
        time = self.adapter.data.time
        for _ in range(20):
            self.client.wait_step()
        np.testing.assert_array_equal(pose, self.adapter.data.qpos)
        self.assertEqual(self.adapter.data.time, time)


@unittest.skipUnless(AVAILABLE and importlib.util.find_spec("httpx"), "Optional simulation/web dependencies required")
class SO101ServiceTests(unittest.TestCase):
    def setUp(self):
        from examples.control_studio.server import create_app
        from fastapi.testclient import TestClient
        from forma_core.control import MachineRegistry, MachineRuntime
        from forma_core.control.so101 import SO101Adapter
        self.adapter = SO101Adapter(SCENE)
        self.runtime = MachineRuntime(self.adapter.definition(), self.adapter)
        registry = MachineRegistry()
        registry.add(self.runtime)
        self.client = TestClient(create_app(registry))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.base = "/api/control/v2/machines/so101"
        self.claim = self.client.post(self.base+"/claim", json={"name": "test"}).json()

    def test_ik_never_executes_and_cannot_bypass_ownership(self):
        result = self.client.post(self.base+"/ik", json={"target_mm": [220., 25., 70.]}).json()
        self.assertFalse(result["executed"])
        self.assertEqual(self.runtime.state, "DISARMED")
        response = self.client.post(self.base+"/command", json=dict(protocol_version=2, op="move", targets=result["targets"],
            session_id="wrong", lease_id=self.claim["lease_id"], seq=1, coordinated=True))
        self.assertEqual(response.json()["reason"], "wrong_session")
        self.assertFalse(self.runtime.plans)
        self.assertEqual(self.client.post(self.base+"/ik", json={"target_mm": [220, 25]}).status_code, 422)
        self.assertEqual(self.client.post(self.base+"/ik", json={"target_mm": [220, 25, 120]}).status_code, 409)

    def test_reset_requires_owner_and_releases_and_disarms(self):
        self.adapter.ever_lifted = True
        self.adapter.success = True
        bad = self.client.post(self.base+"/simulation", json={"session_id": "observer", "op": "reset"})
        self.assertEqual(bad.json()["reason"], "wrong_session")
        result = self.client.post(self.base+"/simulation", json={"session_id": self.claim["session_id"], "op": "reset"})
        self.assertEqual(result.status_code, 200)
        state = result.json()["telemetry"]
        self.assertIsNone(state["owner"])
        self.assertEqual(state["state"], "DISARMED")
        self.assertFalse(state["execution"]["details"]["success"])
        self.assertEqual(state["execution"]["details"]["time_s"], 0)
        self.assertIsNot(self.runtime.adapter, self.adapter)


if __name__ == "__main__":
    unittest.main()

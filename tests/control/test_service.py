"""Optional example transport tests; core control has no FastAPI dependency."""
import importlib.util
from pathlib import Path
import unittest

from forma_core.control import MachineRegistry, MachineRuntime
from tests.control.test_runtime import definition

HAS_WEB = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


@unittest.skipUnless(HAS_WEB, "Optional control web dependencies not installed")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        source = Path(__file__).resolve().parents[2]/"examples/control_studio/server.py"
        spec = importlib.util.spec_from_file_location("control_studio_test_server", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.runtime = MachineRuntime(definition())
        registry = MachineRegistry(); registry.add(self.runtime)
        self.client = TestClient(module.create_app(registry))
        self.client.__enter__()
        self.base = "/api/control/v2/machines/xy"
        self.claim = self.client.post(self.base+"/claim", json={"name": "test"}).json()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def command(self, op, seq=0, **values):
        return self.client.post(self.base+"/command", json=dict(protocol_version=2, session_id=self.claim["session_id"],
            lease_id=self.claim["lease_id"], seq=seq, op=op, **values))

    def test_discovery_schema_and_atomic_command_rejection(self):
        self.assertEqual(len(self.client.get("/api/control/v2/machines").json()), 1)
        capability = self.client.get(self.base+"/capabilities").json()
        self.assertEqual(len(capability["definition"]["axes"]), 2)
        self.assertIn("discriminator", capability["command_schema"])
        self.assertEqual(self.command("arm").status_code, 200)
        bad = self.command("move", seq=1, targets={"x": 20, "y": 31}, coordinated=True)
        self.assertEqual(bad.json()["reason"], "position_limit")
        state = self.client.get(self.base+"/state").json()["telemetry"]
        self.assertEqual(state["axes"]["x"]["target"], 0)
        self.assertEqual(self.client.get("/api/control/v2/machines/missing/state").status_code, 404)

    def test_single_owner_release_and_protocol_mismatch(self):
        self.assertEqual(self.client.post(self.base+"/claim", json={"name": "second"}).status_code, 409)
        self.assertEqual(self.client.post(self.base+"/command", json={"protocol_version": 1}).json()["reason"], "invalid_command")
        self.command("arm")
        self.client.post(self.base+"/release", json={"session_id": self.claim["session_id"]})
        self.assertFalse(self.runtime.enabled)
        self.assertIsNone(self.runtime.owner)

    def test_stream_reports_named_axes_and_owner_lease(self):
        with self.client.websocket_connect(self.base+"/stream?session_id="+self.claim["session_id"]) as ws:
            data = ws.receive_json()
        self.assertEqual(set(data["telemetry"]["axes"]), {"x", "y"})
        self.assertIn("lease_id", data["lease"])
        self.assertIsNone(data["telemetry"]["axes"]["x"]["measured"])

    def test_public_telemetry_cannot_supply_control_credentials(self) -> None:
        """State and observer streams expose identity without authorization."""
        secret = self.claim["session_id"]
        state = self.client.get(self.base+"/state").json()
        owner = state["telemetry"]["owner"]
        self.assertEqual(set(owner), {"owner_id", "name", "kind"})
        self.assertEqual(owner["owner_id"], self.claim["owner_id"])
        self.assertNotEqual(owner["owner_id"], secret)
        self.assertNotIn(secret, str(state))
        with self.client.websocket_connect(self.base+"/stream") as ws:
            observer = ws.receive_json()
        self.assertNotIn(secret, str(observer))
        self.assertNotIn("lease", observer)
        self.assertEqual(
            self.client.post(self.base+"/lease", json={"session_id": owner["owner_id"]}).json()["reason"],
            "wrong_session",
        )
        ack = self.command("arm").json()
        self.assertNotIn(secret, str(ack))
        self.assertTrue(ack["accepted"])

    def test_takeover_changes_public_identity_and_revokes_old_stream_lease(self) -> None:
        """Observers can distinguish owners without seeing either credential."""
        replacement = self.client.post(self.base+"/claim", json={"name": "next", "takeover": True}).json()
        self.assertNotEqual(replacement["owner_id"], self.claim["owner_id"])
        with self.client.websocket_connect(self.base+"/stream?session_id="+self.claim["session_id"]) as ws:
            data = ws.receive_json()
        self.assertNotIn("lease", data)
        self.assertEqual(data["telemetry"]["owner"]["owner_id"], replacement["owner_id"])
        self.assertNotIn(replacement["session_id"], str(data))

    def test_cross_origin_commands_are_rejected(self):
        response = self.client.post(self.base+"/claim", json={"name": "other"}, headers={"Origin": "http://elsewhere.invalid"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()

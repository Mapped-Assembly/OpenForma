"""Machine identity, dispatch, adapter binding and evidence regression tests."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest

from forma_core.control import MachineRuntime, MachineRegistry, VirtualAdapter, ControlError
from forma_core.control.models import MachineDefinition, Feedback, AxisFeedback, AdapterDiagnostics
from forma_core.control.package import (
    MachinePackage, MachineAction, ActionParameter, CompletionRule, ObservationCondition,
    ObservationSpec, import_opencad_identity, load_machine_package,
)


def package_fixture():
    source = dict(version=1, assembly=dict(id="mechanism", name="fixture", root_ids=["base-component"], components={
        "base-component": dict(id="base-component", name="Base", child_ids=["slide-component"], geometry_refs=["base-shape"], metadata={"keep": "original"}),
        "slide-component": dict(id="slide-component", name="Slide", child_ids=[], geometry_refs=["slide-shape"])}))
    joints = [dict(id="native-slide", type="prismatic", parent_shape_id="base-shape", child_shape_id="slide-shape",
        axis=[1., 0., 0.], origin_mm=[0., 0., 0.], lower_limit=-20., upper_limit=20., unit="mm", metadata={"keep": 42})]
    definition = MachineDefinition(machine_id="fixture", name="fixture", backend="virtual", axes=[
        dict(axis_id="x", label="X", unit="mm", minimum=-20., maximum=20., neutral=0., max_rate=20.)])
    action = MachineAction(action_id="position", label="Position slide", handler="move", axes=["x"], rates={"x": 20.},
        parameters={"distance": ActionParameter(unit="mm", minimum=-20, maximum=20)}, parameter_axes={"distance": "x"},
        completion=CompletionRule(source="simulated", tolerance=.01, dwell_s=.04, timeout_s=2))
    return MachinePackage(definition=definition, model_source="opencad", mechanical=import_opencad_identity(source, joints, {"x": "native-slide"}),
                          adapter=VirtualAdapter(definition).describe(), actions=[action])


class PackageTests(unittest.TestCase):
    def test_roundtrip_preserves_native_payload_and_stable_identity_after_regeneration(self):
        package = package_fixture()
        restored = MachinePackage.model_validate_json(package.model_dump_json())
        self.assertEqual(restored.mechanical, package.mechanical)
        self.assertEqual(restored.sha256, package.sha256)
        source = deepcopy(package.mechanical.assembly_snapshot)
        joints = deepcopy(package.mechanical.kinematic_joints)
        source["assembly"]["components"]["slide-component"]["geometry_refs"] = ["regenerated-shape"]
        joints[0]["child_shape_id"] = "regenerated-shape"
        rebuilt = import_opencad_identity(source, joints, {"x": "native-slide"})
        self.assertEqual(rebuilt.joint_components, package.mechanical.joint_components)
        self.assertEqual(rebuilt.axis_joints, package.mechanical.axis_joints)
        self.assertEqual(rebuilt.kinematic_joints[0]["metadata"], {"keep": 42})

    def test_rejects_broken_source_identity_limits_bindings_and_provenance(self):
        cases = [
            lambda p: p["mechanical"]["joint_components"].update({"native-slide": ["base-component", "invented"]}),
            lambda p: p["mechanical"]["kinematic_joints"][0].update(child_shape_id="missing"),
            lambda p: p["definition"]["axes"][0].update(maximum=21),
            lambda p: p["actions"][0]["parameters"]["distance"].update(maximum=21),
            lambda p: p["actions"][0]["completion"].update(source="measured"),
            lambda p: p["actions"][0]["completion"].update(source="commanded"),
            lambda p: p["actions"][0]["completion"].update(positions=False),
            lambda p: p["actions"][0]["rates"].update(x=21),
            lambda p: p["adapter"].update(handlers=[]),
        ]
        for change in cases:
            with self.subTest(change=change):
                raw = package_fixture().model_dump(mode="json")
                change(raw)
                with self.assertRaises(ValueError):
                    MachinePackage.model_validate(raw)

    def test_artifact_integrity_checked_without_executing_payload(self):
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root/"source.json"
            artifact.write_bytes(b"original")
            raw = package_fixture().model_dump(mode="json")
            raw["artifacts"] = [dict(path="source.json", role="source", sha256=sha256(b"original").hexdigest())]
            file = root/"machine.json"
            file.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(load_machine_package(file).definition.machine_id, "fixture")
            artifact.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash/path"):
                load_machine_package(file)
            raw["artifacts"][0]["path"] = "../escape.json"
            with self.assertRaises(ValueError):
                MachinePackage.model_validate(raw)

    def test_wrong_installed_actuator_binding_rejected_before_execution(self):
        package = package_fixture()
        raw = package.model_dump(mode="json")
        raw["adapter"]["actuators"]["x"] = "invented-motor"
        with self.assertRaisesRegex(ValueError, "binding"):
            MachineRuntime(package.definition, package=MachinePackage.model_validate(raw))

    def test_revolute_limits_convert_without_rewriting_native_identity(self):
        raw = package_fixture().model_dump(mode="json")
        raw["mechanical"]["kinematic_joints"][0].update(type="revolute", unit="radian", lower_limit=-math.pi/2, upper_limit=math.pi/2)
        raw["definition"]["axes"][0].update(unit="degree", minimum=-90., maximum=90.)
        raw["actions"][0]["parameters"]["distance"].update(unit="degree", minimum=-90., maximum=90.)
        package = MachinePackage.model_validate(raw)
        self.assertEqual(package.mechanical.kinematic_joints[0]["upper_limit"], math.pi/2)
        raw["definition"]["axes"][0]["maximum"] = 90.01
        with self.assertRaisesRegex(ValueError, "native joint limits"):
            MachinePackage.model_validate(raw)

    def test_rehashed_source_cannot_disagree_with_embedded_identity(self):
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = package_fixture().model_dump(mode="json")
            source = deepcopy(raw["mechanical"]["assembly_snapshot"])
            source["assembly"]["components"]["slide-component"]["name"] = "different source"
            payload = json.dumps(source).encode()
            (root/"assembly.json").write_bytes(payload)
            raw["artifacts"] = [dict(path="assembly.json", role="opencad-assembly", sha256=sha256(payload).hexdigest())]
            (root/"package.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Embedded identity"):
                load_machine_package(root/"package.json")

    def test_cli_identity_schema_and_static_validation(self):
        from forma_core.cli.main import main
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = package_fixture()
            for name, value in (("assembly", package.mechanical.assembly_snapshot), ("joints", package.mechanical.kinematic_joints), ("axes", package.mechanical.axis_joints)):
                (root/f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(main(["machine", "import-opencad", "--assembly", str(root/"assembly.json"), "--joints", str(root/"joints.json"), "--axis-joints", str(root/"axes.json"), "--output", str(root/"identity.json")]), 0)
            self.assertEqual(json.loads((root/"identity.json").read_text()), package.mechanical.model_dump(mode="json"))
            self.assertEqual(main(["machine", "schema", "--output", str(root/"schema.json")]), 0)
            self.assertIn("actions", json.loads((root/"schema.json").read_text())["properties"])
            (root/"package.json").write_text(package.model_dump_json(), encoding="utf-8")
            self.assertEqual(main(["machine", "validate", str(root/"package.json"), "--output", str(root/"report.json")]), 0)
            report = json.loads((root/"report.json").read_text())
            self.assertTrue(report["valid"])
            self.assertFalse(report["execution_verified"])


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.package = package_fixture()
        self.adapter = VirtualAdapter(self.package.definition)
        self.make_runtime()

    def make_runtime(self):
        self.r = MachineRuntime(self.package.definition, self.adapter, clock=lambda: self.now, package=self.package)
        self.owner = self.r.claim("Agent")
        self.seq = 0
        self.send("arm")

    def send(self, op, **params):
        lease = self.r.lease(self.owner["session_id"])
        self.seq += 1
        return self.r.command(dict(protocol_version=2, session_id=self.owner["session_id"], lease_id=lease["lease_id"],
                                   seq=self.seq, op=op, **params))

    def advance(self, duration):
        for _ in range(round(duration/.02)):
            self.now += .02
            self.send("heartbeat")

    def act(self):
        return self.send("action", action_id="position", parameters={"distance": 5.})

    def test_completion_records_feedback_not_just_command_ack(self):
        reply = self.act()
        self.assertEqual(reply["telemetry"]["action"]["status"], "running")
        self.advance(.4)
        record = self.r.action_execution.snapshot(reply["run_id"])
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["evidence"]["positions"], {"x": 5.})
        self.assertEqual(record["provenance"], "ideal-kinematics")
        self.assertEqual(record["feedback_source"], "simulated")
        self.assertEqual(record["package_sha256"], self.package.sha256)
        self.assertNotIn(self.owner["session_id"], json.dumps(record))
        self.send("move", targets={"x": -5.})
        self.advance(.6)
        self.assertEqual(record, self.r.action_execution.snapshot(reply["run_id"]))

    def test_stalled_feedback_times_out_even_when_command_finishes(self):
        self.adapter.read = lambda: Feedback(mode="virtual", axes={"x": AxisFeedback(simulated=0.)})
        self.act()
        self.advance(.5)
        self.assertTrue(self.r.snapshot()["command_motion_complete"])
        self.assertEqual(self.r.snapshot()["action"]["status"], "running")
        self.advance(1.6)
        self.assertEqual(self.r.snapshot()["action"]["status"], "timed_out")
        self.assertEqual(self.r.state, "HOLD")

    def test_unknown_and_invalid_actions_leave_existing_motion_intact(self):
        first = self.act()["run_id"]
        for action_id, params in (("unknown", {}), ("position", {"bad": 1}), ("position", {"distance": 25.})):
            with self.assertRaises(ControlError):
                self.send("action", action_id=action_id, parameters=params)
            self.assertEqual(self.r.action_execution.snapshot(first)["status"], "running")
            self.assertEqual(self.r.targets, {"x": 5.})

    def test_hold_takeover_and_link_loss_cannot_later_report_success(self):
        first = self.act()["run_id"]
        self.send("hold")
        self.assertEqual(self.r.action_execution.snapshot(first)["status"], "interrupted")
        second = self.act()["run_id"]
        self.r.claim("Other", takeover=True)
        self.assertEqual(self.r.action_execution.snapshot(second)["reason"], "ownership_changed")
        self.owner = self.r.claim("Agent", takeover=True)
        self.send("arm")
        third = self.act()["run_id"]
        self.now += 2
        self.r.tick()
        self.assertEqual(self.r.action_execution.snapshot(third)["reason"], "link_lost")

    def test_adapter_fault_fails_action_and_preserves_error(self):
        self.act()
        def fail():
            raise RuntimeError("encoder offline")
        self.adapter.read = fail
        self.r.tick()
        state = self.r.snapshot()
        self.assertEqual(state["action"]["status"], "failed")
        self.assertEqual(state["execution_error"]["message"], "encoder offline")

    def test_missing_and_wrong_type_observations_cannot_satisfy_contact(self):
        class ContactAdapter(VirtualAdapter):
            contact = None
            def describe(self):
                contract = super().describe()
                contract.observations["contact"] = ObservationSpec(path=["contact"], value_type="boolean")
                return contract
            def read(self):
                feedback = super().read()
                feedback.diagnostics = AdapterDiagnostics(kind="test", status="RUNNING", output_enabled=True, details={"contact": self.contact})
                return feedback
        self.adapter = ContactAdapter(self.package.definition)
        raw = self.package.model_dump(mode="json")
        raw["adapter"] = self.adapter.describe().model_dump(mode="json")
        raw["actions"][0]["completion"].update(positions=False, conditions=[dict(observation="contact", operator="eq", value=True)])
        self.package = MachinePackage.model_validate(raw)
        self.make_runtime()
        self.act()
        for value in (None, "true", 1):
            self.adapter.contact = value
            self.advance(.4)
            self.assertEqual(self.r.snapshot()["action"]["status"], "running")
        self.adapter.contact = True
        self.advance(.1)
        self.assertEqual(self.r.snapshot()["action"]["status"], "completed")


class ActionServiceTests(unittest.TestCase):
    def test_additional_package_registers_and_serves_its_hashed_viewer(self):
        from hashlib import sha256
        from fastapi.testclient import TestClient
        from examples.control_studio.server import create_app
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b'{"format":"test-viewer","bodies":[]}'
            (root/"viewer.json").write_bytes(payload)
            raw = package_fixture().model_dump(mode="json")
            raw["artifacts"] = [dict(path="viewer.json", role="viewer-scene", sha256=sha256(payload).hexdigest())]
            path = root/"package.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            registry = MachineRegistry()
            with TestClient(create_app(registry, package_paths=[path])) as client:
                self.assertEqual(client.get("/api/control/v2/machines").json()[0]["machine_id"], "fixture")
                self.assertEqual(client.get("/api/control/v2/machines/fixture/model").content, payload)
                self.assertEqual(registry.get("fixture").action_execution.package.sha256, load_machine_package(path).sha256)

    def test_discovery_authenticated_dispatch_and_public_evidence(self):
        from fastapi.testclient import TestClient
        from examples.control_studio.server import create_app
        p = package_fixture()
        r = MachineRuntime(p.definition, package=p)
        registry = MachineRegistry()
        registry.add(r)
        with TestClient(create_app(registry)) as client:
            base = "/api/control/v2/machines/fixture"
            manifest = client.get(base+"/capabilities").json()["machine_package"]
            self.assertEqual(manifest["mechanical"]["axis_joints"], {"x": "native-slide"})
            owner = client.post(base+"/claim", json={"name": "Agent"}).json()
            packet = dict(protocol_version=2, session_id=owner["session_id"], lease_id=owner["lease_id"], seq=1, op="arm")
            self.assertEqual(client.post(base+"/command", json=packet).status_code, 200)
            packet.update(seq=2, op="action", action_id="position", parameters={"distance": 1.})
            reply = client.post(base+"/command", json=packet).json()
            self.assertIn("run_id", reply)
            self.assertEqual(client.post(base+"/command", json=packet).json()["reason"], "stale_sequence")
            result = client.get(base+"/actions/"+reply["run_id"]).json()
            self.assertNotIn(owner["session_id"], json.dumps(result))
            self.assertEqual(client.get(base+"/actions/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()

"""Control checks deliberately cover a different mechanism and units from the gate."""
import copy
import math
import unittest

from forma_core.control import Feedback, MachineDefinition, MachineRegistry, MachineRuntime, ControlError, VirtualAdapter


def definition():
    return MachineDefinition.model_validate({"machine_id": "xy", "name": "XY benchmark", "axes": [
        {"axis_id": "x", "label": "X", "unit": "mm", "minimum": -40, "maximum": 40, "neutral": 0, "max_rate": 40},
        {"axis_id": "y", "label": "Y", "unit": "mm", "minimum": -30, "maximum": 30, "neutral": 0, "max_rate": 30}]})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.r = MachineRuntime(definition(), clock=lambda: self.now)
        self.session = self.r.claim("test")["session_id"]
        self.seq = 0
        self.send("arm")

    def packet(self, op, **kw):
        self.seq += 1
        return dict(protocol_version=2, session_id=self.session, lease_id=self.r.lease(self.session)["lease_id"], seq=self.seq, op=op, **kw)

    def send(self, op, **kw):
        return self.r.command(self.packet(op, **kw))

    def advance(self, seconds):
        for i in range(round(seconds/.02)):
            self.now += .02
            if i % 10 == 0:
                self.send("heartbeat")
            self.r.tick()

    def reject(self, reason, fn):
        with self.assertRaises(ControlError) as error:
            fn()
        self.assertEqual(error.exception.reason, reason)

    def test_independent_axes_keep_unrelated_motion(self):
        self.send("move", targets={"x": 40}, rates={"x": 20})
        self.advance(.4)
        self.send("move", targets={"y": 30})
        self.advance(.4)
        self.assertAlmostEqual(self.r.commanded["x"], 16)
        self.assertAlmostEqual(self.r.commanded["y"], 12)

    def test_coordinated_diagonal_has_common_progress_and_completion(self):
        self.send("move", targets={"x": 40, "y": 15}, rates={"x": 20, "y": 30}, coordinated=True)
        for _ in range(10):
            before = dict(self.r.commanded)
            self.advance(.1)
            self.assertAlmostEqual(self.r.commanded["x"]/40, self.r.commanded["y"]/15)
            self.assertLessEqual(self.r.commanded["x"]-before["x"], 2+1e-9)
        self.advance(1.02)
        self.assertEqual(self.r.commanded, {"x": 40, "y": 15})
        self.assertTrue(self.r.snapshot()["command_motion_complete"])

    def test_coordinated_zero_distance_member(self):
        self.send("move", targets={"x": 0, "y": 30}, coordinated=True)
        self.advance(1.02)
        self.assertEqual(self.r.commanded, {"x": 0, "y": 30})

    def test_partial_interrupt_cancels_whole_coordinated_group(self):
        self.send("move", targets={"x": 40, "y": 30}, coordinated=True)
        self.advance(.2)
        self.send("hold", axes=["x"])
        held = dict(self.r.commanded)
        self.advance(.4)
        self.assertEqual(self.r.commanded, held)
        self.assertEqual(self.r.targets, held)

    def test_atomic_rejection_no_first_axis_side_effect(self):
        self.send("move", targets={"x": 20})
        before = copy.deepcopy(self.r.targets)
        self.reject("position_limit", lambda: self.send("move", targets={"x": -20, "y": 100}, coordinated=True))
        self.assertEqual(self.r.targets, before)
        self.assertEqual(self.r.plans[0].targets, {"x": 20})
        self.reject("unknown_axis", lambda: self.send("jog", velocities={"x": 5, "z": 5}))
        self.assertEqual(self.r.targets, before)

    def test_jog_neutral_holds_only_selected_axis(self):
        self.send("jog", velocities={"x": 20, "y": -15})
        self.advance(.2)
        self.send("jog", velocities={"x": 0})
        x = self.r.commanded["x"]
        self.advance(.2)
        self.assertAlmostEqual(self.r.commanded["x"], x)
        self.assertAlmostEqual(self.r.commanded["y"], -6)

    def test_jog_stops_at_axis_bound(self):
        self.send("jog", velocities={"x": 40, "y": -30})
        self.advance(2)
        self.assertEqual(self.r.commanded, {"x": 40, "y": -30})

    def test_scheduler_delay_is_capped_and_timeout_precedes_motion(self):
        self.send("move", targets={"x": 40, "y": 30}, coordinated=True)
        self.now += .7; self.r.tick()
        self.assertAlmostEqual(self.r.commanded["x"], .8)
        held = dict(self.r.commanded)
        self.now += .4; self.r.tick()
        self.assertEqual(self.r.state, "LINK_LOST")
        self.assertEqual(self.r.commanded, held)
        self.send("heartbeat"); self.advance(.2)
        self.assertEqual(self.r.state, "LINK_LOST")
        self.send("arm")
        self.assertEqual(self.r.targets, held)

    def test_delayed_newer_packet_and_duplicate_sequence(self):
        packet = self.packet("hold")
        self.r.command(packet)
        self.reject("stale_sequence", lambda: self.r.command(packet))
        packet["seq"] += 10
        self.now += .6
        self.reject("expired_or_unknown_lease", lambda: self.r.command(packet))

    def test_takeover_and_machine_registry_isolation(self):
        self.reject("control_busy", lambda: self.r.claim("agent", kind="agent"))
        self.r.claim("agent", kind="agent", takeover=True)
        self.assertEqual(self.r.state, "LINK_LOST")
        self.reject("wrong_session", lambda: self.r.lease(self.session))
        registry = MachineRegistry(); registry.add(self.r)
        other_definition = definition().model_copy(update={"machine_id": "other"})
        other = MachineRuntime(other_definition, clock=lambda: self.now)
        registry.add(other)
        self.assertEqual(other.state, "DISARMED")
        with self.assertRaises(ValueError):
            registry.add(other)

    def test_disarm_feedback_and_release(self):
        self.send("disarm")
        for axis in self.r.snapshot()["axes"].values():
            self.assertIsNone(axis["simulated"])
            self.assertIsNone(axis["measured"])
        self.r.release(self.session)
        self.assertIsNone(self.r.owner)

    def test_invalid_units_values_and_extra_fields(self):
        for value in (float("nan"), float("inf"), "12", True):
            self.reject("invalid_command", lambda: self.send("move", targets={"x": value}))
        self.reject("rate_limit", lambda: self.send("jog", velocities={"x": 41}))
        self.reject("rate_without_target", lambda: self.send("move", targets={"x": 1}, rates={"y": 5}))
        self.reject("invalid_command", lambda: self.send("arm", targets={"x": 1}))

    def test_invalid_packets_do_not_refresh_watchdog(self):
        for _ in range(6):
            self.now += .2
            self.reject("invalid_command", lambda: self.r.command({"op": "heartbeat"}))
        self.assertEqual(self.r.state, "LINK_LOST")

    def test_adapter_failure_latches_fault_and_clears_feedback(self):
        class Broken(VirtualAdapter):
            def apply(self, commands, enabled):
                if enabled:
                    raise OSError("offline")
                super().apply(commands, enabled)
        self.r.adapter = Broken(self.r.definition)
        self.r.tick()
        self.assertEqual(self.r.state, "FAULT")
        self.assertFalse(self.r.snapshot()["output_enabled"])
        self.assertIsNone(self.r.snapshot()["axes"]["x"]["simulated"])
        self.reject("adapter_fault", lambda: self.send("arm"))

    def test_virtual_adapter_cannot_claim_physical_measurement(self):
        with self.assertRaises(ValueError):
            Feedback.model_validate({"mode": "virtual", "axes": {"x": {"measured": 0}}})
        physical = definition().model_copy(update={"backend": "physical"})
        with self.assertRaises(ValueError):
            MachineRuntime(physical)

    def test_angular_gate_uses_same_runtime(self):
        gate = MachineDefinition.model_validate({"machine_id": "gate", "name": "gate", "axes": [
            {"axis_id": "gate", "label": "Gate", "unit": "degree", "minimum": 0, "maximum": 90, "neutral": 45, "max_rate": 45}]})
        self.r = MachineRuntime(gate, clock=lambda: self.now)
        self.session = self.r.claim("gate")["session_id"]
        self.seq = 0
        self.send("arm"); self.send("move", targets={"gate": 90})
        self.advance(1.02)
        self.assertEqual(self.r.commanded["gate"], 90)


class DefinitionTests(unittest.TestCase):
    def test_duplicate_ids_and_reversed_limits(self):
        data = definition().model_dump(mode="json")
        data["axes"][1]["axis_id"] = "x"
        with self.assertRaises(ValueError):
            MachineDefinition.model_validate(data)
        data = definition().model_dump(mode="json")
        data["axes"][0]["minimum"] = 100
        with self.assertRaises(ValueError):
            MachineDefinition.model_validate(data)

    def test_parent_cycle_body_collision_and_unit_mismatch(self):
        data = definition().model_dump(mode="json")
        for i, a in enumerate(data["axes"]):
            a["joint"] = dict(joint_id=a["axis_id"], kind="prismatic", axis=[1, 0, 0], body_ids=[str(i)], parent_axis="y" if i == 0 else "x")
        with self.assertRaises(ValueError):
            MachineDefinition.model_validate(data)
        data["axes"][0]["joint"]["parent_axis"] = None
        MachineDefinition.model_validate(data)
        data["axes"][1]["joint"]["body_ids"] = ["0"]
        with self.assertRaises(ValueError):
            MachineDefinition.model_validate(data)
        data["axes"][1]["joint"]["body_ids"] = ["1"]
        data["axes"][1]["unit"] = "degree"
        with self.assertRaises(ValueError):
            MachineDefinition.model_validate(data)


if __name__ == "__main__":
    unittest.main()

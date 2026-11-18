"""Execute the compiled C++ controller, not a Python reimplementation.

Build first; set FORMA_SIL_LIBRARY for a non-example output directory.
Without a build these optional native tests explicitly skip.
"""
import json
import importlib.util
import os
from pathlib import Path
import unittest

from forma_core.control import ControlError, MachineDefinition, MachineRuntime
from forma_core.firmware.sil.adapter import FirmwareSilAdapter
from forma_core.firmware.sil.build import library_name
from forma_core.firmware.sil.simulator import FirmwareProfile, NativeSimulator

ROOT = Path(__file__).resolve().parents[2]/"examples/control_studio"
LIBRARY = Path(os.environ.get("FORMA_SIL_LIBRARY", ROOT/"generated/firmware"/library_name()))


def machine(name):
    return MachineDefinition.model_validate_json((ROOT/f"definitions/{name}.json").read_text())


def profile(name):
    return FirmwareProfile.model_validate_json((ROOT/f"profiles/{name}.json").read_text(encoding="utf-8"))


@unittest.skipUnless(LIBRARY.is_file(), "Build native firmware SIL first (see control_studio README)")
class NativeFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.instances = []

    def tearDown(self):
        for sim in self.instances:
            sim.close()

    def native(self, name="servo-gate"):
        sim = NativeSimulator(LIBRARY, machine(name).axes, profile(name))
        self.instances.append(sim)
        return sim

    def test_actual_servo_pulse_mapping_and_local_slew_limit(self):
        sim = self.native()
        sim.feed(b"F1 1 1 1 90\n", 0)
        sim.tick(20)
        first = sim.snapshot()["axes"]["gate"]
        self.assertAlmostEqual(first["firmware_position"], 45.9)
        self.assertEqual(first["pulse_us"], 1510)
        self.assertAlmostEqual(first["simulated"], 45.9)
        for t in range(40, 1040, 20):
            sim.feed(f"F1 {t} 1 1 90\n".encode(), t)
        final = sim.snapshot()["axes"]["gate"]
        self.assertEqual(final["pulse_us"], 2000)
        self.assertEqual(final["simulated"], 90)

    def test_partial_serial_frames_do_not_actuate(self):
        sim = self.native()
        sim.feed(b"F1 1 1", 0)
        sim.tick(20)
        self.assertFalse(sim.snapshot()["enabled"])
        sim.feed(b" 1 60\n", 20)
        self.assertTrue(sim.snapshot()["enabled"])
        self.assertEqual(sim.snapshot()["axes"]["gate"]["target"], 60)

    def test_atomic_multi_axis_limits_and_parser_rejections(self):
        sim = self.native("xy-stage")
        sim.feed(b"F1 1 1 2 20 15\n", 0)
        for packet in (b"F1 2 1 2 -20 31\n", b"F1 2 1 2 nan 0\n", b"F1 2 2 2 0 0\n",
                       b"F1 2 1 2 0 0 trailing\n", b"F1 2 1 1 0\n", b"F1 -1 1 2 0 0\n",
                       b"F1 9007199254740992 1 2 0 0\n", b"F1 2 1 2 0\x00 0\n"):
            sim.feed(packet, 0)
            self.assertEqual(sim.snapshot()["accepted"], 1)
            self.assertEqual(sim.snapshot()["axes"]["x"]["target"], 20)
            self.assertEqual(sim.snapshot()["axes"]["y"]["target"], 15)
        self.assertEqual(sim.snapshot()["rejected"], 8)

    def test_duplicate_sequence_and_bad_packets_do_not_feed_watchdog(self):
        sim = self.native()
        sim.feed(b"F1 1 1 1 90\n", 0)
        sim.feed(b"F1 1 1 1 0\n", 100)
        self.assertEqual(sim.snapshot()["reject_reason"], 2)
        sim.feed(b"garbage\n", 200)
        sim.tick(250)
        status = sim.snapshot()
        self.assertEqual(status["fault"], "firmware_watchdog")
        self.assertFalse(status["enabled"])
        self.assertEqual(status["axes"]["gate"]["pulse_us"], 0)
        sim.feed(b"F1 2 1 1 45\n", 260)
        self.assertFalse(sim.snapshot()["enabled"])
        self.assertEqual(sim.snapshot()["reject_reason"], 4)
        sim.feed(b"F1 3 0 1 45\n", 270)
        self.assertEqual(sim.snapshot()["fault"], "firmware_watchdog")

    def test_overlong_frame_recovers_at_newline(self):
        sim = self.native()
        sim.feed(b"x"*600+b"\nF1 1 1 1 45\n", 0)
        self.assertEqual(sim.snapshot()["rejected"], 1)
        self.assertEqual(sim.snapshot()["accepted"], 1)

    def test_scheduler_pause_cannot_catch_up_before_timeout(self):
        sim = self.native()
        sim.feed(b"F1 1 1 1 90\n", 0)
        sim.tick(200)
        self.assertAlmostEqual(sim.snapshot()["axes"]["gate"]["firmware_position"], 45.9)
        sim.tick(300)
        self.assertAlmostEqual(sim.snapshot()["axes"]["gate"]["firmware_position"], 45.9)
        self.assertEqual(sim.snapshot()["fault"], "firmware_watchdog")

    def test_non_monotonic_clock_faults_without_unsigned_motion_jump(self):
        sim = self.native()
        sim.feed(b"F1 1 1 1 90\n", 100)
        sim.tick(99)
        self.assertEqual(sim.snapshot()["fault"], "firmware_clock")
        self.assertFalse(sim.snapshot()["enabled"])

    def test_xy_outputs_drive_plant_and_reversal(self):
        sim = self.native("xy-stage")
        sim.feed(b"F1 1 1 2 20 -15\n", 0)
        sim.tick(20)
        axes = sim.snapshot()["axes"]
        self.assertEqual(axes["x"]["signed_steps"], 64)
        self.assertEqual(axes["y"]["signed_steps"], -48)
        self.assertAlmostEqual(axes["x"]["simulated"], .8)
        self.assertAlmostEqual(axes["y"]["simulated"], -.6)
        sim.feed(b"F1 2 1 2 -20 15\n", 20)
        sim.tick(40)
        axes = sim.snapshot()["axes"]
        self.assertEqual(axes["x"]["signed_steps"], 0)
        self.assertEqual(axes["x"]["pulses"], 128)
        self.assertEqual(axes["x"]["direction"], -1)

    def test_fractional_steps_do_not_bank_idle_or_emit_at_zero_dt(self):
        sim = self.native("xy-stage")
        sim.feed(b"F1 1 1 2 0 0\n", 0)
        for t in range(1, 200):
            sim.tick(t)
        sim.feed(b"F1 2 1 2 40 30\n", 199)
        sim.tick(200)
        steps = sim.snapshot()["axes"]["x"]["pulses"]
        self.assertLessEqual(steps, 4)
        for _ in range(100):
            sim.tick(200)
        self.assertEqual(sim.snapshot()["axes"]["x"]["pulses"], steps)

    def test_limit_switch_disables_entire_axis_group(self):
        sim = self.native("xy-stage")
        sim.feed(b"F1 1 1 2 20 15\n", 0)
        sim.inject("x", maximum=True)
        sim.tick(20)
        state = sim.snapshot()
        self.assertEqual(state["fault"], "firmware_limit")
        self.assertEqual(state["axes"]["y"]["pulses"], 0)
        self.assertFalse(state["enabled"])

    def test_stall_separates_output_from_plant_and_has_no_fake_encoder(self):
        for name, axis, packet in (("servo-gate", "gate", b"F1 1 1 1 90\n"), ("xy-stage", "x", b"F1 1 1 2 20 15\n")):
            sim = self.native(name)
            start = sim.snapshot()["axes"][axis]["simulated"]
            sim.inject(axis, stalled=True)
            sim.feed(packet, 0)
            sim.tick(100)
            state = sim.snapshot()
            self.assertEqual(state["axes"][axis]["simulated"], start)
            self.assertNotEqual(state["axes"][axis]["firmware_position"], start)
            self.assertIsNone(state["fault"])  # Open-loop firmware cannot see a mechanical stall.

    def test_disarm_cuts_outputs_and_closed_instance_is_guarded(self):
        sim = self.native()
        sim.feed(b"F1 1 1 1 90\n", 0)
        sim.tick(20)
        sim.feed(b"F1 2 0 1 90\n", 20)
        self.assertEqual(sim.snapshot()["axes"]["gate"]["pulse_us"], 0)
        sim.close()
        with self.assertRaises(RuntimeError):
            sim.tick(30)

    def test_invalid_drive_configuration_is_rejected_before_execution(self):
        bad = profile("servo-gate").model_copy(deep=True)
        bad.axes["gate"].pulse_min_us = 2100
        with self.assertRaises(ValueError):
            NativeSimulator(LIBRARY, machine("servo-gate").axes, bad)


@unittest.skipUnless(LIBRARY.is_file(), "Build native firmware SIL first")
class FirmwareAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.definition = machine("servo-gate")
        self.adapter = FirmwareSilAdapter(self.definition, profile("servo-gate"), LIBRARY, clock=lambda: self.now)
        self.runtime = MachineRuntime(self.definition, self.adapter, clock=lambda: self.now)
        self.session = self.runtime.claim("SIL test")["session_id"]
        self.seq = 0

    def tearDown(self):
        self.runtime.adapter.close()
        self.adapter.close()

    def send(self, op, **params):
        self.seq += 1
        return self.runtime.command(dict(protocol_version=2, session_id=self.session,
            lease_id=self.runtime.lease(self.session)["lease_id"], seq=self.seq, op=op, **params))

    def test_link_drop_latches_fault_and_explicit_reset_requires_reclaim(self):
        self.send("arm")
        self.adapter.link_connected = False
        self.now = .25
        self.runtime.tick()
        self.assertEqual(self.runtime.state, "FAULT")
        self.assertEqual(self.runtime.snapshot()["execution_fault"], "firmware_watchdog")
        self.assertIsNone(self.runtime.snapshot()["axes"]["gate"]["simulated"])
        self.adapter.link_connected = True
        with self.assertRaises(ControlError):
            self.send("arm")
        fresh = self.adapter.fresh()
        try:
            with self.assertRaises(ControlError):
                self.runtime.replace_adapter(fresh)
            self.runtime.release(self.session)
            self.runtime.replace_adapter(fresh)
            self.assertEqual(self.runtime.state, "DISARMED")
            self.assertIsNone(self.runtime.owner)
            self.assertIsNone(self.runtime.commanded["gate"])
        finally:
            if self.runtime.adapter is not fresh:
                fresh.close()

    def test_command_and_simulated_position_are_distinct_on_stall(self):
        self.send("arm")
        self.adapter.inject("gate", stalled=True)
        self.send("move", targets={"gate": 90})
        for i in range(60):
            self.now += .02
            if i % 10 == 0:
                self.send("heartbeat")
            self.runtime.tick()
        axis = self.runtime.snapshot()["axes"]["gate"]
        self.assertEqual(axis["commanded"], 90)
        self.assertEqual(axis["simulated"], 45)
        self.assertIsNone(axis["measured"])

    def test_adapter_resets_do_not_cross_machine_boundaries(self):
        other = MachineRuntime(machine("xy-stage"), clock=lambda: self.now)
        self.send("arm")
        self.adapter.link_connected = False
        self.now = .25
        self.runtime.tick()
        self.assertEqual(other.state, "DISARMED")
        self.assertIsNone(other.snapshot()["execution_fault"])


@unittest.skipUnless(LIBRARY.is_file() and importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"), "Native SIL and optional web dependencies required")
class FirmwareServiceTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from forma_core.control import MachineRegistry
        spec = importlib.util.spec_from_file_location("sil_service_test", ROOT/"server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.now = 0.0
        definition = machine("xy-stage")
        self.adapter = FirmwareSilAdapter(definition, profile("xy-stage"), LIBRARY, clock=lambda: self.now)
        self.runtime = MachineRuntime(definition, self.adapter, clock=lambda: self.now)
        registry = MachineRegistry()
        registry.add(self.runtime)
        self.client = TestClient(module.create_app(registry))
        self.client.__enter__()
        self.base = "/api/control/v2/machines/xy-stage"
        self.claim = self.client.post(self.base+"/claim", json={"name": "SIL service test"}).json()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def action(self, op, **params):
        return self.client.post(self.base+"/simulation", json=dict(session_id=self.claim["session_id"], op=op, **params))

    def test_simulator_injection_requires_owner_and_valid_axis(self):
        self.assertEqual(self.client.post(self.base+"/simulation", json={"session_id": "wrong", "op": "disconnect"}).json()["reason"], "wrong_session")
        self.assertEqual(self.action("stall", axis="missing").json()["reason"], "unknown_axis")
        self.assertEqual(self.action("disconnect", axis="x").json()["reason"], "unexpected_axis")
        self.assertEqual(self.action("limit_max", axis="x").status_code, 200)
        state = self.client.get(self.base+"/state").json()["telemetry"]
        self.assertTrue(state["execution"]["details"]["axes"]["x"]["maximum_limit"])

    def test_watchdog_reset_releases_owner_and_stream_observes_new_execution(self):
        self.client.post(self.base+"/command", json=dict(protocol_version=2, session_id=self.claim["session_id"], lease_id=self.claim["lease_id"], seq=1, op="arm"))
        self.action("disconnect")
        self.now = .25
        state = self.client.get(self.base+"/state").json()["telemetry"]
        self.assertEqual(state["execution_fault"], "firmware_watchdog")
        state = self.action("reset").json()["telemetry"]
        self.assertEqual(state["state"], "DISARMED")
        self.assertIsNone(state["owner"])
        self.assertIsNone(state["execution_fault"])
        self.assertEqual(self.action("reconnect").json()["reason"], "wrong_session")
        with self.client.websocket_connect(self.base+"/stream") as ws:
            data = ws.receive_json()
            self.assertFalse(data["telemetry"]["execution"]["output_enabled"])
            self.assertIsNone(data["telemetry"]["axes"]["x"]["simulated"])


if __name__ == "__main__":
    unittest.main()

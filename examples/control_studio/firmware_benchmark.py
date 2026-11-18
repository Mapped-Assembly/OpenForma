"""Deterministic compiled-firmware benchmark; --live also exercises the HTTP host."""
import argparse
import json
from pathlib import Path

from forma_core.control import ControlClient, MachineDefinition, MachineRuntime
from forma_core.firmware.sil.adapter import FirmwareSilAdapter
from forma_core.firmware.sil.build import library_name
from forma_core.firmware.sil.simulator import FirmwareProfile

ROOT = Path(__file__).resolve().parent
PATHS = {
    "servo-gate": [{"gate": 0}, {"gate": 90}, {"gate": 45}],
    "xy-stage": [{"x": -30, "y": -20}, {"x": 30, "y": -20}, {"x": 30, "y": 20},
                 {"x": -30, "y": 20}, {"x": -30, "y": -20}, {"x": 0, "y": 0}],
}


def deterministic(name):
    definition = MachineDefinition.model_validate_json((ROOT/f"definitions/{name}.json").read_text())
    profile = FirmwareProfile.model_validate_json((ROOT/f"profiles/{name}.json").read_text(encoding="utf-8"))
    now = 0.0
    adapter = FirmwareSilAdapter(definition, profile, ROOT/"generated/firmware"/library_name(), clock=lambda: now)
    runtime = MachineRuntime(definition, adapter, clock=lambda: now)
    session = runtime.claim("Compiled firmware benchmark", kind="agent")["session_id"]
    seq = 0
    trace = []
    max_output_step = {a.axis_id: 0.0 for a in definition.axes}
    max_progress_error = 0.0

    def send(op, **values):
        nonlocal seq
        seq += 1
        return runtime.command(dict(protocol_version=2, session_id=session,
            lease_id=runtime.lease(session)["lease_id"], seq=seq, op=op, **values))

    try:
        send("arm")
        for segment, target in enumerate(PATHS[name], 1):
            starts = {a: v["simulated"] for a, v in runtime.snapshot()["axes"].items()}
            send("move", targets=target, coordinated=True)
            for tick in range(1500):
                before = runtime.snapshot()["execution"]["details"]["axes"]
                now = round(now+.02, 6)
                if tick % 10 == 0:
                    send("heartbeat")
                runtime.tick()
                state = runtime.snapshot()
                assert state["state"] == "ARMED", state
                output = state["execution"]["details"]["axes"]
                for a in definition.axes:
                    delta = abs(output[a.axis_id]["firmware_position"]-before[a.axis_id]["firmware_position"])
                    max_output_step[a.axis_id] = max(max_output_step[a.axis_id], delta)
                    assert delta <= a.max_rate*.02+1e-8
                    assert state["axes"][a.axis_id]["measured"] is None
                progress = [(state["axes"][a]["simulated"]-starts[a])/(v-starts[a]) for a, v in target.items() if abs(v-starts[a]) > .05]
                if len(progress) > 1:
                    max_progress_error = max(max_progress_error, max(progress)-min(progress))
                    assert max_progress_error < .005, "Simulated firmware axes lost coordinated progress"
                trace.append(dict(time_s=now, segment=segment, axes=state["axes"], outputs=output))
                if state["command_motion_complete"] and all(abs(state["axes"][a]["simulated"]-v) <= .05 for a, v in target.items()):
                    break
            else:
                raise AssertionError("Firmware output / simulated plant did not reach the target")
        adapter.link_connected = False
        now += profile.watchdog_ms/1000
        runtime.tick()
        assert runtime.state == "FAULT"
        assert runtime.snapshot()["execution_fault"] == "firmware_watchdog"
        assert not runtime.snapshot()["execution"]["output_enabled"]
        return dict(machine=name, passed=True, segments=len(PATHS[name]), samples=len(trace), maximum_firmware_step=max_output_step,
                    maximum_simulated_progress_error=max_progress_error,
                    watchdog_fault="passed", build=adapter.native.manifest, profile=profile.model_dump(mode="json"), trace=trace)
    finally:
        runtime.release(session)
        adapter.close()


def live(url):
    results = []
    for name in PATHS:
        client = ControlClient(url, name+"-sil")
        try:
            client.claim("Live firmware benchmark")
            client.command("arm")
            for targets in PATHS[name]:
                state = client.move_and_wait(targets, wait_for="simulated", tolerance=.05)
                assert state["execution"]["kind"] == "native-firmware-sil"
                assert all(a["measured"] is None for a in state["axes"].values())
                results.append(dict(machine=name, targets=targets, telemetry=state))
        finally:
            client.release()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    report = dict(passed=True, scope="Native C++ software-in-loop, simulated I/O and idealized plants; no MCU emulation or physical measurements.",
                  deterministic=[deterministic(name) for name in PATHS])
    if args.live:
        report["live"] = live(args.url)
    out = ROOT/"generated/firmware-benchmark.json"
    out.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": True, "machines": [{k: r[k] for k in ("machine", "segments", "samples", "maximum_firmware_step", "maximum_simulated_progress_error", "watchdog_fault")} for r in report["deterministic"]],
                      "live_segments": len(report.get("live", [])), "report": str(out)}, indent=2))

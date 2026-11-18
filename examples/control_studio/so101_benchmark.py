"""Contact-only reference controller, not a learned or LLM policy.

Offline: python examples/control_studio/so101_benchmark.py
Live:    python examples/control_studio/so101_benchmark.py --live
Live mode claims an idle SO-101, resets its virtual task and releases it on exit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from forma_core.control import MachineRuntime
from forma_core.control.client import ControlClient

ROOT = Path(__file__).resolve().parent


class DeterministicClient:
    """Exercise the same runtime and v2 commands with a controlled clock."""
    def __init__(self, scene, *, package=None):
        from forma_core.control.so101 import SO101Adapter
        self.now = 0.0
        self.adapter = SO101Adapter(scene, clock=lambda: self.now)
        self.runtime = MachineRuntime(self.adapter.definition(), self.adapter, clock=lambda: self.now, package=package)
        self.owner = self.runtime.claim("Deterministic reference", kind="agent")
        self.seq = 0

    def command(self, op, **parameters):
        lease = self.runtime.lease(self.owner["session_id"])
        self.seq += 1
        return self.runtime.command(dict(protocol_version=2, session_id=self.owner["session_id"],
                                         lease_id=lease["lease_id"], seq=self.seq, op=op, **parameters))

    def state(self):
        return self.runtime.snapshot()

    def action_result(self, run_id):
        return self.runtime.action_execution.snapshot(run_id)

    def solve_tool(self, target_mm):
        return self.adapter.inverse_kinematics(target_mm)

    def wait_step(self):
        self.now += .02
        self.runtime.tick()

    def release(self):
        self.runtime.release(self.owner["session_id"])
        self.adapter.close()


def run_reference(client, wait_step):
    """Observe actual TCP, bilateral contact, lift and released/settled success."""
    stages = []

    def observe():
        state = client.state()
        if state["state"] not in ("ARMED", "HOLD"):
            raise RuntimeError(f"Reference interrupted: {state['state']}")
        return state, state["execution"]["details"]

    def wait(label, predicate, timeout=15):
        _, first = observe()
        start = first["time_s"]
        wall_deadline = time.monotonic()+timeout*4
        while time.monotonic() < wall_deadline:
            client.command("heartbeat")
            wait_step()
            state, task = observe()
            if predicate(state, task):
                stages.append(dict(stage=label, time_s=task["time_s"], tcp_mm=task["tcp_mm"], cube_mm=task["cube_mm"],
                                   contacts=task["contact_sides"], lifted=task["lifted"], success=task["success"]))
                return task
            if task["time_s"]-start > timeout:
                break
        client.command("hold")
        raise TimeoutError(f"{label}: required simulated observation did not arrive")

    def tool(label, point):
        targets = client.solve_tool(point)
        client.command("move", targets=targets, coordinated=True,
                       rates={axis: 22.5 for axis in targets})
        return wait(label, lambda s, t: s["command_motion_complete"] and
                    sum((a-b)**2 for a, b in zip(t["tcp_mm"], point)) < 4)

    client.command("arm")
    _, initial = observe()
    source, target = initial["cube_mm"], initial["target_mm"]
    client.command("move", targets={"gripper": 85.0}, rates={"gripper": 33.75})
    wait("open", lambda s, t: s["command_motion_complete"] and s["axes"]["gripper"]["simulated"] > 80)
    tool("approach", [source[0], source[1], 70.0])
    tool("lower to cube", [source[0], source[1], 15.0])
    client.command("move", targets={"gripper": 0.0}, rates={"gripper": 33.75})
    wait("grasp", lambda s, t: s["command_motion_complete"] and len(t["contact_sides"]) == 2)
    tool("lift", [source[0], source[1], 70.0])
    wait("verify lift", lambda s, t: t["lifted"] and len(t["contact_sides"]) == 2)
    tool("transport", [target[0], target[1], 70.0])
    tool("lower to target", [target[0], target[1], 15.0])
    client.command("move", targets={"gripper": 85.0}, rates={"gripper": 33.75})
    wait("open at target", lambda s, t: s["command_motion_complete"] and s["axes"]["gripper"]["simulated"] > 80)
    tool("retreat", [target[0], target[1], 70.0])
    final = wait("verify released placement", lambda s, t: t["success"] and not t["contact_sides"])
    return dict(passed=True, controller="scripted-reference", observations="simulated-ground-truth",
                grasp_assistance=False, physics="MuJoCo contact/friction/gravity", stages=stages,
                max_lift_mm=final["max_lift_mm"], final_cube_mm=final["cube_mm"], target_mm=target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    if args.live:
        client = ControlClient(args.url, "so101")
        client.claim("SO-101 scripted reference")
        client.reset_simulation()
        client.claim("SO-101 scripted reference")
        wait_step = lambda: time.sleep(.1)
    else:
        client = DeterministicClient(ROOT/"generated/so101/pick-place.xml")
        wait_step = client.wait_step
    try:
        report = run_reference(client, wait_step)
    finally:
        client.release()
    output = ROOT/("generated/so101-live-benchmark.json" if args.live else "generated/so101-benchmark.json")
    output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("Saved:", output)


if __name__ == "__main__":
    main()

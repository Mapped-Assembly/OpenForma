"""Run SO-101 pick/place using discovered declared actions and evidence records.

This is a scripted reference policy, not an autonomous LLM policy.
"""
import argparse
import json
from pathlib import Path
import time

from forma_core.control import ControlClient, load_machine_package
from so101_benchmark import DeterministicClient

ROOT = Path(__file__).resolve().parent


def run(client, wait_step):
    records = []

    def act(name, **parameters):
        reply = client.command("action", action_id=name, parameters=parameters)
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            client.command("heartbeat")
            wait_step()
            record = client.action_result(reply["run_id"])
            if record["status"] == "completed":
                records.append(record)
                return
            if record["status"] != "running":
                raise RuntimeError(f"{name}: {record['status']} / {record['reason']}")
        client.command("hold")
        raise TimeoutError(name)

    client.command("arm")
    observation = client.state()["execution"]["details"]
    sx, sy, _ = observation["cube_mm"]
    tx, ty, _ = observation["target_mm"]
    act("open_gripper")
    act("move_tool", x=sx, y=sy, z=70.)
    act("move_tool", x=sx, y=sy, z=15.)
    act("grasp")
    act("move_tool", x=sx, y=sy, z=70.)
    if not client.state()["execution"]["details"]["lifted"]:
        raise RuntimeError("No verified contact-based lift")
    act("move_tool", x=tx, y=ty, z=70.)
    act("move_tool", x=tx, y=ty, z=15.)
    act("open_gripper")
    act("verify_placement", x=tx, y=ty, z=70.)
    return dict(passed=True, policy="scripted-reference", records=records,
                task=client.state()["execution"]["details"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    if args.live:
        client = ControlClient(args.url, "so101")
        manifest = client.capabilities()["machine_package"]
        if manifest is None:
            raise RuntimeError("SO-101 has no registered package; run setup and restart Studio")
        client.claim("Declared-action reference")
        client.reset_simulation()
        client.claim("Declared-action reference")
        wait_step = lambda: time.sleep(.1)
    else:
        package = load_machine_package(ROOT/"generated/so101/machine-package.json")
        client = DeterministicClient(ROOT/"generated/so101/pick-place.xml", package=package)
        wait_step = client.wait_step
    try:
        report = run(client, wait_step)
    finally:
        client.release()
    path = ROOT/("generated/action-live-evidence.json" if args.live else "generated/action-evidence.json")
    path.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"], actions=len(report["records"]),
        provenance=report["records"][-1]["provenance"], final_evidence=report["records"][-1]["evidence"], output=str(path)), indent=2))


if __name__ == "__main__":
    main()

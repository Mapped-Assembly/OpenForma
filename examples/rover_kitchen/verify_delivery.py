"""Check completed SDK task journals against an independent SpaceIR layout."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any


def check_delivery(
    record: dict[str, Any],
    item: str,
    station: str,
    scenario: dict[str, Any],
    centers: dict[str, tuple[float, float]],
) -> None:
    """Require a pickup, cited delivery and park evidence, and matching final geometry."""
    machine = record["machine_id"]
    final = record["final_observation"]
    current = final["observations"]
    initial = record["initial_observation"]["observations"]["inventory"][item]
    slot = scenario["stations"][station]
    home = centers[scenario["machines"][machine]["home"]]
    target = (*centers[slot["footprint"]], slot["height_m"] + 0.032)
    model = record.get("model")
    if (
        not isinstance(model, dict)
        or not all(model.get(k) for k in ("providerID", "id"))
        or not record.get("agent_session_id")
    ):
        raise ValueError("Missing SDK model or session identity")
    if not any(event["kind"] == "inspect" for event in record["events"]):
        raise ValueError("No machine capability inspection was recorded")
    if final["provenance"] != "ideal-kinematics":
        raise ValueError("Expected simulated rover feedback")
    if (
        record["status"] != "completed"
        or initial["holder"] is not None
        or initial["station"] in (None, station)
    ):
        raise ValueError("Expected a completed transfer from another station")
    observed_item = current["inventory"][item]
    if (
        current["holding"] is not None
        or observed_item["holder"] is not None
        or observed_item["station"] != station
    ):
        raise ValueError("Final item custody or station does not match the goal")
    if not math.dist(observed_item["position_m"], target) < 0.008:
        raise ValueError("Item lies outside the target's 8 mm tolerance")
    if (
        final["state"] != "DISARMED"
        or not current["at_home"]
        or not math.dist(current["base_m"], home) < 0.004
    ):
        raise ValueError("Rover did not park within 4 mm of home and disarm")
    completed = {
        action["run_id"]: action["after"]["observations"]
        for action in record["actions"]
        if (action.get("evidence") or {}).get("status") == "completed"
        and action["evidence"]["run_id"] == action["run_id"]
    }
    if not any(after["holding"] == item for after in completed.values()):
        raise ValueError("No completed action shows the assigned rover holding the item")
    cited = record.get("evidence_run_ids", [])
    if not cited or not set(cited) <= completed.keys():
        raise ValueError("Missing or unknown action evidence")
    if not any(completed[key]["inventory"][item]["station"] == station for key in cited):
        raise ValueError("The cited runs do not show delivery to the requested station")
    if not any(completed[key]["at_home"] for key in cited):
        raise ValueError("The cited runs do not show the rover at home")


def main() -> None:
    """Verify one or more raw journals and optionally require overlapping execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path(__file__).with_name("scenario.json"))
    parser.add_argument(
        "--delivery", nargs=3, action="append", required=True, metavar=("JOURNAL", "ITEM", "STATION")
    )
    parser.add_argument("--require-overlap", action="store_true")
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text())
    space = json.loads((args.scenario.parent / scenario["space"]).read_text())
    if space["frames"] != [{"id": "world", "units": "m", "axes": "XZ"}]:
        parser.exit(1, "Expected a world X/Z frame in meters\n")
    centers = {
        obj["id"]: (
            sum(x for x, _ in obj["polygon"]) / len(obj["polygon"]),
            -sum(z for _, z in obj["polygon"]) / len(obj["polygon"]),
        )
        for obj in space["occupancies"]
    }
    windows = []
    actions = []
    machines = set()
    for filename, item, station in args.delivery:
        try:
            record = json.loads(Path(filename).read_text())
            check_delivery(record, item, station, scenario, centers)
            ready = [event["time"] for event in record["events"] if event["kind"] == "ready"]
            if len(ready) != 1:
                raise ValueError("Expected one SDK worker connection")
            windows.append((datetime.fromisoformat(ready[0]), datetime.fromisoformat(record["finished_at"])))
            started = {e["run_id"]: e["time"] for e in record["events"] if e["kind"] == "action_started"}
            for event in record["events"]:
                if event["kind"] == "action_completed":
                    start = datetime.fromisoformat(started[event["evidence"]["run_id"]])
                    actions.append((record["machine_id"], start, datetime.fromisoformat(event["time"])))
        except (KeyError, OSError, TypeError, ValueError) as error:
            parser.exit(1, f"{filename}: {error}\n")
        machines.add(record["machine_id"])
        print(f"{record['machine_id']} delivered {item} to {station} and parked.")
    if args.require_overlap:
        if len(machines) < 2 or max(start for start, _ in windows) >= min(end for _, end in windows):
            parser.exit(1, "Expected overlapping tasks on at least two machines\n")
        if not any(a[0] != b[0] and max(a[1], b[1]) < min(a[2], b[2]) for a, b in combinations(actions, 2)):
            parser.exit(1, "No completed actions overlapped across different rovers\n")
        print("The verified tasks and their machine actions overlapped.")


if __name__ == "__main__":
    main()

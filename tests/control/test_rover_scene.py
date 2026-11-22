"""Conformance checks for the shared rover scene's data and execution boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from collections.abc import Callable, Awaitable
from typing import Any
from unittest.mock import patch

import httpx
import mujoco
import numpy as np

from forma_core.control.rover_scene.assets import SOURCES, validate_assets
from forma_core.control.rover_scene.host import create_app
from forma_core.control.rover_scene.machine import Rover
from forma_core.control.rover_scene.scene import Scenario, Scene

HERE = Path(__file__).resolve().parents[2] / "examples/rover_kitchen"
ASSETS = Path(os.environ.get("FORMA_ROVER_ASSETS", HERE / "generated/robots"))
WORKER = HERE.parent / "control_studio/task_worker.mjs"


def fixture() -> tuple[dict, dict]:
    """Read the example as input data, independent of the compiler's implementation."""
    spec = json.loads((HERE / "scenario.json").read_text())
    return spec, json.loads((HERE / spec["space"]).read_text())


def write_scene(directory: Path, spec: dict, space: dict) -> Path:
    """Write a complete portable scenario with its referenced static fixture assets."""
    manifest = directory / "scenario.json"
    manifest.write_text(json.dumps(spec))
    (directory / spec["space"]).write_text(json.dumps(space))
    for name in spec["fixtures"].values():
        (directory / name).write_bytes((HERE / name).read_bytes())
    return manifest


def transformed() -> tuple[dict, dict]:
    """Rename every entity, reorder instances, translate the world, and add a third rover."""
    spec, space = fixture()
    third = copy.deepcopy(next(o for o in space["occupancies"] if o["id"] == "amber_home"))
    third["id"] = "third_home"
    for point in third["polygon"]:
        point[0] += 1.75
    space["occupancies"].append(third)
    spec["machines"]["third"] = {**spec["machines"]["amber"], "home": "third_home", "name": "Third"}
    def rename(key: str) -> str:
        """Rename a reference consistently across all input records."""
        return "new_" + key
    spec["aisle"] = rename(spec["aisle"])
    spec["fixtures"] = {rename(k): v for k, v in spec["fixtures"].items()}
    spec["machines"] = {
        rename(k): {**v, "home": rename(v["home"])} for k, v in reversed(list(spec["machines"].items()))
    }
    spec["stations"] = {rename(k): {**v, "footprint": rename(v["footprint"])} for k, v in spec["stations"].items()}
    spec["items"] = {rename(k): {**v, "station": rename(v["station"])} for k, v in spec["items"].items()}
    for shape in [*space["regions"], *space["occupancies"]]:
        shape["id"] = rename(shape["id"])
        if "region_id" in shape:
            shape["region_id"] = rename(shape["region_id"])
        for point in shape["polygon"]:
            point[0] += 0.4
            point[1] += 0.2
    return spec, space


class DataOperator:
    """Use task-supplied references and the public action binding catalogue."""

    def __init__(self, bindings: dict[str, dict]) -> None:
        """Retain API-discovered bindings without parsing action names."""
        self.bindings = bindings

    async def run(self, task: dict, invoke: Callable[[str, dict], Awaitable[dict]], progress: Callable[[str, dict], Any]) -> None:
        """Transfer the requested item using discovered operations and observed custody."""
        progress("ready", {"model": "data-conformance-test"})
        goal = json.loads(task["goal"])
        inventory = (await invoke("observe", {}))["observations"]["inventory"]
        source = inventory[goal["item"]]["station"]
        sequence = [("drive", source), ("reach", source), ("pick", goal["item"]),
                    ("lift", source), ("drive", goal["destination"]),
                    ("reach", goal["destination"]), ("place", goal["destination"]), ("home", None)]
        runs = []
        for operation, target in sequence:
            action = next(key for key, binding in self.bindings[task["machine_id"]].items()
                          if binding == {"operation": operation, "target_id": target})
            result = await invoke("execute_action", {"action_id": action, "parameters": {}, "reason": operation})
            if result["evidence"]["status"] != "completed":
                raise AssertionError(result["evidence"])
            runs.append(result["evidence"]["run_id"])
        await invoke("finish_task", {"outcome": "completed", "summary": "Transferred", "evidence_run_ids": runs})


class ContractTests(unittest.TestCase):
    """Validate malformed declarations without loading large robot assets."""

    def test_unsupported_execution_and_profile(self) -> None:
        """Unsupported fidelity, capabilities, and profile versions fail at input validation."""
        for mutation, message in [
            ({"fidelity": "contact-dynamics"}, "unsupported_fidelity"),
            ({"capabilities": ["force-control"]}, "unsupported_capabilities"),
        ]:
            spec, _ = fixture()
            spec["execution"] = mutation
            with self.assertRaisesRegex(ValueError, message):
                Scenario.model_validate(spec)
        spec["execution"] = {}
        spec["machines"]["amber"]["profile_version"] = 2
        with self.assertRaisesRegex(ValueError, "profile_version"):
            Scenario.model_validate(spec)

    def test_invalid_item_reference(self) -> None:
        """Dangling references include the offending input path."""
        spec, _ = fixture()
        spec["items"]["tomato"]["station"] = "missing"
        with self.assertRaisesRegex(ValueError, r"items.tomato.station: unknown"):
            Scenario.model_validate(spec)

    def test_missing_occupancy(self) -> None:
        """Reject missing home, fixture, station, and aisle bindings before model loading."""
        for kind in ("home", "fixture", "station", "aisle"):
            spec, space = fixture()
            if kind == "home":
                spec["machines"]["amber"]["home"] = "missing"
            elif kind == "fixture":
                spec["fixtures"]["missing"] = "irrelevant.xml"
            elif kind == "station":
                spec["stations"]["tomato_bin"]["footprint"] = "missing"
            else:
                spec["aisle"] = "missing"
            with tempfile.TemporaryDirectory() as directory:
                # Missing fixture paths must fail on the reference, before reading files.
                path = Path(directory)
                (path / "scenario.json").write_text(json.dumps(spec))
                (path / spec["space"]).write_text(json.dumps(space))
                with self.assertRaisesRegex(ValueError, "unknown occupancy missing"):
                    Scene(path / "scenario.json", path / "no-assets")

    def test_asset_revision_mismatch(self) -> None:
        """Reject absent or incompatible revision metadata, without downloading assets."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, _, revision, _, _ in SOURCES:
                (root / name).mkdir()
                (root / name / "REVISION").write_text(revision)
            self.assertEqual(len(validate_assets(root)), 2)
            (root / "so101/REVISION").write_text("unsupported")
            with self.assertRaisesRegex(ValueError, "Unsupported asset revision for so101"):
                validate_assets(root)


@unittest.skipUnless((ASSETS / "astra/model.xml").is_file(), "Install pinned robot assets first")
class SharedWorldTests(unittest.IsolatedAsyncioTestCase):
    """Exercise unchanged compiler and HTTP runtime against transformed scene data."""

    def test_instance_isolation_and_custody(self) -> None:
        """Three instances share one world but cannot overwrite peer joints or custody."""
        with tempfile.TemporaryDirectory() as directory:
            world = Scene(write_scene(Path(directory), *transformed()), ASSETS)
            rovers = {key: Rover(world, key) for key in world.spec.machines}
            a, b, c = rovers.values()
            self.assertTrue(all(r.model is world.model and r.data is world.data for r in rovers.values()))
            before = world.data.qpos.copy()
            a.apply({"base_x": a.positions["base_x"] + 20}, True)
            for peer in (b, c):
                np.testing.assert_array_equal(world.data.qpos[peer.root:peer.root + 7], before[peer.root:peer.root + 7])
                np.testing.assert_array_equal(world.data.qpos[peer.ik.qids], before[peer.ik.qids])
            with self.assertRaisesRegex(ValueError, "already bound"):
                Rover(world, a.machine_id)
            self.assertTrue(world.claim(a.machine_id, "new_tomato"))
            self.assertFalse(world.claim(b.machine_id, "new_tomato"))
            self.assertFalse(world.claim(a.machine_id, "new_lettuce"))
            self.assertFalse(world.release(a.machine_id, "new_greens_bin"))
            self.assertEqual(world.held, {"new_tomato": a.machine_id})
            self.assertTrue(world.release(a.machine_id, "new_left_plate"))
            self.assertEqual(world.locations["new_tomato"], "new_left_plate")
            self.assertFalse(world.held)

    async def test_transformed_world_http_transfers_and_fidelity(self) -> None:
        """Discover actions, reject unsupported tasks, and deliver concurrently after data-only edits."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operator = DataOperator({})
            app = create_app(write_scene(root, *transformed()), ASSETS, root / "run", worker=WORKER, agent=operator)
            with patch.object(mujoco, "mj_step", wraps=mujoco.mj_step) as step:
                async with app.router.lifespan_context(app), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    scene = (await client.get("/api/scene")).json()
                    self.assertEqual(scene["execution"]["fidelity"], "kinematic")
                    self.assertEqual(set(scene["stations"]), set(app.state.world.spec.stations))
                    rejected = await client.post("/api/tasks", json={"machine_id": "new_amber", "goal": "test",
                        "execution": {"fidelity": "contact-dynamics"}})
                    self.assertEqual(rejected.status_code, 409)
                    self.assertIsNone(app.state.runner.snapshot("new_amber"))
                    rejected = await client.post("/api/tasks", json={"machine_id": "new_amber", "goal": "test",
                        "execution": {"capabilities": ["force-control"]}})
                    self.assertEqual(rejected.status_code, 409)
                    ids = []
                    for machine, item, destination in [("new_amber", "new_tomato", "new_left_plate"),
                                                        ("new_blue", "new_lettuce", "new_right_plate")]:
                        package = (await client.get("/api/capabilities/" + machine)).json()
                        operator.bindings[machine] = package["bindings"]
                        self.assertEqual(package["execution"], scene["execution"])
                        response = await client.post("/api/tasks", json={"machine_id": machine,
                            "goal": json.dumps({"item": item, "destination": destination}),
                            "execution": {"capabilities": ["proximity-transfer"]}})
                        self.assertEqual(response.status_code, 200, response.text)
                        ids.append(response.json()["task_id"])
                    async with asyncio.timeout(90):
                        for task_id in ids:
                            while True:
                                record = (await client.get("/api/tasks/" + task_id)).json()
                                if record["status"] in ("completed", "failed", "cancelled", "interrupted"):
                                    break
                                await asyncio.sleep(0.1)
                            self.assertEqual(record["status"], "completed", record.get("reason"))
                            self.assertEqual(record["final_observation"]["observations"]["execution"]["fidelity"], "kinematic")
                            self.assertEqual(len(record["evidence_run_ids"]), 8)
                    state = (await client.get("/api/state")).json()
                    self.assertEqual(len(state["machines"]), 3)
                    self.assertEqual(state["inventory"]["new_tomato"]["station"], "new_left_plate")
                    self.assertEqual(state["inventory"]["new_lettuce"]["station"], "new_right_plate")
                    self.assertIsNone(state["machines"]["new_third"]["task"])
                step.assert_not_called()

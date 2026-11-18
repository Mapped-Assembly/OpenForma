"""Offline integration checks. Use the separately installed, pinned robot assets."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import httpx

from forma_core.control.rover_scene.host import create_app, operator_view
from forma_core.control.rover_scene.machine import Rover
from forma_core.control.rover_scene.scene import Scene
from forma_core.control.runtime import ControlError

from .server import DEFAULT, HERE, WORKER

ASSETS = Path(os.environ.get("FORMA_ROVER_ASSETS", HERE / "generated/robots"))


class ReferenceOperator:
    """Exercise the SDK tool contract deterministically without calling a provider."""

    async def run(self, task: dict, invoke: Callable, progress: Callable) -> None:
        """Discover a source and serving slot and execute the known test workload."""
        progress("ready", {"model": "reference-test"})
        package = (await invoke("inspect_machine", {}))["package"]
        ids = {a["action_id"] for a in package["actions"]}
        inventory = (await invoke("observe", {}))["observations"]["inventory"]
        item, destination = (
            ("tomato", "left_plate") if task["machine_id"] == "amber" else ("lettuce", "right_plate")
        )
        source = inventory[item]["station"]
        actions = [
            f"visit_{source}",
            f"reach_{source}",
            f"pick_{item}",
            f"lift_{source}",
            f"visit_{destination}",
            f"reach_{destination}",
            f"place_{destination}",
            "return_home",
        ]
        runs = []
        for action in actions:
            if action not in ids:
                raise ValueError(f"Undeclared action {action}")
            result = await invoke("execute_action", {"action_id": action, "parameters": {}, "reason": action})
            if result["evidence"]["status"] != "completed":
                raise ValueError(result["evidence"]["reason"])
            runs.append(result["evidence"]["run_id"])
        await invoke(
            "finish_task",
            {"outcome": "completed", "summary": "Delivered and parked", "evidence_run_ids": runs},
        )


@unittest.skipUnless((ASSETS / "astra/model.xml").is_file(), "Install pinned robot assets first")
class WorkcellTests(unittest.IsolatedAsyncioTestCase):
    """Check task entry points, world custody, cancellation and portability."""

    async def test_shared_kitchen(self) -> None:
        """Deliver both items concurrently and check independent evidence and final poses."""
        with tempfile.TemporaryDirectory() as output:
            app = create_app(DEFAULT, ASSETS, Path(output), agent=ReferenceOperator(), worker=WORKER)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            ):
                ids = {}
                for key in ("amber", "blue"):
                    response = await client.post(
                        "/api/tasks",
                        json={
                            "machine_id": key,
                            "goal": "Deliver the ingredient to its plate and return home.",
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    ids[key] = response.json()["task_id"]
                async with asyncio.timeout(90):
                    for key, task_id in ids.items():
                        while True:
                            record = (await client.get("/api/tasks/" + task_id)).json()
                            if record["status"] in ("failed", "interrupted", "completed", "cancelled"):
                                break
                            await asyncio.sleep(0.2)
                        self.assertEqual(record["status"], "completed", record["reason"])
                        self.assertTrue(record["final_observation"]["observations"]["at_home"])
                        package = record["package"]
                        discovery = operator_view(
                            "inspect_machine", {"package": package, "current": record["initial_observation"]}
                        )
                        self.assertEqual(set(discovery["actions"]), {a["action_id"] for a in package["actions"]})
                        self.assertEqual(discovery["operating_notes"], package["operating_notes"])
                        for action in record["actions"]:
                            view = operator_view(
                                "execute_action", {"evidence": action["evidence"], "current": action["after"]}
                            )
                            self.assertEqual(view["evidence"]["run_id"], action["run_id"])
                            self.assertEqual(view["current"]["holding"], action["after"]["observations"]["holding"])
                        final = record["final_observation"]
                        observed = operator_view("observe", final)
                        self.assertIn("at_home", observed["true_conditions"])
                        self.assertEqual(observed["inventory"], final["observations"]["inventory"])
                        self.assertEqual(observed["base_m"], final["observations"]["base_m"])
                        self.assertEqual(len(record["evidence_run_ids"]), 8)
                        self.assertTrue(
                            all(a["evidence"]["provenance"] == "ideal-kinematics" for a in record["actions"])
                        )
                world = app.state.world
                self.assertEqual(world.locations, {"tomato": "left_plate", "lettuce": "right_plate"})
                for item, slot in world.locations.items():
                    self.assertLess(
                        float(sum((world.data.mocap_pos[world.item_ids[item]] - world.targets[slot]) ** 2)),
                        1e-6,
                    )
                self.assertFalse(world.held)
                self.assertFalse(world.routes)

    async def test_preflight_rejection_is_journaled(self) -> None:
        """An out-of-reach command leaves a reason and action ID in the task history."""
        with tempfile.TemporaryDirectory() as output:
            app = create_app(DEFAULT, ASSETS, Path(output), agent=ReferenceOperator(), worker=WORKER)
            async with app.router.lifespan_context(app):
                runner = app.state.runner
                task_id = runner.start("amber", "Check reach")["task_id"]
                with self.assertRaisesRegex(ControlError, "visit_station_first"):
                    await runner.tool(runner.tasks[task_id], "execute_action", {"action_id": "place_left_plate"})
                event = runner.get(task_id)["events"][-1]
                self.assertEqual(event["kind"], "action_rejected")
                self.assertEqual(event["action_id"], "place_left_plate")
                self.assertEqual(event["message"], "visit_station_first")

    async def test_stop_one(self) -> None:
        """A selected Stop releases only that machine's task."""
        with tempfile.TemporaryDirectory() as output:
            app = create_app(DEFAULT, ASSETS, Path(output), agent=ReferenceOperator(), worker=WORKER)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            ):
                for key in ("amber", "blue"):
                    await client.post("/api/tasks", json={"machine_id": key, "goal": "Deliver a serving"})
                await asyncio.sleep(0.2)
                stopped = await client.post("/api/machines/amber/stop")
                self.assertEqual(stopped.json()["status"], "cancelled")
                self.assertEqual(app.state.runner.snapshot("blue")["status"], "running")
                self.assertFalse(app.state.world.adapters["amber"].enabled)

    def test_relocated_and_renamed_instance(self) -> None:
        """Load one renamed rover, a translated source, and a new action ID from data alone."""
        spec = json.loads(DEFAULT.read_text())
        space = json.loads((HERE / spec["space"]).read_text())
        spec["machines"] = {"cedar": spec["machines"]["blue"]}
        station = spec["stations"].pop("tomato_bin")
        spec["stations"]["supply"] = station
        spec["items"]["tomato"]["station"] = "supply"
        footprint = next(x for x in space["occupancies"] if x["id"] == station["footprint"])
        for point in footprint["polygon"]:
            point[0] += 0.22
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.json"
            path.write_text(json.dumps(spec))
            (path.parent / spec["space"]).write_text(json.dumps(space))
            for name in spec["fixtures"].values():
                (path.parent / name).write_bytes((HERE / name).read_bytes())
            world = Scene(path, ASSETS)
            rover = Rover(world, "cedar")
            self.assertEqual(set(world.adapters), {"cedar"})
            self.assertIn("visit_supply", rover.commands)
            self.assertIn("visit_supply", rover.available_actions())
            self.assertNotIn("pick_tomato", rover.available_actions())
            self.assertAlmostEqual(world.docks["supply"][0], -1.53)
            self.assertAlmostEqual(world.inventory()["tomato"]["position_m"][0], -1.53)

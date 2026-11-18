"""Launch the portable MuJoCo rover workcell with Forma's OpenCode task worker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from forma_core.control.opencode_worker import OpenCodeTaskAgent
from forma_core.control.rover_scene.fleet import FleetRunner, RoutedRuntime
from forma_core.control.rover_scene.machine import Rover
from forma_core.control.rover_scene.scene import Scene
from forma_core.control.rover_scene.view import BrowserScene
from forma_core.control.runtime import ControlError, MachineRegistry
from forma_core.control.tasks import TaskAgent

WEB = Path(__file__).resolve().parent / "web"


class Goal(BaseModel):
    """One free-form task addressed to a machine in the current scene."""

    model_config = ConfigDict(extra="forbid")
    machine_id: str
    goal: str = Field(min_length=1, max_length=2000)
    model: str | None = Field(default=None, pattern=r"^[^/\s]+/[^\s]+$")


class Workers:
    """Dispatch each task to an isolated OpenCode workspace."""

    def __init__(self, world: Scene, output: Path, worker: Path, env_files: tuple[Path, ...]) -> None:
        """Reuse the same SDK worker and provider settings as Control Studio."""
        self.members = {
            key: OpenCodeTaskAgent(worker, output / "workers" / key, env_files=env_files)
            for key in world.adapters
        }

    async def run(self, task: dict, invoke: Callable, progress: Callable) -> None:
        """Pass the user's literal goal through the existing worker protocol."""
        await self.members[task["machine_id"]].run(task, invoke, progress)


def create_app(
    manifest: Path,
    assets: Path,
    output: Path,
    *,
    worker: Path,
    env_files: tuple[Path, ...] = (),
    agent: TaskAgent | None = None,
) -> FastAPI:
    """Bind a SpaceIR scene and scenario to the HTTP task and visualization APIs."""
    world = Scene(manifest, assets)
    registry = MachineRegistry()
    machines = {}
    for key in world.spec.machines:
        adapter = Rover(world, key)
        runtime = RoutedRuntime(adapter.definition, adapter, package=adapter.package())
        machines[key] = runtime
        registry.add(runtime)
    geometry = BrowserScene(world.model, world.data)
    workers = Workers(world, output, worker, env_files)
    runner = FleetRunner(registry, agent or workers, output / "tasks", timeout_s=600)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Advance the shared world independently of UI and model latency."""

        async def tick() -> None:
            """Update each machine on the owning event loop."""
            while True:
                for runtime in machines.values():
                    runtime.tick()
                await asyncio.sleep(0.02)

        ticker = asyncio.create_task(tick())
        try:
            yield
        finally:
            await runner.close()
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    app = FastAPI(title=world.spec.name, lifespan=lifespan)
    app.state.world, app.state.runner = world, runner

    @app.middleware("http")
    async def origin_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Only permit same-origin browser commands, matching Control Studio."""
        if (
            request.method == "POST"
            and (origin := request.headers.get("origin"))
            and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ControlError)
    async def conflict(request: Request, error: ControlError) -> JSONResponse:
        """Expose actionable machine or scheduling rejections."""
        return JSONResponse({"detail": error.reason}, status_code=409)

    @app.get("/api/scene")
    def scene() -> dict:
        """Serve static geometry, camera bounds and user-visible station labels."""
        return dict(
            **geometry.document,
            bounds=world.bounds.tolist(),
            name=world.spec.name,
            stations={
                k: {"label": s.name, "point": world.targets[k].tolist()}
                for k, s in world.spec.stations.items()
            },
        )

    @app.get("/assets/mesh/{index}.bin")
    def mesh(index: int) -> Response:
        """Serve a single shared little-endian mesh buffer."""
        if index not in geometry.meshes:
            raise HTTPException(404)
        return Response(geometry.meshes[index], media_type="application/octet-stream")

    @app.get("/api/state")
    async def state() -> dict:
        """Snapshot transforms, item custody and compact per-machine journals."""
        return {
            "poses": geometry.poses(world.data),
            "inventory": world.inventory(),
            "machines": {
                k: {"name": r.definition.name, "task": runner.snapshot(k)} for k, r in machines.items()
            },
        }

    @app.get("/api/capabilities/{machine_id}")
    async def capabilities(machine_id: str) -> dict:
        """Return the same action package that the agent discovers."""
        return registry.get(machine_id).action_execution.package.model_dump(mode="json")

    @app.post("/api/tasks")
    async def start(goal: Goal) -> dict:
        """Assign natural-language work to the named machine."""
        registry.get(goal.machine_id)
        availability = workers.members[goal.machine_id].availability()
        if agent is None and not availability["available"]:
            raise HTTPException(503, availability["message"])
        return runner.start(goal.machine_id, goal.goal, model=goal.model)

    @app.post("/api/machines/{machine_id}/stop")
    async def stop(machine_id: str) -> dict | None:
        """Cancel one machine while keeping its physical custody and peers intact."""
        registry.get(machine_id)
        return runner.cancel(machine_id)

    @app.get("/api/tasks/{task_id}")
    async def evidence(task_id: str) -> dict:
        """Read the persisted task with action results and observed final inventory."""
        return runner.get(task_id)

    @app.get("/{asset:path}")
    def static(asset: str) -> FileResponse:
        """Expose only the page, viewer and locally installed Three.js modules."""
        files = {
            "": WEB / "index.html",
            "viewer.js": WEB / "viewer.js",
            "three.js": worker.parent / "node_modules/three/build/three.module.js",
            "three.core.js": worker.parent / "node_modules/three/build/three.core.js",
            "orbit.js": worker.parent / "node_modules/three/examples/jsm/controls/OrbitControls.js",
        }
        if asset not in files or not files[asset].is_file():
            raise HTTPException(404)
        return FileResponse(files[asset])

    return app

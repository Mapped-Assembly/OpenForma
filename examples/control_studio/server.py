"""Optional local HTTP/WebSocket host for the reusable Forma control core."""
import asyncio
from contextlib import asynccontextmanager
import json
import importlib.util
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from forma_core.control import COMMAND_ADAPTER, ControlError, MachineDefinition, MachineRegistry, MachineRuntime
from forma_core.control.package import load_machine_package
from forma_core.control.tasks import MachineTaskRunner
from forma_core.control.opencode_worker import OpenCodeTaskAgent
from forma_core.firmware.sil.adapter import FirmwareSilAdapter
from forma_core.firmware.sil.build import library_name
from forma_core.firmware.sil.simulator import FirmwareProfile

ROOT = Path(__file__).resolve().parent
PREFIX = "/api/control/v2/machines"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["human", "agent", "joystick"] = "human"
    takeover: bool = False


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)


class SimulationAction(Session):
    op: Literal["disconnect", "reconnect", "stall", "unstall", "limit_min", "limit_max", "clear_limits", "reset"]
    axis: str | None = None


class ToolTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_mm: list[FiniteFloat] = Field(min_length=3, max_length=3)


class TaskGoal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    reset: bool = False
    model: str | None = Field(default=None, min_length=3, max_length=200, pattern=r"^[^/\s]+/[^\s]+$")


def create_app(registry=None, *, package_paths=(), task_agent=None, task_directory=None):
    model_assets = {}

    def bind_viewer(package, root, machine_id=None):
        assets = [a for a in package.artifacts if a.role == "viewer-scene"]
        if len(assets) > 1:
            raise ValueError("A machine package may have one viewer-scene artifact.")
        if assets:
            model_assets[machine_id or package.definition.machine_id] = (root/assets[0].path).resolve()

    if registry is None:
        registry = MachineRegistry()
        for file in sorted((ROOT/"definitions").glob("*.json")):
            definition = MachineDefinition.model_validate_json(file.read_text())
            package_path = ROOT/"generated"/definition.machine_id/"machine-package.json"
            package = load_machine_package(package_path) if package_path.is_file() else None
            registry.add(MachineRuntime(definition, package=package))
            if package:
                bind_viewer(package, package_path.parent)
            library = ROOT/"generated/firmware"/library_name()
            profile_path = ROOT/"profiles"/file.name
            if library.is_file() and profile_path.is_file():
                profile = FirmwareProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
                sil = definition.model_copy(update={"machine_id": definition.machine_id+"-sil", "name": definition.name+" · Firmware SIL",
                    "description": "Compiled C++ controller drives simulated " + ("servo PWM." if len(definition.axes) == 1 else "step/direction outputs.")})
                adapter = FirmwareSilAdapter(sil, profile, library)
                sil_package = None
                if package:
                    from forma_core.control.package import MachinePackage
                    body = package.model_dump(mode="json")
                    body.update(definition=sil.model_dump(mode="json"), adapter=adapter.describe().model_dump(mode="json"))
                    sil_package = MachinePackage.model_validate(body)
                registry.add(MachineRuntime(sil, adapter, package=sil_package))
                if sil_package:
                    bind_viewer(sil_package, package_path.parent, sil.machine_id)
        scene = ROOT/"generated/so101/pick-place.xml"
        if scene.is_file() and (ROOT/"web/assets/so101-scene.json").is_file() and importlib.util.find_spec("mujoco"):
            from forma_core.control.so101 import SO101Adapter
            robot = SO101Adapter(scene)
            package_path = scene.parent/"machine-package.json"
            package = load_machine_package(package_path) if package_path.is_file() else None
            registry.add(MachineRuntime(robot.definition(), robot, package=package))
            if package:
                bind_viewer(package, package_path.parent)

    for path in package_paths:
        path = Path(path)
        package = load_machine_package(path)
        adapter = None
        if package.adapter.adapter_id == "forma.so101-mujoco":
            from forma_core.control.so101 import SO101Adapter
            scenes = [a for a in package.artifacts if a.role == "mujoco-scene"]
            if len(scenes) != 1:
                raise ValueError("SO-101 package requires exactly one hashed mujoco-scene artifact.")
            adapter = SO101Adapter(path.parent/scenes[0].path)
        elif package.adapter.adapter_id != "forma.ideal":
            raise ValueError("Register this adapter explicitly through MachineRegistry.add_package().")
        registry.add_package(package, adapter)
        bind_viewer(package, path.parent)

    task_agent = task_agent or OpenCodeTaskAgent(ROOT/"task_worker.mjs", ROOT/"generated/agent-workspace", env_files=[ROOT.parent.parent/".env"])
    tasks = MachineTaskRunner(registry, task_agent, task_directory or ROOT/"generated/tasks")

    @asynccontextmanager
    async def lifespan(app):
        async def run():
            while True:
                for machine in registry.all():
                    machine.tick()
                await asyncio.sleep(min(m.definition.timing.update_ms for m in registry.all())/1000)
        task = asyncio.create_task(run())
        yield
        await tasks.close()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for machine in registry.all():
            if machine.owner:
                machine.release(machine.owner["session_id"])
            if hasattr(machine.adapter, "close"):
                machine.adapter.close()

    app = FastAPI(title="Forma Control Studio", version="0.2.0", lifespan=lifespan)
    app.state.registry = registry
    app.state.tasks = tasks

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
            return JSONResponse({"reason": "origin_not_allowed"}, status_code=403)
        response = await call_next(request)
        # This local development host must not mix a cached controller bundle
        # with new HTML or show stale task/telemetry responses after a rebuild.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ControlError)
    async def control_error(request, error):
        return JSONResponse({"accepted": False, "reason": error.reason}, status_code=404 if error.reason == "unknown_machine" else 409)

    @app.get(PREFIX)
    async def machines():
        return [m.definition.model_dump(mode="json") for m in registry.all()]

    @app.get("/api/control/v2/task-runner")
    async def task_runner():
        return task_agent.availability() if hasattr(task_agent, "availability") else dict(available=True, kind="injected")

    @app.post(PREFIX+"/{machine_id}/tasks")
    async def start_task(machine_id: str, body: TaskGoal):
        if hasattr(task_agent, "availability") and not task_agent.availability()["available"]:
            return JSONResponse({"reason": task_agent.availability()["message"]}, status_code=503)
        return tasks.start(machine_id, body.goal, session_id=body.session_id, reset=body.reset, model=body.model)

    @app.get(PREFIX+"/{machine_id}/tasks/latest")
    async def latest_task(machine_id: str):
        registry.get(machine_id)
        return tasks.snapshot(machine_id)

    @app.post(PREFIX+"/{machine_id}/tasks/stop")
    async def stop_task(machine_id: str):
        registry.get(machine_id)
        return tasks.cancel(machine_id)

    @app.get(PREFIX+"/{machine_id}/tasks/{task_id}")
    async def task_record(machine_id: str, task_id: str):
        record = tasks.get(task_id)
        if record["machine_id"] != machine_id:
            raise ControlError("unknown_task")
        return record

    @app.get(PREFIX+"/{machine_id}/capabilities")
    async def capabilities(machine_id: str):
        machine = registry.get(machine_id)
        return dict(definition=machine.definition.model_dump(mode="json"), protocol_version=2,
                     command_schema=COMMAND_ADAPTER.json_schema(),
                      tool_ik=hasattr(machine.adapter, "inverse_kinematics"),
                      simulation_reset=machine.definition.backend == "virtual" and callable(getattr(machine.adapter, "fresh", None)),
                     machine_package=machine.action_execution.package.model_dump(mode="json") if machine.action_execution else None,
                     coordination="Common linear progress; interrupting any member freezes the entire previous coordinated group.")

    @app.get(PREFIX+"/{machine_id}/actions/{run_id}")
    async def action_result(machine_id: str, run_id: str):
        machine = registry.get(machine_id)
        machine.tick()
        result = machine.action_execution.snapshot(run_id) if machine.action_execution else None
        if result is None:
            return JSONResponse({"reason": "unknown_action_run"}, status_code=404)
        return result

    @app.post(PREFIX+"/{machine_id}/ik")
    async def inverse_kinematics(machine_id: str, body: ToolTarget):
        # A pure solver, not a motion command. Execution still uses the ordinary
        # owner/lease/sequence/rate/limit-checked v2 move command.
        adapter = registry.get(machine_id).adapter
        if not hasattr(adapter, "inverse_kinematics"):
            raise ControlError("tool_ik_unavailable")
        try:
            targets = adapter.inverse_kinematics(body.target_mm)
        except ValueError as error:
            raise ControlError(str(error)) from None
        return dict(targets=targets, target_mm=body.target_mm, executed=False,
                    path="Coordinated joint interpolation; not a Cartesian straight-line or collision-free path.")

    @app.get(PREFIX+"/{machine_id}/state")
    async def state(machine_id: str):
        machine = registry.get(machine_id)
        machine.tick()
        return dict(telemetry=machine.snapshot(), events=list(machine.events)[-20:], task=tasks.snapshot(machine_id))

    @app.get(PREFIX+"/{machine_id}/model")
    async def model_asset(machine_id: str):
        machine = registry.get(machine_id)
        file = model_assets.get(machine_id)
        if file is None:
            root = (ROOT/"web/assets").resolve()
            file = (root/(machine.definition.model_asset or "missing")).resolve()
            if not file.is_relative_to(root):
                raise ControlError("invalid_model_asset")
        if not file.is_file():
            return JSONResponse({"reason": "model_asset_unavailable"}, status_code=404)
        return FileResponse(file, media_type="application/json")

    @app.post(PREFIX+"/{machine_id}/claim")
    async def claim(machine_id: str, body: Claim):
        if body.takeover:
            tasks.cancel(machine_id, reason="manual_takeover")
        return registry.get(machine_id).claim(body.name, kind=body.kind, takeover=body.takeover)

    @app.post(PREFIX+"/{machine_id}/lease")
    async def lease(machine_id: str, body: Session):
        return registry.get(machine_id).lease(body.session_id)

    @app.post(PREFIX+"/{machine_id}/release")
    async def release(machine_id: str, body: Session):
        registry.get(machine_id).release(body.session_id)
        return {"released": True}

    @app.post(PREFIX+"/{machine_id}/command")
    async def command(machine_id: str, request: Request):
        try:
            packet = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise ControlError("invalid_command") from None
        return registry.get(machine_id).command(packet)

    @app.post(PREFIX+"/{machine_id}/simulation")
    async def simulation(machine_id: str, body: SimulationAction):
        machine = registry.get(machine_id)
        machine._require_owner(body.session_id)
        adapter = machine.adapter
        if hasattr(adapter, "inverse_kinematics") and hasattr(adapter, "fresh"):
            if body.op != "reset" or body.axis is not None:
                raise ControlError("unsupported_task_operation")
            fresh = adapter.fresh()
            machine.release(body.session_id)
            machine.replace_adapter(fresh)
            adapter.close()
            return dict(telemetry=machine.snapshot())
        if not isinstance(adapter, FirmwareSilAdapter):
            raise ControlError("not_a_firmware_simulation")
        axis_operations = {"stall", "unstall", "limit_min", "limit_max", "clear_limits"}
        if body.op in axis_operations:
            if body.axis not in adapter.injections:
                raise ControlError("unknown_axis")
            if body.op.startswith("limit") and adapter.profile.axes[body.axis].kind != "stepper":
                raise ControlError("axis_has_no_limit_switches")
            changes = {"stall": {"stalled": True}, "unstall": {"stalled": False},
                       "limit_min": {"minimum": True}, "limit_max": {"maximum": True},
                       "clear_limits": {"minimum": False, "maximum": False}}
            adapter.inject(body.axis, **changes[body.op])
        elif body.axis is not None:
            raise ControlError("unexpected_axis")
        elif body.op == "reset":
            fresh = adapter.fresh()
            machine.release(body.session_id)
            machine.replace_adapter(fresh)
            adapter.close()
        else:
            adapter.link_connected = body.op == "reconnect"
        machine.tick()
        return dict(telemetry=machine.snapshot())

    @app.websocket(PREFIX+"/{machine_id}/stream")
    async def stream(machine_id: str, ws: WebSocket):
        if ws.headers.get("origin") not in (None, f'http://{ws.headers.get("host")}'):
            await ws.close(code=1008); return
        try:
            machine = registry.get(machine_id)
        except ControlError:
            await ws.close(code=1008); return
        await ws.accept()
        session_id = ws.query_params.get("session_id")
        try:
            while True:
                data = dict(telemetry=machine.snapshot(), events=list(machine.events)[-20:], task=tasks.snapshot(machine_id))
                if machine.owner and session_id == machine.owner["session_id"]:
                    data["lease"] = machine.lease(session_id)
                await ws.send_json(data)
                await asyncio.sleep(machine.definition.timing.telemetry_ms/1000)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    app.mount("/", StaticFiles(directory=ROOT/"web", html=True), name="studio")
    return app


if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--machine-package", action="append", default=[], help="Additional validated package (ideal or SO-101 adapter).")
    args = parser.parse_args()
    uvicorn.run(create_app(package_paths=args.machine_package), host="127.0.0.1", port=args.port, log_level="warning")

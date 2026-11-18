"""Optional local HTTP/WebSocket host for the reusable Forma control core."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from forma_core.control import COMMAND_ADAPTER, ControlError, MachineDefinition, MachineRegistry, MachineRuntime
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


def create_app(registry=None):
    if registry is None:
        registry = MachineRegistry()
        for file in sorted((ROOT/"definitions").glob("*.json")):
            definition = MachineDefinition.model_validate_json(file.read_text())
            registry.add(MachineRuntime(definition))
            library = ROOT/"generated/firmware"/library_name()
            profile_path = ROOT/"profiles"/file.name
            if library.is_file() and profile_path.is_file():
                profile = FirmwareProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
                sil = definition.model_copy(update={"machine_id": definition.machine_id+"-sil", "name": definition.name+" · Firmware SIL",
                    "description": "Compiled C++ controller drives simulated " + ("servo PWM." if len(definition.axes) == 1 else "step/direction outputs.")})
                registry.add(MachineRuntime(sil, FirmwareSilAdapter(sil, profile, library)))

    @asynccontextmanager
    async def lifespan(app):
        async def run():
            while True:
                for machine in registry.all():
                    machine.tick()
                await asyncio.sleep(min(m.definition.timing.update_ms for m in registry.all())/1000)
        task = asyncio.create_task(run())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for machine in registry.all():
            if machine.owner:
                machine.release(machine.owner["session_id"])
            if isinstance(machine.adapter, FirmwareSilAdapter):
                machine.adapter.close()

    app = FastAPI(title="Forma Control Studio", version="0.2.0", lifespan=lifespan)
    app.state.registry = registry

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
            return JSONResponse({"reason": "origin_not_allowed"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ControlError)
    async def control_error(request, error):
        return JSONResponse({"accepted": False, "reason": error.reason}, status_code=404 if error.reason == "unknown_machine" else 409)

    @app.get(PREFIX)
    async def machines():
        return [m.definition.model_dump(mode="json") for m in registry.all()]

    @app.get(PREFIX+"/{machine_id}/capabilities")
    async def capabilities(machine_id: str):
        machine = registry.get(machine_id)
        return dict(definition=machine.definition.model_dump(mode="json"), protocol_version=2,
                    command_schema=COMMAND_ADAPTER.json_schema(),
                    coordination="Common linear progress; interrupting any member freezes the entire previous coordinated group.")

    @app.get(PREFIX+"/{machine_id}/state")
    async def state(machine_id: str):
        machine = registry.get(machine_id)
        machine.tick()
        return dict(telemetry=machine.snapshot(), events=list(machine.events)[-20:])

    @app.post(PREFIX+"/{machine_id}/claim")
    async def claim(machine_id: str, body: Claim):
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
                data = dict(telemetry=machine.snapshot(), events=list(machine.events)[-20:])
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
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")

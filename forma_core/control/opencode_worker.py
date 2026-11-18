"""Optional OpenCode SDK subprocess boundary; no model calls in the control loop."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil


class OpenCodeTaskAgent:
    def __init__(self, worker: Path, workspace: Path, *, env_files=()):
        self.worker, self.workspace = Path(worker).resolve(), Path(workspace).resolve()
        self.env_files = [Path(path) for path in env_files] + [self.worker.parent/".env.task"]
        self.workspace.mkdir(parents=True, exist_ok=True)
        local = self.worker.parent/"node_modules/bun/bin"
        self.executable = next((str(local/name) for name in ("bun.exe", "bun") if (local/name).is_file()), None) or shutil.which("bun")

    def availability(self):
        installed = self.executable is not None and (self.worker.parent/"node_modules/@opencode/sdk/package.json").is_file()
        return dict(available=installed, kind="opencode-sdk", message="OpenCode SDK worker installed" if installed else "Run npm ci in examples/control_studio to install the task worker.")

    async def run(self, task, invoke, progress):
        if not self.availability()["available"]:
            raise RuntimeError(self.availability()["message"])
        # Reload local worker credentials for each task; never expose them in
        # telemetry, the journal, or the JSON-lines protocol.
        env = dict(os.environ)
        for env_file in self.env_files:
            if env_file.is_file():
                from dotenv import dotenv_values
                env.update({k: v for k, v in dotenv_values(env_file).items() if v})
        process = await asyncio.create_subprocess_exec(self.executable, str(self.worker), cwd=self.worker.parent, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024*1024)
        errors = []

        async def stderr():
            while chunk := await process.stderr.read(4096):
                errors.append(chunk.decode(errors="replace"))
                del errors[:-3]

        error_task = asyncio.create_task(stderr())

        async def send(packet):
            process.stdin.write((json.dumps(packet, allow_nan=False)+"\n").encode())
            await process.stdin.drain()

        try:
            await send(dict(type="init", directory=str(self.workspace), goal=task["goal"], model=task.get("model") or env.get("FORMA_TASK_MODEL")))
            while line := await process.stdout.readline():
                if not line.startswith(b"FORMA_TASK "):
                    continue
                packet = json.loads(line[len(b"FORMA_TASK "):])
                kind = packet.get("type")
                if kind in {"ready", "thinking"}:
                    progress(kind, packet)
                elif kind == "tool":
                    try:
                        result = await invoke(packet["name"], packet["input"])
                        await send(dict(type="result", id=packet["id"], result=result))
                        if packet["name"] == "finish_task":
                            return
                    except ValueError as error:
                        await send(dict(type="result", id=packet["id"], error=str(error)))
                elif kind == "error":
                    raise RuntimeError("OpenCode worker: "+str(packet.get("message"))[:2000])
                elif kind == "ended":
                    return
            code = await process.wait()
            raise RuntimeError(f"OpenCode worker exited ({code}) without finishing the task. "+"".join(errors)[-1500:])
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            error_task.cancel()
            await asyncio.gather(error_task, return_exceptions=True)

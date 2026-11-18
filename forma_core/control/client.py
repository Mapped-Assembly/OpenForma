"""Standard-library client for agent tools or non-browser input bridges.

The HTTP host is optional. This client has no autonomous background thread:
long-lived callers must heartbeat; move_and_wait does so while it waits.
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ClientError(RuntimeError):
    def __init__(self, reason: str, status: int):
        self.reason, self.status = reason, status
        super().__init__(f"{reason} (HTTP {status})")


class ControlClient:
    def __init__(self, url: str, machine_id: str):
        self.base = url.rstrip("/")+"/api/control/v2/machines/"+quote(machine_id, safe="")
        self.session_id = None
        self.seq = 0

    def _request(self, path, body=None):
        request = Request(self.base+path, data=None if body is None else json.dumps(body, allow_nan=False).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                reason = json.load(error).get("reason", "request_failed")
            except ValueError:
                reason = "request_failed"
            raise ClientError(reason, error.code) from error

    def capabilities(self):
        return self._request("/capabilities")

    def claim(self, name="Agent client", *, kind="agent", takeover=False):
        result = self._request("/claim", dict(name=name, kind=kind, takeover=takeover))
        self.session_id, self.seq = result["session_id"], 0
        return result

    def command(self, op, **parameters):
        if self.session_id is None:
            raise RuntimeError("Claim machine control before sending commands.")
        lease = self._request("/lease", {"session_id": self.session_id})
        packet = dict(protocol_version=2, session_id=self.session_id, lease_id=lease["lease_id"], seq=self.seq, op=op, **parameters)
        self.seq += 1
        return self._request("/command", packet)

    def state(self):
        return self._request("/state")["telemetry"]

    def release(self):
        if self.session_id is not None:
            session = self.session_id
            self.session_id = None
            return self._request("/release", {"session_id": session})
        return None

    def move_and_wait(
        self, targets: dict[str, float], *,
        wait_for: Literal["commanded", "simulated", "measured"],
        rates: dict[str, float] | None = None, coordinated: bool = True,
        timeout: float = 30, tolerance: float = 1e-6,
    ) -> dict[str, Any]:
        """Move and await the explicitly selected position source.

        Commanded arrival only confirms the host plan. Simulated or measured
        arrival requires feedback from that source; neither falls back to commands.
        Omitting wait_for raises TypeError before any command is sent.
        """
        if wait_for not in ("commanded", "simulated", "measured") or not 0 < tolerance < float("inf"):
            raise ValueError("Select an explicit position source and a finite positive tolerance.")
        reply = self.command("move", targets=targets, rates=rates or {}, coordinated=coordinated)
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            self.command("heartbeat")
            state = self.state()
            if state["state"] != "ARMED":
                raise RuntimeError(f"Motion interrupted: {state['state']}")
            if state["ack_seq"] >= reply["ack_seq"] and all(state["axes"][a][wait_for] is not None and abs(state["axes"][a][wait_for]-v) <= tolerance for a, v in targets.items()):
                return state  # The caller explicitly selects feedback provenance.
            time.sleep(.1)
        self.command("hold")
        raise TimeoutError("Command motion did not complete before timeout.")

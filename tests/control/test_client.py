"""Verify completion provenance with a moving plan and a stalled virtual plant."""
from collections.abc import Mapping
from typing import Any
import unittest
from unittest.mock import patch

from forma_core.control import ControlClient, MachineRuntime, VirtualAdapter
from tests.control.test_runtime import definition


class StalledAdapter(VirtualAdapter):
    """Keep simulated axes at zero while accepting the runtime's commands."""

    def apply(self, commands: Mapping[str, float | None], enabled: bool) -> None:
        """Report a stationary plant independently of commanded positions."""
        super().apply({axis: 0.0 for axis in commands}, enabled)


class RuntimeClient(ControlClient):
    """Exercise the real client wait loop against an in-process runtime."""

    def __init__(self, runtime: MachineRuntime) -> None:
        """Bind a runtime in place of HTTP transport."""
        super().__init__("http://unused", "xy")
        self.runtime = runtime

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch client requests through the runtime's public operations."""
        if path == "/claim":
            return self.runtime.claim(**body)
        if path == "/lease":
            return self.runtime.lease(body["session_id"])
        if path == "/command":
            return self.runtime.command(body)
        if path == "/state":
            self.runtime.tick()
            return {"telemetry": self.runtime.snapshot()}
        raise AssertionError(path)


class CompletionTests(unittest.TestCase):
    """Prevent a completed command from silently proving plant arrival."""

    def setUp(self) -> None:
        """Create a deterministic clock and stalled virtual mechanism."""
        self.now = 0.0
        self.runtime = MachineRuntime(
            definition(), StalledAdapter(definition()), clock=lambda: self.now
        )
        self.client = RuntimeClient(self.runtime)
        self.client.claim()
        self.client.command("arm")

    def advance(self, seconds: float) -> None:
        """Advance the wait loop clock without sleeping."""
        self.now += seconds

    def test_missing_source_sends_no_motion(self) -> None:
        """Require the completion decision before changing the machine."""
        before = self.runtime.snapshot()
        with self.assertRaises(TypeError):
            self.client.move_and_wait({"x": 1})
        self.assertEqual(self.runtime.snapshot(), before)

    def test_explicit_commanded_arrival_can_complete_with_stalled_plant(self) -> None:
        """Host-plan completion remains available as an explicit choice."""
        with patch("forma_core.control.client.time.monotonic", lambda: self.now), patch(
            "forma_core.control.client.time.sleep", self.advance
        ):
            state = self.client.move_and_wait({"x": 1}, wait_for="commanded")
        self.assertEqual(state["axes"]["x"]["commanded"], 1)
        self.assertEqual(state["axes"]["x"]["simulated"], 0)

    def test_feedback_sources_never_fall_back_to_commands(self) -> None:
        """Stalled simulation and absent measurements time out and hold."""
        for source in ("simulated", "measured"):
            with self.subTest(source=source):
                self.client.command("arm")
                with patch("forma_core.control.client.time.monotonic", lambda: self.now), patch(
                    "forma_core.control.client.time.sleep", self.advance
                ):
                    with self.assertRaises(TimeoutError):
                        self.client.move_and_wait({"x": 1}, wait_for=source, timeout=.5)
                self.assertEqual(self.runtime.state, "HOLD")
                self.assertEqual(self.runtime.commanded["x"], 1)


if __name__ == "__main__":
    unittest.main()

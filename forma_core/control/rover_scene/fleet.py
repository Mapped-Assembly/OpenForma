"""Route shared mobile bases and serialize custody-changing actions."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import math
import time
from collections.abc import Callable

import numpy as np

from forma_core.control.models import MachineDefinition
from forma_core.control.package import MachinePackage
from forma_core.control.runtime import ControlError, MachineRuntime
from forma_core.control.tasks import MachineTaskRunner, Task

from .machine import Rover
from .scene import Scene

Point = tuple[float, float]

SEPARATION_MM = 800.0


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Measure clearance from a base center to a reserved segment."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) if length else 0.0
    return math.hypot(p[0] - a[0] - fraction * dx, p[1] - a[1] - fraction * dy)


def segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """Return zero for crossing routes, otherwise their closest distance."""

    def cross(x: Point, y: Point) -> float:
        """Compute the signed 2D cross product."""
        return x[0] * y[1] - x[1] * y[0]

    u, v = (b[0] - a[0], b[1] - a[1]), (d[0] - c[0], d[1] - c[1])
    determinant = cross(u, v)
    if abs(determinant) > 1e-8:
        offset = (c[0] - a[0], c[1] - a[1])
        t, s = cross(offset, v) / determinant, cross(offset, u) / determinant
        if 0 <= t <= 1 and 0 <= s <= 1:
            return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def plan_route(world: Scene, machine_id: str, goal: Point) -> list[Point] | None:
    """Grid A* around peer bases and reserved corridors, in millimeters.

    This is a coarse fleet exclusion model, not whole-body collision planning.
    Planning/reservation run synchronously on the single owning event loop.
    """
    adapter = world.adapters[machine_id]
    start = (adapter.positions["base_x"], adapter.positions["base_y"])
    goal = tuple(goal)
    obstacles = []
    for key, other in world.adapters.items():
        if key == machine_id:
            continue
        p = (other.positions["base_x"], other.positions["base_y"])
        obstacles.append((p, p))
        route = world.routes.get(key, [])
        obstacles.extend(itertools.pairwise(route))

    def clear(a: Point, b: Point) -> bool:
        """Check a prospective segment against each occupied corridor."""
        return all(segment_distance(a, b, c, d) >= SEPARATION_MM - 1e-6 for c, d in obstacles)

    if not clear(goal, goal):
        return None
    if clear(start, goal):
        return [start, goal]
    # The scene declares a rectangular, obstacle-free drive aisle.
    axes = {a.axis_id: a for a in adapter.definition.axes}
    xs = np.arange(axes["base_x"].minimum, axes["base_x"].maximum + 1, 200.0)
    ys = np.arange(axes["base_y"].minimum, axes["base_y"].maximum + 1, 200.0)
    grid = {(i, j): (float(x), float(y)) for i, x in enumerate(xs) for j, y in enumerate(ys)}
    valid = {key for key, value in grid.items() if clear(value, value)}
    # Virtual start/goal nodes connect to nearby visible grid points.
    links = {key for key in valid if math.dist(start, grid[key]) <= 350 and clear(start, grid[key])}
    frontier, costs, previous = [], {}, {}
    for key in links:
        costs[key] = math.dist(start, grid[key])
        previous[key] = None
        heapq.heappush(frontier, (costs[key] + math.dist(grid[key], goal), key))
    visited = set()
    while frontier:
        _, key = heapq.heappop(frontier)
        if key in visited:
            continue
        visited.add(key)
        here = grid[key]
        if math.dist(here, goal) <= 350 and clear(here, goal):
            path = [goal, here]
            while previous[key] is not None:
                key = previous[key]
                path.append(grid[key])
            path.append(start)
            path.reverse()
            # Remove redundant grid corners only when the shortcut stays clear.
            result = [path[0]]
            cursor = 0
            while cursor < len(path) - 1:
                candidate = len(path) - 1
                while candidate > cursor + 1 and not clear(path[cursor], path[candidate]):
                    candidate -= 1
                result.append(path[candidate])
                cursor = candidate
            return result
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                neighbor = (key[0] + di, key[1] + dj)
                if (di == dj == 0) or neighbor not in valid or not clear(here, grid[neighbor]):
                    continue
                cost = costs[key] + math.dist(here, grid[neighbor])
                if cost < costs.get(neighbor, float("inf")):
                    costs[neighbor], previous[neighbor] = cost, key
                    heapq.heappush(frontier, (cost + math.dist(grid[neighbor], goal), neighbor))
    return None


class RoutedRuntime(MachineRuntime):
    """Execute reserved waypoints within one feedback-backed Forma action."""

    def __init__(
        self,
        definition: MachineDefinition,
        adapter: Rover,
        *,
        clock: Callable = time.monotonic,
        package: MachinePackage,
    ) -> None:
        """Use the existing Forma interpolator between reserved waypoints."""
        self.waypoints = []
        super().__init__(definition, adapter, clock=clock, package=package)

    def _move(self, targets: dict, rates: dict, coordinated: bool) -> None:
        """Reserve the complete path before issuing its first segment."""
        self.waypoints.clear()
        if {"base_x", "base_y"} <= targets.keys():
            goal = (targets["base_x"], targets["base_y"])
            route = self.adapter.world.routes.get(self.definition.machine_id)
            if not route or tuple(route[-1]) != goal:
                route = plan_route(self.adapter.world, self.definition.machine_id, goal)
                if route is None:
                    raise ControlError("route_busy")
                self.adapter.world.routes[self.definition.machine_id] = route
            self.waypoints = [
                ({**targets, "base_x": point[0], "base_y": point[1]}, rates, coordinated)
                for point in route[1:]
            ]
            targets, rates, coordinated = self.waypoints.pop(0)
        super()._move(targets, rates, coordinated)

    def _output(self) -> bool:
        """Keep the intermediate path inside one action evidence record."""
        if self.state != "ARMED":
            self.waypoints.clear()
        elif not self.plans and self.waypoints:
            targets, rates, coordinated = self.waypoints.pop(0)
            super()._move(targets, rates, coordinated)
        return super()._output()


class FleetRunner(MachineTaskRunner):
    """Guard resource transitions across concurrent task workers on one host loop."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize per-machine dispatch locks."""
        super().__init__(*args, **kwargs)
        self.locks: dict[str, asyncio.Lock] = {}

    async def tool(self, task: Task, name: str, inputs: dict) -> dict:
        """Reserve a route and/or manipulate only through the bound machine."""
        machine = self._require_active(task)
        rover = machine.adapter
        lock = self.locks.setdefault(rover.machine_id, asyncio.Lock())
        if name == "finish_task" and lock.locked():
            raise ControlError("action_in_progress")
        if name != "execute_action" or not isinstance(inputs, dict):
            return await super().tool(task, name, inputs)
        async with lock:
            self._require_active(task)
            try:
                verb, key = rover.preflight(inputs.get("action_id", ""))
                if verb in ("drive", "home"):
                    goal = rover.home if verb == "home" else rover.world.docks[key]
                    end = time.monotonic() + 12
                    while (route := plan_route(rover.world, rover.machine_id, goal * 1000)) is None:
                        if time.monotonic() >= end:
                            raise ControlError("route_busy: observe peers and retry after they move")
                        await asyncio.sleep(0.15)
                        self._require_active(task)
                    rover.world.routes[rover.machine_id] = route
            except ControlError as error:
                self._require_active(task)
                self._event(task, "action_rejected", error.reason, action_id=inputs.get("action_id"))
                raise
            rover.intent = (verb, key)
            try:
                return await super().tool(task, name, inputs)
            finally:
                rover.intent = None
                rover.world.routes.pop(rover.machine_id, None)

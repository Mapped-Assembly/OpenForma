"""Bind a single rectangular, metric OpenIndustries workcell to a rover scenario."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Literal

import mujoco
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from .assets import validate_assets
from .robot import add_rovers

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
Color = tuple[
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
]


class Record(BaseModel):
    """Reject unknown operations or accidental misspellings in scenario data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExecutionRequirements(Record):
    """Required execution semantics; unsupported requests must never degrade silently."""

    fidelity: Literal["kinematic", "contact-dynamics"] = "kinematic"
    capabilities: list[str] = Field(default_factory=list)

    def check(self) -> None:
        """Reject semantics the shared rover backend does not implement."""
        if self.fidelity != "kinematic":
            raise ValueError("unsupported_fidelity: rover scenes support kinematic execution only")
        missing = set(self.capabilities) - set(EXECUTION_CAPABILITIES)
        if missing:
            raise ValueError("unsupported_capabilities: " + ", ".join(sorted(missing)))


EXECUTION_CAPABILITIES = ("mobile-base", "arm-positioning", "proximity-transfer")


class Machine(Record):
    """One independently controlled mounted rover at its named home occupancy."""

    profile: Literal["astra-so101"]
    profile_version: Literal[1] = 1
    name: str
    home: Id
    color: Color


class Station(Record):
    """A single item slot with a reachable approach from the aisle."""

    name: str
    footprint: Id
    height_m: Annotated[FiniteFloat, Field(gt=0.08)]
    dock_offset_m: tuple[FiniteFloat, FiniteFloat]
    surface: Literal["counter", "plate"] = "counter"


class Item(Record):
    """A transportable ingredient or component, initially occupying one slot."""

    name: str
    station: Id
    color: Color
    radius_m: Annotated[FiniteFloat, Field(gt=0.005, le=0.05)] = 0.028


class Scenario(Record):
    """Portable workload binding a SpaceIR document to machines and inventory."""

    version: Literal[1]
    name: str
    space: str
    aisle: Id
    execution: ExecutionRequirements = Field(default_factory=ExecutionRequirements)
    fixtures: dict[Id, str] = Field(default_factory=dict)
    machines: dict[Id, Machine] = Field(min_length=1)
    stations: dict[Id, Station] = Field(min_length=1)
    items: dict[Id, Item] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bindings(self) -> Scenario:
        """Validate semantic references before geometry or asset loading."""
        self.execution.check()
        occupied: set[str] = set()
        for key, item in self.items.items():
            if item.station not in self.stations:
                raise ValueError(f"items.{key}.station: unknown station {item.station}")
            if item.station in occupied:
                raise ValueError(f"items.{key}.station: slot already occupied {item.station}")
            occupied.add(item.station)
        return self


def rectangle(shape: dict) -> np.ndarray:
    """Convert an axis-aligned OI world X/Z rectangle into MuJoCo XY bounds."""
    if shape["frame_id"] != "world":
        raise ValueError("Rover scenes require calibrated world-frame polygons")
    points = np.array(shape["polygon"], dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Expected a four-corner metric occupancy")
    points[:, 1] *= -1
    lo, hi = points.min(0), points.max(0)
    corners = {(x, y) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])}
    if len(corners) != 4 or set(map(tuple, points)) != corners:
        raise ValueError("The rover loader supports axis-aligned rectangles")
    return np.array([lo, hi])


def vector(values: Iterable[float]) -> str:
    """Format an MJCF vector in native units."""
    return " ".join(str(float(value)) for value in values)


class Scene:
    """Own the single MuJoCo state and the locations of all discrete objects."""

    def __init__(self, manifest: Path, assets: Path) -> None:
        """Resolve bindings, build the model once, and verify the initial layout."""
        self.spec = Scenario.model_validate_json(manifest.read_text())
        space_file = Path(self.spec.space)
        if space_file.is_absolute() or ".." in space_file.parts:
            raise ValueError("SpaceIR path must be relative to the scenario directory")
        space = json.loads((manifest.parent / space_file).read_text())
        if (space["schema_id"], space["schema_version"]) != ("mapped-assembly.space-ir", "0.1.0"):
            raise ValueError("Unsupported SpaceIR version")
        if (
            space["frames"] != [{"id": "world", "units": "m", "axes": "XZ"}]
            or len(space["regions"]) != 1
            or space["boundaries"]
            or space["portals"]
        ):
            raise ValueError("Expected one open metric workcell with no internal walls or portals")
        self.bounds = rectangle(space["regions"][0])
        boxes = {obj["id"]: rectangle(obj) for obj in space["occupancies"]}
        if len(boxes) != len(space["occupancies"]):
            raise ValueError("Duplicate SpaceIR occupancy")
        if any((box[0] < self.bounds[0]).any() or (box[1] > self.bounds[1]).any() for box in boxes.values()):
            raise ValueError("Occupancy outside the workcell")
        if any(obj["region_id"] != space["regions"][0]["id"] for obj in space["occupancies"]):
            raise ValueError("Occupancy refers to an unknown region")
        references = {"aisle": self.spec.aisle}
        references.update({f"machines.{k}.home": m.home for k, m in self.spec.machines.items()})
        references.update({f"stations.{k}.footprint": s.footprint for k, s in self.spec.stations.items()})
        references.update({f"fixtures.{k}": k for k in self.spec.fixtures})
        for path, reference in references.items():
            if reference not in boxes:
                raise ValueError(f"{path}: unknown occupancy {reference}")
        self.aisle = boxes[self.spec.aisle]
        self.homes = {key: boxes[m.home].mean(0) for key, m in self.spec.machines.items()}
        self.docks, self.targets = {}, {}
        self.locations: dict[str, str | None] = {key: item.station for key, item in self.spec.items.items()}
        xml = ET.fromstring('<mujoco><compiler angle="radian"/><asset/><worldbody/></mujoco>')
        world = xml.find("worldbody")

        def box(name: str, center: Sequence[float], half: Sequence[float], color: str) -> None:
            """Add fixed furniture geometry in MuJoCo meters."""
            ET.SubElement(
                world, "geom", name=name, type="box", pos=vector(center), size=vector(half), rgba=color
            )

        center = self.bounds.mean(0)
        half = (self.bounds[1] - self.bounds[0]) / 2
        box("floor", [*center, -0.035], [*half, 0.035], ".60 .67 .64 1")
        for occupancy, filename in self.spec.fixtures.items():
            relative = Path(filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Fixture paths must be relative to the scene")
            footprint = boxes[occupancy]
            if np.all(footprint[1] > self.aisle[0]) and np.all(footprint[0] < self.aisle[1]):
                raise ValueError(f"{occupancy} fixture overlaps the drive aisle")
            source = ET.parse(manifest.parent / relative).getroot()
            if source.tag != "body" or any(part.tag != "geom" or list(part) for part in source):
                raise ValueError("A static fixture must contain a single body of primitive geoms")
            source.set("name", "fixture/" + occupancy)
            source.set("pos", vector([*footprint.mean(0), 0]))
            world.append(source)
        for key, station in self.spec.stations.items():
            footprint = boxes[station.footprint]
            if np.all(footprint[1] > self.aisle[0]) and np.all(footprint[0] < self.aisle[1]):
                raise ValueError(f"{key} counter overlaps the drive aisle")
            xy, size = footprint.mean(0), (footprint[1] - footprint[0]) / 2
            h = station.height_m
            self.targets[key] = np.array([*xy, h + 0.032])
            self.docks[key] = xy + station.dock_offset_m
            box(key + "/cabinet", [*xy, (h - 0.024) / 2], [*size * 0.96, (h - 0.024) / 2], ".30 .46 .45 1")
            box(key + "/counter", [*xy, h - 0.012], [*size, 0.012], ".83 .73 .56 1")
            if station.surface == "plate":
                ET.SubElement(
                    world,
                    "geom",
                    type="cylinder",
                    pos=vector([*xy, h + 0.003]),
                    size=".107 .003",
                    rgba=".95 .94 .83 1",
                )
        for key, xy in self.homes.items():
            box(
                key + "/pad", [*xy, 0.002], [0.32, 0.33, 0.002], vector([*self.spec.machines[key].color, 0.7])
            )
        for key, xy in [*self.homes.items(), *self.docks.items()]:
            if (xy < self.aisle[0]).any() or (xy > self.aisle[1]).any():
                raise ValueError(f"{key} outside the drive aisle")
        for index, xy in enumerate(self.homes.values()):
            if any(np.linalg.norm(xy - other) < 0.8 for other in list(self.homes.values())[:index]):
                raise ValueError("Home positions violate the rover clearance")
        for key, item in self.spec.items.items():
            body = ET.SubElement(
                world, "body", name="item/" + key, mocap="true", pos=vector(self.targets[item.station])
            )
            ET.SubElement(body, "geom", type="sphere", size=str(item.radius_m), rgba=vector([*item.color, 1]))
        self.asset_revisions = validate_assets(assets)
        add_rovers(xml, assets.resolve(), self.spec.machines)
        self.model = mujoco.MjModel.from_xml_string(ET.tostring(xml, encoding="unicode"))
        self.data = mujoco.MjData(self.model)
        self.item_ids = {key: int(self.model.body("item/" + key).mocapid[0]) for key in self.spec.items}
        self.held: dict[str, str] = {}
        self.routes: dict[str, list] = {}
        self.adapters: dict = {}
        mujoco.mj_forward(self.model, self.data)

    def execution_contract(self) -> dict:
        """Publish backend guarantees independently of scene names or UI assumptions."""
        return {
            "version": 1,
            "fidelity": "kinematic",
            "evidence": "ideal-kinematics",
            "attachment": "proximity",
            "capabilities": list(EXECUTION_CAPABILITIES),
            "asset_revisions": self.asset_revisions.copy(),
        }

    def claim(self, machine_id: str, item_id: str) -> bool:
        """Atomically claim a free item on the owning event loop, with one item per rover."""
        if machine_id not in self.adapters or item_id not in self.locations:
            raise ValueError("Unknown machine or item reference")
        if item_id in self.held or machine_id in self.held.values():
            return False
        self.held[item_id] = machine_id
        self.locations[item_id] = None
        return True

    def release(self, machine_id: str, station_id: str) -> bool:
        """Release custody only into an existing empty slot; rejected writes change nothing."""
        if machine_id not in self.adapters or station_id not in self.targets:
            raise ValueError("Unknown machine or station reference")
        item_id = next((key for key, holder in self.held.items() if holder == machine_id), None)
        if item_id is None or station_id in self.locations.values():
            return False
        self.data.mocap_pos[self.item_ids[item_id]] = self.targets[station_id]
        self.locations[item_id] = station_id
        del self.held[item_id]
        return True

    def inventory(self) -> dict:
        """Return the current custody, slot, and observed position of every item."""
        return {
            key: {
                "label": item.name,
                "station": self.locations[key],
                "holder": self.held.get(key),
                "position_m": self.data.mocap_pos[self.item_ids[key]].tolist(),
            }
            for key, item in self.spec.items.items()
        }

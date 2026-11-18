"""Compose the licensed ASTRA chassis and SO-101 URDF in their original mount frame."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import mujoco
import numpy as np


def quaternion(rpy: str) -> str:
    """Convert URDF fixed-axis roll/pitch/yaw to MuJoCo wxyz quaternion."""
    r, p, y = (float(v) / 2 for v in rpy.split())
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return " ".join(
        map(
            str,
            (
                cr * cp * cy + sr * sp * sy,
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
            ),
        )
    )


def add_rovers(xml: ET.Element, assets: Path, machines: dict) -> None:
    """Add one shared mesh library and an independent joint tree for every instance."""
    model_file = assets / "astra/model.xml"
    if not model_file.is_file():
        raise FileNotFoundError(
            f"Missing {model_file}. Run python -m forma_core.control.rover_scene.assets {assets}"
        )
    base = ET.parse(model_file).getroot()
    world = base.find("worldbody")
    asset = xml.find("asset")
    for mesh in base.findall("./asset/mesh"):
        mesh.set("file", str((model_file.parent / mesh.attrib["file"]).resolve()))
        asset.append(mesh)
    chassis = world.find("body[@name='frontbase']")
    assert chassis is not None
    mount = ET.SubElement(chassis, "body", name="arm_mount", pos="0 0.10 0.39")
    ET.SubElement(
        mount,
        "geom",
        name="mount_plate",
        type="box",
        pos="0 0 -.015",
        size=".075 .07 .015",
        rgba=".8 .6 .2 1",
    )
    ET.SubElement(chassis, "geom", type="box", pos="0 .10 .345", size=".025 .025 .03", rgba=".8 .6 .2 1")
    urdf_dir = assets / "so101"
    urdf = ET.parse(urdf_dir / "so101.urdf").getroot()
    bodies: dict[str, ET.Element] = {"base": ET.SubElement(mount, "body", name="arm_base")}
    pending = list(urdf.findall("joint"))
    while pending:
        progressed = False
        for joint in pending[:]:
            parent = joint.find("parent").attrib["link"]
            if parent not in bodies:
                continue
            child = joint.find("child").attrib["link"]
            origin = joint.find("origin")
            body = ET.SubElement(
                bodies[parent],
                "body",
                name=f"arm_{child}",
                pos=origin.attrib.get("xyz", "0 0 0"),
                quat=quaternion(origin.attrib.get("rpy", "0 0 0")),
            )
            bodies[child] = body
            if joint.attrib["type"] != "fixed":
                limit = joint.find("limit")
                ET.SubElement(
                    body,
                    "joint",
                    name=f"arm_{joint.attrib['name']}",
                    type="hinge",
                    axis=joint.find("axis").attrib["xyz"],
                    range=f"{limit.attrib['lower']} {limit.attrib['upper']}",
                    limited="true",
                )
                ET.SubElement(body, "inertial", mass=".01", pos="0 0 0", diaginertia=".00001 .00001 .00001")
            pending.remove(joint)
            progressed = True
        if not progressed:
            raise ValueError("Disconnected URDF joint hierarchy")
    for link in urdf.findall("link"):
        visual = link.find("visual")
        if visual is None:
            continue
        name = link.attrib["name"]
        meshpath = urdf_dir / visual.find("geometry/mesh").attrib["filename"]
        if not meshpath.is_file() or meshpath.stat().st_size < 84:
            raise ValueError(f"Missing or empty mesh: {meshpath}")
        ET.SubElement(xml.find("asset"), "mesh", name=f"arm_mesh_{name}", file=str(meshpath))
        origin = visual.find("origin")
        ET.SubElement(
            bodies[name],
            "geom",
            type="mesh",
            mesh=f"arm_mesh_{name}",
            pos=origin.attrib["xyz"],
            quat=quaternion(origin.attrib.get("rpy", "0 0 0")),
            rgba=".95 .67 .12 1",
        )
    ET.SubElement(bodies["tool"], "site", name="arm_tcp", size=".012", rgba="1 .2 .1 1")
    bodies["tool"].find("site[@name='arm_tcp']").set("pos", "0 0 .055")
    for name, sign in (("left", -1), ("right", 1)):
        finger = ET.SubElement(
            bodies["tool"], "body", name=f"arm_finger_{name}", pos=f"{sign * 0.012} 0 .025"
        )
        ET.SubElement(
            finger,
            "joint",
            name=f"arm_finger_{name}",
            type="slide",
            axis=f"{sign} 0 0",
            range="0 .035",
            limited="true",
        )
        ET.SubElement(finger, "geom", type="box", size=".007 .014 .035", rgba=".15 .18 .2 1", mass=".005")
    crate = ET.SubElement(chassis, "body", name="collection_box", pos=".20 -.06 .34")
    ET.SubElement(crate, "geom", type="box", size=".12 .10 .012", rgba=".65 .40 .17 1")
    for x, y, sx, sy in (
        (-0.12, 0, 0.008, 0.10),
        (0.12, 0, 0.008, 0.10),
        (0, -0.10, 0.12, 0.008),
        (0, 0.10, 0.12, 0.008),
    ):
        ET.SubElement(
            crate, "geom", type="box", pos=f"{x} {y} .045", size=f"{sx} {sy} .045", rgba=".88 .65 .30 1"
        )
    ET.SubElement(crate, "site", name="box_drop", pos="0 0 .14", size=".005", rgba="0 0 0 0")
    for key, machine in machines.items():
        instance = deepcopy(chassis)
        for element in instance.iter():
            if "name" in element.attrib:
                element.set("name", key + "/" + element.attrib["name"])
            if element.tag == "geom" and element.attrib.get("rgba") == ".95 .67 .12 1":
                element.set("rgba", " ".join(map(str, machine.color)) + " 1")
            elif element.tag == "geom" and "rgba" not in element.attrib:
                element.set("rgba", ".27 .31 .34 1")
        xml.find("worldbody").append(instance)


def leg_pose(model: mujoco.MjModel, data: mujoco.MjData, t: float, prefix: str) -> None:
    """Update only this rover's leg joints; never reset a shared world."""
    for leg, offset in (("FR", 0), ("RL", 0), ("FL", math.pi), ("RR", math.pi)):
        phase = 2 * math.pi * t * 1.25 + offset
        forward = 0.035 * math.cos(phase)
        down = 0.24 - 0.035 * max(0, math.sin(phase))
        length = math.hypot(forward, down)
        a, b = 0.14, 0.126
        knee = math.pi - math.acos(np.clip((a * a + b * b - length * length) / (2 * a * b), -1, 1))
        hip = math.atan2(forward, down) - math.acos(
            np.clip((a * a + length * length - b * b) / (2 * a * length), -1, 1)
        )
        sign = -1 if leg.endswith("L") else 1
        for name, value in (("femur", hip), ("tibia", knee)):
            data.qpos[model.joint(f"{prefix}{leg}_j_{name}").qposadr[0]] = value * sign

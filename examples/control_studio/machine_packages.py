"""Example package authoring. Uses public core contracts, not private runtime APIs."""
from pathlib import Path
import hashlib

from forma_core.control.package import (
    MachinePackage, MachineAction, ActionParameter, CompletionRule,
    ObservationCondition, PackageArtifact,
)


def artifacts(root: Path, paths: dict[str, str]):
    return [PackageArtifact(path=name, role=role, sha256=hashlib.sha256((root/name).read_bytes()).hexdigest())
            for name, role in paths.items()]


def axis_actions(definition, *, tolerance=.1):
    parameters = {a.axis_id: ActionParameter(unit=a.unit, minimum=a.minimum, maximum=a.maximum) for a in definition.axes}
    source = "simulated" if definition.backend == "virtual" else "measured"
    return [MachineAction(action_id="move_axes", label="Move all axes", handler="move", parameters=parameters,
        parameter_axes={a: a for a in parameters}, axes=list(parameters), rates={a.axis_id: a.max_rate*.75 for a in definition.axes},
        completion=CompletionRule(source=source, tolerance=tolerance))]


def so101_package(adapter, root: Path):
    definition = adapter.definition()
    arm = [a.axis_id for a in definition.axes if a.axis_id != "gripper"]
    tool = dict(handler="tool_move", parameters={
        "x": ActionParameter(unit="mm", minimum=60, maximum=360),
        "y": ActionParameter(unit="mm", minimum=-230, maximum=230),
        "z": ActionParameter(unit="mm", minimum=12, maximum=350)}, tool_parameters=["x", "y", "z"],
        axes=arm, rates={a: 22.5 for a in arm})
    actions = axis_actions(definition, tolerance=1)
    actions += [MachineAction(action_id="move_tool", label="Move tool (top down)", **tool,
                    completion=CompletionRule(source="simulated", tolerance=1)),
                MachineAction(action_id="verify_placement", label="Retreat and verify placement", **tool,
                    completion=CompletionRule(source="simulated", tolerance=1, conditions=[
                        ObservationCondition(observation="placement_settled", operator="eq", value=True)])),
                MachineAction(action_id="open_gripper", label="Open gripper", handler="move", axes=["gripper"],
                    targets={"gripper": 85.0}, rates={"gripper": 33.75},
                    completion=CompletionRule(source="simulated", tolerance=1)),
                MachineAction(action_id="grasp", label="Close and verify contact", handler="move", axes=["gripper"],
                    targets={"gripper": 0.0}, rates={"gripper": 33.75},
                    completion=CompletionRule(source="simulated", positions=False, timeout_s=8, conditions=[
                        ObservationCondition(observation="bilateral_contact", operator="eq", value=True)]))]
    files = {"pick-place.xml": "mujoco-scene", "source-manifest.json": "upstream-provenance", "LICENSE": "license", "viewer-scene.json": "viewer-scene"}
    files.update({p.relative_to(root).as_posix(): "collision-and-visual-mesh" for p in (root/"assets").glob("*.stl")})
    return MachinePackage(definition=definition, model_source="external", adapter=adapter.describe(), actions=actions,
        operating_notes=[
            "Tool moves use top-down IK in millimeters. Use approximately Z70 for approach, lift and transport in this tabletop fixture; high top-down targets may exceed wrist limits.",
            "The 30 mm cube initially rests with its center at Z15. Align the tool with the observed cube center to grasp. Approach with the gripper open before descending.",
            "Grasp success requires bilateral jaw contact. Verify that cube_mm rises with the tool before transporting. Cube center may be offset from tcp_mm within the jaws; use observations to align placement.",
            "After lowering over the target, open the gripper and retreat using verify_placement. Require placement_settled and no jaw contacts; success/lifted are historical flags.",
            "Observations are MuJoCo simulated ground truth, not camera perception or physical hardware. Disarming pauses physics.",
        ], artifacts=artifacts(root, files))

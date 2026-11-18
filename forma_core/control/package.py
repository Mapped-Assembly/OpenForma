"""Portable machine contracts. No OpenCAD, simulator or web dependency.

OpenCAD payloads cross this boundary as JSON; original IDs and payloads are
retained. Package validation never imports or executes caller-supplied code.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, FiniteFloat, JsonValue, model_validator

from .models import ControlModel, MachineDefinition


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def opencad_index(assembly: dict, joints: list[dict]):
    """Validate the identity/kinematic subset without reinterpreting CAD geometry."""
    if assembly.get("version") != 1 or not isinstance(assembly.get("assembly"), dict):
        raise ValueError("Expected an OpenCAD version-1 assembly snapshot.")
    tree = assembly["assembly"]
    components = tree.get("components", {})
    if not isinstance(components, dict) or not components or not isinstance(tree.get("id"), str) or not tree["id"]:
        raise ValueError("Assembly needs an ID and components.")
    parents, shapes = {}, {}
    for key, component in components.items():
        if not isinstance(component, dict) or component.get("id") != key:
            raise ValueError("Component keys must preserve source component IDs.")
        for field in ("child_ids", "geometry_refs", "feature_refs"):
            refs = component.get(field, [])
            if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs) or len(refs) != len(set(refs)):
                raise ValueError("Component references must be unique nonempty strings.")
        for child in component.get("child_ids", []):
            if child not in components or child in parents or child == key:
                raise ValueError("Missing, duplicate or self-parented component.")
            parents[child] = key
        for shape in component.get("geometry_refs", []):
            if shape in shapes:
                raise ValueError("Geometry occurrence must have exactly one component owner.")
            shapes[shape] = key
    roots = tree.get("root_ids", [])
    if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
        raise ValueError("Assembly roots must be component IDs.")
    if len(roots) != len(set(roots)) or set(roots) != set(components)-set(parents):
        raise ValueError("Assembly roots do not match component hierarchy.")
    for key in components:
        seen = set()
        while key in parents:
            if key in seen:
                raise ValueError("Assembly component cycle.")
            seen.add(key)
            key = parents[key]
    by_id, bindings, joint_parents = {}, {}, {}
    for joint in joints:
        jid = joint.get("id")
        parent, child = joint.get("parent_shape_id"), joint.get("child_shape_id")
        kind = joint.get("type")
        if not isinstance(jid, str) or not jid or jid in by_id:
            raise ValueError("Joint IDs must be nonempty and unique.")
        if not isinstance(parent, str) or not isinstance(child, str) or parent not in shapes or child not in shapes or parent == child or child in joint_parents:
            raise ValueError("Joint occurrences must resolve uniquely to assembly geometry.")
        units = {"fixed": "none", "revolute": "radian", "prismatic": "mm"}
        if kind not in units or joint.get("unit") != units[kind]:
            raise ValueError("Invalid native OpenCAD joint type/unit.")
        lo, hi = joint.get("lower_limit"), joint.get("upper_limit")
        if not isinstance(joint.get("axis"), list) or not isinstance(joint.get("origin_mm"), list) or len(joint["axis"]) != 3 or len(joint["origin_mm"]) != 3:
            raise ValueError("Native joint axes and origins must have three components.")
        values = [lo, hi, *joint.get("axis", []), *joint.get("origin_mm", [])]
        if len(values) != 8 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
            raise ValueError("Joint limits, axis and origin must be finite.")
        if lo > hi or (kind == "fixed" and (lo != 0 or hi != 0)):
            raise ValueError("Invalid joint limits.")
        if kind != "fixed" and sum(v*v for v in joint["axis"]) <= 1e-24:
            raise ValueError("Movable joint axis must be nonzero.")
        joint_parents[child] = parent
        by_id[jid] = joint
        bindings[jid] = [shapes[parent], shapes[child]]
    for child in joint_parents:
        seen = set()
        while child in joint_parents:
            if child in seen:
                raise ValueError("Kinematic joint cycle.")
            seen.add(child)
            child = joint_parents[child]
    return by_id, bindings


class OpenCADIdentity(ControlModel):
    format: Literal["opencad-identity"] = "opencad-identity"
    assembly_snapshot: dict[str, JsonValue]
    kinematic_joints: list[dict[str, JsonValue]]
    joint_components: dict[str, list[str]]
    axis_joints: dict[str, str]

    @model_validator(mode="after")
    def references(self):
        joints, bindings = opencad_index(self.assembly_snapshot, self.kinematic_joints)
        if bindings != self.joint_components:
            raise ValueError("Joint/component bindings must match source geometry ownership exactly.")
        if len(set(self.axis_joints.values())) != len(self.axis_joints):
            raise ValueError("Each control axis must map to a distinct joint.")
        if any(j not in joints or joints[j]["type"] == "fixed" for j in self.axis_joints.values()):
            raise ValueError("Control axes must reference existing movable joints.")
        return self


def import_opencad_identity(assembly_snapshot: dict, kinematic_joints: list[dict], axis_joints: dict[str, str]) -> OpenCADIdentity:
    """Keep source IDs, hierarchy, transforms, metadata and joints verbatim."""
    _, bindings = opencad_index(assembly_snapshot, kinematic_joints)
    return OpenCADIdentity(assembly_snapshot=assembly_snapshot, kinematic_joints=kinematic_joints,
                           joint_components=bindings, axis_joints=axis_joints)


class ObservationSpec(ControlModel):
    path: list[str] = Field(min_length=1)
    value_type: Literal["number", "boolean", "string"]
    unit: str | None = None


class AdapterContract(ControlModel):
    adapter_id: str = Field(min_length=1)
    version: Literal[1] = 1
    mode: Literal["virtual", "physical"]
    evidence: Literal["ideal-kinematics", "contact-physics", "firmware-sil", "hardware-measurement"]
    configuration_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    # Named control axis -> concrete adapter actuator/channel identity.
    actuators: dict[str, str]
    handlers: list[Literal["move", "tool_move"]]
    observations: dict[str, ObservationSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def provenance(self):
        if (self.mode == "physical") != (self.evidence == "hardware-measurement"):
            raise ValueError("Hardware evidence requires a physical adapter.")
        if not self.actuators or len(set(self.actuators.values())) != len(self.actuators) or any(not v for v in self.actuators.values()):
            raise ValueError("Actuator channel identities must be nonempty and unique.")
        return self


class ActionParameter(ControlModel):
    unit: str
    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum >= self.maximum:
            raise ValueError("Action parameter range must be ordered.")
        return self


class ObservationCondition(ControlModel):
    observation: str
    operator: Literal["eq", "gte", "lte"]
    value: bool | FiniteFloat | str


class CompletionRule(ControlModel):
    source: Literal["simulated", "measured"]
    positions: bool = True
    tolerance: FiniteFloat = Field(default=.1, gt=0)
    conditions: list[ObservationCondition] = Field(default_factory=list)
    dwell_s: FiniteFloat = Field(default=.1, ge=0, le=30)
    timeout_s: FiniteFloat = Field(default=30, gt=0, le=300)

    @model_validator(mode="after")
    def meaningful(self):
        if not self.positions and not self.conditions:
            raise ValueError("Completion needs observed positions or conditions.")
        if self.dwell_s >= self.timeout_s:
            raise ValueError("Completion dwell must be shorter than timeout.")
        return self


class MachineAction(ControlModel):
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    label: str
    handler: Literal["move", "tool_move"]
    parameters: dict[str, ActionParameter] = Field(default_factory=dict)
    parameter_axes: dict[str, str] = Field(default_factory=dict)
    targets: dict[str, FiniteFloat] = Field(default_factory=dict)
    # For tool_move only: parameter names in X/Y/Z order, in mm.
    tool_parameters: list[str] = Field(default_factory=list)
    axes: list[str] = Field(min_length=1)
    rates: dict[str, FiniteFloat]
    completion: CompletionRule


class PackageArtifact(ControlModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: str

    @model_validator(mode="after")
    def relative_path(self):
        p = PurePosixPath(self.path)
        if not self.path or "\\" in self.path or ":" in self.path or p.is_absolute() or ".." in p.parts or str(p) == ".":
            raise ValueError("Artifact paths must be package-relative POSIX paths.")
        return self


class MachinePackage(ControlModel):
    format: Literal["forma.machine-package"] = "forma.machine-package"
    version: Literal[1] = 1
    definition: MachineDefinition
    model_source: Literal["opencad", "external"]
    mechanical: OpenCADIdentity | None = None
    adapter: AdapterContract
    actions: list[MachineAction] = Field(min_length=1)
    operating_notes: list[str] = Field(default_factory=list, max_length=30)
    artifacts: list[PackageArtifact] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self):
        axes = {a.axis_id: a for a in self.definition.axes}
        if self.adapter.mode != self.definition.backend or set(self.adapter.actuators) != set(axes):
            raise ValueError("Adapter backend/axis set must match the machine.")
        if (self.model_source == "opencad") != (self.mechanical is not None):
            raise ValueError("OpenCAD packages require native identity; external models must be labelled external.")
        if self.mechanical:
            if set(self.mechanical.axis_joints) != set(axes):
                raise ValueError("Every axis requires an OpenCAD joint identity.")
            joints = {j["id"]: j for j in self.mechanical.kinematic_joints}
            by_child = {j["child_shape_id"]: j for j in joints.values()}
            joint_axes = {j: a for a, j in self.mechanical.axis_joints.items()}
            for aid, jid in self.mechanical.axis_joints.items():
                axis, joint = axes[aid], joints[jid]
                angular = joint["type"] == "revolute"
                lo, hi = joint["lower_limit"], joint["upper_limit"]
                if angular:
                    lo, hi = math.degrees(lo), math.degrees(hi)
                if axis.unit != ("degree" if angular else "mm") or axis.minimum < lo-1e-8 or axis.maximum > hi+1e-8:
                    raise ValueError("Operational limits cannot exceed native joint limits or change units.")
                if axis.joint:
                    parent_shape = joint["parent_shape_id"]
                    parent_axis = None
                    while parent_shape in by_child:
                        ancestor = by_child[parent_shape]
                        if ancestor["type"] != "fixed":
                            if ancestor["id"] not in joint_axes:
                                raise ValueError("Viewer binding has an unbound movable ancestor.")
                            parent_axis = joint_axes[ancestor["id"]]
                            break
                        parent_shape = ancestor["parent_shape_id"]
                    if axis.joint.parent_axis != parent_axis:
                        raise ValueError("Viewer hierarchy must match native kinematic ancestry.")
                    norm = math.sqrt(sum(v*v for v in joint["axis"]))
                    if axis.joint.joint_id != jid or axis.joint.kind != joint["type"] or axis.joint.sign != 1:
                        raise ValueError("Viewer joint binding must preserve native joint identity and direction.")
                    if any(abs(a-b/norm) > 1e-8 for a, b in zip(axis.joint.axis, joint["axis"])) or any(abs(a-b) > 1e-8 for a, b in zip(axis.joint.origin_mm, joint["origin_mm"])):
                        raise ValueError("Viewer joint frames must match source frames.")
        ids = [a.action_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Action IDs must be unique.")
        for action in self.actions:
            controlled = set(action.axes)
            if len(controlled) != len(action.axes) or not controlled <= axes.keys() or set(action.rates) != controlled:
                raise ValueError("Actions require unique known axes and an explicit rate for each.")
            if action.handler not in self.adapter.handlers:
                raise ValueError("Action handler is not supported by the bound adapter.")
            for aid, rate in action.rates.items():
                if not 0 < rate <= axes[aid].max_rate:
                    raise ValueError("Action exceeds axis rate limit.")
            if action.handler == "move":
                if action.tool_parameters or set(action.parameter_axes) != set(action.parameters):
                    raise ValueError("Every move parameter must bind to an axis.")
                mapped = list(action.parameter_axes.values())
                if len(set(mapped)) != len(mapped) or set(mapped) & set(action.targets) or set(mapped) | set(action.targets) != controlled:
                    raise ValueError("Action targets must cover controlled axes exactly once.")
                for param, aid in action.parameter_axes.items():
                    p, axis = action.parameters[param], axes[aid]
                    if p.unit != axis.unit or p.minimum < axis.minimum or p.maximum > axis.maximum:
                        raise ValueError("Action parameter exceeds axis limits or changes units.")
                for aid, target in action.targets.items():
                    if not axes[aid].minimum <= target <= axes[aid].maximum:
                        raise ValueError("Action preset exceeds axis limit.")
            elif action.parameter_axes or action.targets or len(action.tool_parameters) != 3 or len(set(action.tool_parameters)) != 3 or set(action.tool_parameters) != set(action.parameters) or any(p.unit != "mm" for p in action.parameters.values()):
                raise ValueError("Tool moves require exactly three ordered mm parameters.")
            expected_source = "simulated" if self.adapter.mode == "virtual" else "measured"
            if action.completion.source != expected_source:
                raise ValueError("Completion source must match execution provenance.")
            for condition in action.completion.conditions:
                spec = self.adapter.observations.get(condition.observation)
                if spec is None:
                    raise ValueError("Completion references an undeclared observation.")
                value = condition.value
                matches = isinstance(value, bool) if spec.value_type == "boolean" else isinstance(value, str) if spec.value_type == "string" else isinstance(value, (int, float)) and not isinstance(value, bool)
                if not matches or (spec.value_type != "number" and condition.operator != "eq"):
                    raise ValueError("Observation condition has incompatible type/operator.")
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("Artifact paths must be unique.")
        digest(self.model_dump(mode="json"))  # Reject non-JSON/nonfinite metadata before execution.
        return self

    @property
    def sha256(self):
        return digest(self.model_dump(mode="json"))


def load_machine_package(path: str | Path) -> MachinePackage:
    path = Path(path)
    package = MachinePackage.model_validate_json(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    for artifact in package.artifacts:
        file = (root/artifact.path).resolve()
        if not file.is_relative_to(root) or hashlib.sha256(file.read_bytes()).hexdigest() != artifact.sha256:
            raise ValueError(f"Artifact hash/path mismatch: {artifact.path}")
        if package.mechanical:
            expected = {"opencad-assembly": package.mechanical.assembly_snapshot, "opencad-joints": package.mechanical.kinematic_joints}
            if artifact.role in expected and json.loads(file.read_text(encoding="utf-8")) != expected[artifact.role]:
                raise ValueError(f"Embedded identity differs from source artifact: {artifact.path}")
    return package

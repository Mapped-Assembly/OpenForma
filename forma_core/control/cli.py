"""Machine package schema and validation for host coding agents."""
import json
from pathlib import Path

from .package import MachinePackage, import_opencad_identity, load_machine_package


def _write(value, output):
    text = json.dumps(value, indent=2, allow_nan=False)+"\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def register_parser(subparsers):
    parser = subparsers.add_parser("machine", help="Inspect and validate executable machine packages (no execution).")
    commands = parser.add_subparsers(dest="machine_command", required=True)
    schema = commands.add_parser("schema", help="Print the machine package JSON Schema.")
    schema.add_argument("--output")
    schema.set_defaults(func=lambda args: _write(MachinePackage.model_json_schema(), args.output) or 0)
    validate = commands.add_parser("validate", help="Validate identities, actions, bindings and artifact hashes.")
    validate.add_argument("package")
    validate.add_argument("--output")
    validate.set_defaults(func=validate_package)
    mujoco_schema = commands.add_parser("mujoco-schema", help="Print declarative MuJoCo joint configuration schema.")
    mujoco_schema.add_argument("--output")
    mujoco_schema.set_defaults(func=mujoco_schema_command)
    mujoco_validate = commands.add_parser("validate-mujoco", help="Verify pinned assets and native joint/actuator bindings; no motion.")
    mujoco_validate.add_argument("configuration")
    mujoco_validate.add_argument("--output")
    mujoco_validate.set_defaults(func=validate_mujoco_command)
    identity = commands.add_parser("import-opencad", help="Preserve OpenCAD assembly/joint identities in a sidecar.")
    identity.add_argument("--assembly", required=True)
    identity.add_argument("--joints", required=True)
    identity.add_argument("--axis-joints", required=True, help="JSON file mapping control axis IDs to native joint IDs.")
    identity.add_argument("--output", required=True)
    identity.set_defaults(func=import_identity)


def validate_package(args):
    package = load_machine_package(args.package)
    _write(dict(valid=True, scope="structural-and-artifact-integrity", execution_verified=False,
        machine_id=package.definition.machine_id, package_sha256=package.sha256,
        adapter_id=package.adapter.adapter_id, actions=[a.action_id for a in package.actions],
        component_count=len(package.mechanical.assembly_snapshot["assembly"]["components"]) if package.mechanical else None,
        joint_count=len(package.mechanical.kinematic_joints) if package.mechanical else None), args.output)
    return 0


def import_identity(args):
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))
    identity = import_opencad_identity(read(args.assembly), read(args.joints), read(args.axis_joints))
    _write(identity.model_dump(mode="json"), args.output)
    return 0


def mujoco_schema_command(args) -> int:
    """Discover the simulation contract without importing the optional engine."""
    from .mujoco_joints import MujocoJointConfiguration
    _write(MujocoJointConfiguration.model_json_schema(), args.output)
    return 0


def validate_mujoco_command(args) -> int:
    """Compile pinned MJCF and verify scalar bindings without stepping physics."""
    from .mujoco_joints import MujocoJointAdapter, load_mujoco_configuration
    path = Path(args.configuration)
    adapter = MujocoJointAdapter(load_mujoco_configuration(path), path.parent)
    _write(dict(valid=True, scope="native-joint-binding-and-artifact-integrity",
                execution_verified=False, configuration_sha256=adapter.config.sha256,
                axes=len(adapter.config.bindings), actuators=adapter.model.nu,
                adapter=adapter.describe().model_dump(mode="json")), args.output)
    return 0

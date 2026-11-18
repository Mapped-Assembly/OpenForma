# Authoring an executable machine

Use this workflow for controllable mechanisms. Passive objects need geometry and
properties, not artificial actuator commands. Keep outputs in the project workspace.

## 1. Preserve mechanical identity

Author stable `AssemblyComponent.id` values and an `AssemblyTree` in OpenCAD.
Keep those IDs when geometry is regenerated; `geometry_refs` may change to the
new kernel occurrence IDs. Export using `serialize_assembly_tree(tree)` and
export the native `KinematicJoint` records without renaming their IDs. All joint
shape occurrences must resolve to components. Keep assembly ownership separate
from kinematic ancestry and temporary scene attachments.

Write `axis-joints.json` mapping Forma control axis IDs to native joint IDs, e.g.
`{"x": "x_slide", "y": "y_slide"}`. Generate the identity sidecar:

```bash
forma-core machine import-opencad --assembly PROJECT/opencad-assembly.json --joints PROJECT/joints.json --axis-joints PROJECT/axis-joints.json --output PROJECT/mechanical-identity.json
```

This preserves the original source payloads, including metadata and transforms,
and derives joint-to-component bindings from source geometry ownership. It does
not generate placeholder source IDs. Native revolute limits remain radians;
Forma angular commands use degrees. Operational limits must fit native limits.

## 2. Author the package

```bash
forma-core machine schema --output PROJECT/machine-package.schema.json
```

Use that schema to write `PROJECT/machine-package.json` with:

- `format: "forma.machine-package"`, `version: 1`.
- `definition`: named axes, units, neutral positions, operational limits/rates,
  viewer bindings where applicable, machine identity and backend.
- `model_source: "opencad"` and `mechanical`: the generated identity sidecar.
  Imported non-OpenCAD models use `model_source: "external"` and no fabricated
  OpenCAD identity. Their source files belong in the artifact list.
- `adapter`: the installed adapter's `describe()` contract, including actuator
  channel IDs, supported handlers, observation schema and configuration hash.
  Obtain this from the real adapter; do not invent available implementations.
- `actions`: explicit parameter ranges/units, controlled axes, bounded rates,
  handler and completion rule. `move` binds parameters or preset positions to
  axes. `tool_move` binds three mm parameters to an adapter's existing IK solver.
- `artifacts`: relative POSIX paths, SHA-256 digests and roles for native source,
  geometry, configuration and viewer assets. Roles `opencad-assembly` and
  `opencad-joints` are checked against the embedded identity. `viewer-scene`
  supplies Control Studio's mesh scene. Keep package artifacts immutable while
  a registered machine uses them; regenerate/revalidate/restart after edits.

For other action families, implement a typed handler and adapter contract first;
do not encode unsupported pump/torque/electrical commands as fake axis motion.
The initial executable handlers are axis moves and top-down tool moves.

## 3. Define completion from observations

Choose `simulated` or `measured`; `commanded` is not an action completion source.
Use position tolerance in the controlled axes' units, optional typed observation
conditions, continuous dwell and a bounded timeout. Conditions can only reference
observations declared by the adapter. A contact grasp should wait for bilateral
contact, not require a blocked gripper to reach its zero-degree command.

Keep task evaluation separate from the action acknowledgement. Physical placement
needs observed release/settling; an OpenCAD `PLACE` playback event is not proof.
An ideal adapter's successful arrival is only ideal-kinematics evidence.

## 4. Validate, bind and exercise

```bash
forma-core machine validate PROJECT/machine-package.json --output PROJECT/machine-validation.json
```

This validates structure, references, limits, completion rules and file hashes;
`execution_verified` remains false. Register through
`MachineRegistry.add_package(package, installed_adapter)` in the host, or use
Control Studio's `--machine-package PROJECT/machine-package.json` for its built-in
ideal/SO-101 loaders. The host selects executable implementations; a package
cannot supply a Python import or shell command. IDs must be unique in the host.

Discover `capabilities.machine_package`, claim, arm and use
`ControlClient.act_and_wait(action_id, parameters)` or a protocol-v2 `action`
command. Keep heartbeats alive during agent reasoning between calls. Save each
returned evidence record alongside the package: run ID, command sequence, owner
identity, package/configuration hashes, adapter, feedback source, observations,
status and completion time. Hold, takeover, link loss, failure or timeout are not
successful completion. The host retains only its latest 100 action records;
persist reports before they age out or the process restarts.

## 5. Deliver the complete project

Reference the package and evidence paths from the project's `assembly_metadata`
(e.g. `machine_package` and `machine_evidence`). Include source CAD, native
identity, adapter/configuration, viewer geometry and task fixtures. Report
separately: structural validation, tested actions, simulation fidelity, untested
actions/hardware and any failures. A package hash ties an evidence record to the
tested declaration; it is provenance, not a cryptographic signature or hardware
certification.

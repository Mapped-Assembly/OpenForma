# Declarative MuJoCo joints

`forma.mujoco-joints` version 1 binds a native MuJoCo model to the existing
MachineDefinition, MachineRuntime and MachinePackage contracts. Axis counts,
robot names, joint names and actuator names are declarations, not adapter code.
SO-101's specialized IK and grasp scoring remain in its own adapter.

## Contract

A configuration contains `definition`, `model`, hash-pinned `artifacts` and
`bindings`. `definition` is the existing machine schema (virtual backend).
Every defined axis maps to exactly one unique scalar joint and actuator. All
model actuators must be mapped explicitly. Unactuated free bodies remain free.

A binding example (inside `bindings`, indexed by a declared axis ID):

```json
{
  "wrist": {
    "joint": "left_wrist_yaw_joint",
    "actuator": "left_wrist_yaw_joint",
    "control": "pd_torque",
    "kp": 40.0,
    "kd": 2.0,
    "effort_limit": 5.0
  }
}
```

These are illustrative inspection gains, not a manufacturer's controller.
`position` instead uses the native position servo and its MJCF gains; it forbids
software PD fields. `pd_torque` requires a unit-gain motor and positive declared
kp/effort_limit. PD uses radians/metres and joint velocity in SI, producing Nm/N.
Axis API values remain degrees for hinges and millimetres for slides. Native
joint ranges and position-servo control ranges bound operational ranges. Native
force limits remain active; PD output is also clipped by declared effort limits
and native control limits. No control values are relabelled as measured feedback.

V1 supports direct unit-gear hinge/slide transmission, fixed actuator gain and
no activation dynamics. Ball/free joints cannot bind to scalar axes. Tendon,
non-unit gear and nonlinear actuator bindings fail explicitly. The native model
still owns link frames, hierarchy, inertia, collision, gravity and constraints.
No simplified CAD joint tree is substituted for physics.

## Integrity and loading

Each artifact has the existing `path`, `sha256` and `role` fields. `model` names
one pinned MJCF artifact. Inputs must remain under the package directory and
fit a cumulative 128 MiB budget. Every external mesh/texture/hfield file must
be pinned. Verified bytes are loaded into a virtual filesystem; model loading
does not fetch URLs or run downloaded Python. V1 rejects includes, plugins,
embedded models and unsupported file attributes. This deliberately bounded
format can be extended later without silently ignoring dependencies.

```bash
python -m forma_core machine mujoco-schema --output joints.schema.json
python -m forma_core machine validate-mujoco joints.json
```

Schema discovery requires no MuJoCo import. Native validation requires the
`robot-sim` extra and compiles the model, checks binding semantics and initializes
neutral positions. It does not run a task or verify motion feasibility.

Hosts instantiate `MujocoJointAdapter(configuration, package_root, clock=clock)`
and pass the adapter, its definition and the same clock to MachineRuntime.
Its `describe()` returns `forma.mujoco-joints`, configuration hash, concrete
actuator identities, `move` handler and `contact-physics` evidence provenance.
The existing MachinePackage's adapter contract must match exactly. A changed
model hash, axis limit or gain changes the contract fingerprint.

Control Studio accepts a machine package with exactly one artifact whose role
is `mujoco-joints`. That artifact's configuration is verified transitively; its
definition must match the package. The adapter is discovered by ID. No robot
name check is needed. Existing claim/arm/lease/rate/action-completion handling
stays in MachineRuntime. This does not automatically register a viewer asset.

## Execution semantics and scope

Neutral positions initialize a new virtual model once. Subsequent movement
uses controls and actual MuJoCo integration; qpos is not overwritten to achieve
targets. PD is reevaluated every physics timestep. Host time is capped by
`maximum_advance_s` (default 20 ms) so a delayed host cannot trigger unbounded
catch-up. Simulation may run slower than wall time.

Disarm pauses this virtual simulation and zeros actuator controls; it is not a
real-world power-removal model. Solver warnings or nonfinite state raise an
adapter fault handled by MachineRuntime. Body poses and joint feedback carry
simulated provenance. Completion waits for observed qpos and the existing
completion dwell, rather than assuming the command plan proves arrival.

This layer does not implement orientation-constrained IK, collision-free planning, free-base balance,
walking, grasp recognition, food transfer, washing physics or task policies.
A pelvis-supported G1 joint test is joint execution evidence, not a walking or
kitchen benchmark. The offline ProcessRuntime workcell runner remains explicitly
limited to its ideal adapter; this change does not relax that boundary.

## Regression checks

`python -m pytest tests/control/test_mujoco_joints.py -q` runs a mixed hinge/slide
fixture unrelated to G1/SO-101. It checks physics convergence through a declared
action, units, bounds, disarm, authority, malformed targets, artifact tampering,
missing dependencies, unique mappings, supported actuator semantics, complete
actuator coverage, package identity and host discovery.


# Executable machine packages

`forma.machine-package` v1 connects CAD identity, action discovery, trusted
execution adapters and observed completion. It is implemented in
`forma_core/control/package.py`, `actions.py` and `runtime.py`; it has no OpenCAD,
MuJoCo or web dependency. The base HardwareIR remains a design document.

## Identity

An OpenCAD package embeds its version-1 assembly snapshot and original native
joint records unchanged. Geometry ownership derives the parent/child component
IDs for each joint. `axis_joints` explicitly maps operational axes to source joint
IDs. Source component IDs, metadata and hierarchy survive kernel geometry UUID
regeneration. Invalid references/cycles/duplicate ownership are rejected, and
operational ranges cannot exceed the native joint limits. The original radians
are preserved while degree-axis limits are checked with explicit conversion.

The XY package now contains **25 OpenCAD components, 22 body occurrences and 15
native joints**, including two controlled prismatic joints. Its HardwareIR's
three logical BOM assembly rollups remain separate from those CAD identities.
SO-101 is labelled an **external MuJoCo model** and retains source/configuration
artifacts; it does not claim to have been generated in OpenCAD.

## Actions and bindings

Each declared action names parameters, units/ranges, controlled axes, rates,
handler and completion criteria. V1 handlers:

- `move`: explicit parameter-to-axis bindings plus optional preset targets.
- `tool_move`: three ordered mm parameters, solved by an installed adapter's IK.

`AdapterContract` advertises stable adapter ID/version, concrete actuator/channel
IDs, handler support and typed diagnostic observations. The runtime compares the
entire declared contract against `adapter.describe()` before registration/reset.
SO-101 hashes the loaded scene/mesh inputs; firmware SIL hashes its profile and
controller source provenance. Packages cannot dynamically import code or execute
commands. Additional adapter implementations are registered explicitly by the
host using `MachineRegistry.add_package()`.

Legacy v2 move/jog/manual controls remain available. A declared action is a
discoverable convenience with a completion contract, not an authorization boundary
that forbids the owner from using lower-level controls. Raw motion interrupts an
active declared action; the old run cannot subsequently report success.

## Completion and evidence

An `action` command passes the same session, lease, sequence, armed-state, axis
limit and rate checks as ordinary motion. An acknowledgement gives a `run_id`,
not a success claim. Runs report `running`, `completed`, `interrupted`, `failed`
or `timed_out`. A timeout holds motion. Hold, disarm, release, takeover and
link-loss prevent an active run from completing later.

Completion requires a successful adapter read after dispatch, command-plan
completion, and the declared position and/or diagnostic conditions for the dwell
interval. Missing/wrong-type observations fail the predicate. There is no fallback
from absent measured/simulated positions to command values. The feedback sequence
identifies host reads, not a hardware sensor acquisition timestamp; physical
adapters must supply trustworthy fresh measurements.

Evidence includes actual sampled positions/conditions, feedback source, read
sequence, dwell, run/command IDs, public owner ID, package/configuration hashes,
adapter ID/version and host uptime. Completed records are immutable under later
motion; each runtime retains its latest 100 runs. Evidence levels are explicit:
`ideal-kinematics`, `firmware-sil`, `contact-physics`, `hardware-measurement`.
Only physical adapters may declare hardware measurement. No hardware adapter is
provided by this addition.

## Agent and UI interface

```python
from forma_core.control import ControlClient

machine = ControlClient("http://127.0.0.1:8766", "so101")
contract = machine.capabilities()["machine_package"]
machine.claim("My policy")
try:
    machine.command("arm")
    evidence = machine.act_and_wait("move_tool", {"x": 220., "y": 25., "z": 70.})
    print(evidence["status"], evidence["provenance"])
finally:
    machine.release()
```

`act_and_wait` heartbeats while waiting. A persistent agent tool process must
also heartbeat while its model reasons between actions. `GET /actions/{run_id}`
returns a public evidence record without the control session secret. The latest
run appears in telemetry. Control Studio builds its **Declared machine actions**
forms directly from the package, showing the binding and feedback outcome.

### Persistent OpenCode task runner

Control Studio also offers **Give it a goal → Run task**. The reusable
`MachineTaskRunner` in `forma_core/control/tasks.py` owns machine control,
independent heartbeats (75% of the machine's declared interval; 150 ms by default),
timeouts, cancellation and JSON journals. The
optional `OpenCodeTaskAgent` launches the pinned Bun/SDK worker; the control loop
does not wait for model responses.

The worker exposes only `inspect_machine`, `observe`, `execute_action` and
`finish_task`. It discovers the selected package's actions and `operating_notes`,
then decides from observations. It cannot author transport scripts or modify
the simulation. Each action returns feedback-based evidence and a fresh
observation. A completed task must cite completed run IDs belonging to that
task; satisfaction of the free-form goal is **agent-assessed**, not a generic
formal proof. The journal preserves that distinction and the source provenance.

Studio API paths (relative to `/api/control/v2/machines/{machine_id}`):

- `POST /tasks`: `{goal, session_id?, reset?, model?}`. A current owner must
  supply its private session to authorize handoff; duplicate active tasks fail.
- `GET /tasks/latest`: compact status/timeline, also carried in state/WS frames.
- `GET /tasks/{task_id}`: full persisted evidence.
- `POST /tasks/stop`: cancel and release/disarm.
- `POST /claim` with `takeover:true`: interrupt the task and acquire manual
  control. Subsequent worker calls are rejected even if model output arrives late.

The host continues across browser disconnects. The default limits are 300 seconds,
50 attempted actions and 500 journal events. Host shutdown cancels tasks; on
restart, unfinished journals are marked interrupted rather than resumed. Terminal
journals remain available from the evidence link. `GET /api/control/v2/task-runner`
checks dependency installation, not provider authentication or quota.

Configure provider credentials for the embedded SDK host and optionally enter
`provider/model` in Task options. Model failures are visible failures, never
replaced by the reference controller. The standalone Studio README documents
installation and operation.

## Prepare and verify the examples

From the repository root, using its Python environment:

```bash
python .agents/skills/forma-hardware/scripts/cad.py setup
python examples/control_studio/build_assets.py --force
python examples/control_studio/package_benchmark.py
python examples/control_studio/setup_so101.py
forma-core machine validate examples/control_studio/generated/xy-stage/machine-package.json
forma-core machine validate examples/control_studio/generated/so101/machine-package.json
python examples/control_studio/action_benchmark.py
```

Rebuild the frontend and restart Studio. Release browser ownership, then run:

```bash
python examples/control_studio/action_benchmark.py --live
```

The scripted reference executes nine declared actions through contact-based
pick/place. The `grasp` action requires bilateral contact; `verify_placement`
requires actual current settled-placement feedback after retreat, not just the
task's latched historical success flag. Reports include all action records in
`generated/action[-live]-evidence.json`. This is a baseline policy, not an LLM.

Additional ideal/SO-101 packages can be loaded with
`server.py --machine-package path/to/machine-package.json`. A hashed
`viewer-scene` artifact is served from its package; legacy machines use the
existing static assets. Other adapters are passed explicitly to the registry.

The coding-agent authoring workflow is documented in
[machine-packages.md](../.agents/skills/forma-hardware/references/machine-packages.md).
It covers identity export, schema-based authoring, binding, tests, evidence
retention and project delivery. `forma-core machine validate` is an offline
structural/artifact check, and explicitly reports `execution_verified: false`.

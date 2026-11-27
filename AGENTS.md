# Agent workflow for OpenForma

## Author systems as declarations

When a user asks to make or operate a system, first use existing schemas, tools and
runtimes. Author the instance as JSON. Do not add case-specific Python, an evaluator,
a renderer, a repository example or a PR merely to fulfill a design request.
Only change infrastructure when the user asks for implementation or explicitly
approves a reported capability gap. Do not open a PR unless requested.

Before claiming a capability is absent, inspect the source and current registry.
If the user says to stop at issues, report the exact failing contract or unsupported
operation; do not silently substitute a custom implementation.

## Ownership: use the right runtime

- **OpenForma ProcessIR** owns process topology, material streams and manual steps.
- **OpenForma ProcessRuntime**, `forma_core/processes/execution/`, owns process-step
  state, dependencies, conditions, lot records, observations and completion events.
- **Execution adapters** acknowledge human work, evaluate a virtual scenario, or
  delegate a bound action to **OpenForma MachineRuntime**, `forma_core/control/`.
  MachineRuntime owns motion, leases, watchdogs, action completion and feedback.
- **OpenCAD** supplies geometry, assembly identities and geometric calculations.
  It is not the authority for chemical/process execution. A turntable is a geometry
  preview, not a GIF of a system executing end to end.
- A viewer consumes OpenForma state/events. Completing an animation does not
  complete an operation, prove material transfer or produce measured evidence.

A manually operated workcell does not implicitly have motors, a robot or a physical
execution adapter. Do not invent actuators to make an animation appear autonomous.

## Example path: chemical process with a workcell

Start with **`examples/process_runtime/vinegar-bicarbonate-declarative.json`** and
**`examples/process_runtime/README.md`**. The independent thermal example is
`examples/process_runtime/water-mixing.json`. Both use exactly the same runtime.
These examples contain explicitly synthetic inputs, not experimental measurements.

1. Discover native domain schemas and `experiment` catalog/schema through
   `forma.opencode.domains`, or the Python/CLI equivalents.
2. Author ChemicalIR/ProcessIR (or the applicable other domains). Add MechanicalIR
   and `uses_equipment` links only when a workcell is requested. Geometry remains
   optional for process execution.
3. Prepare the empirical plan with named, versioned evaluators. Freeze original
   predictions, assumptions, input requirements and acceptance criteria.
4. Declare a `forma.process-execution` sidecar over that exact ProcessIR with one
   binding per ProcessStep. This is an execution contract, not another engineering IR.
   Declare pre-action conditions, completion readings, lot/evidence obligations,
   evaluator references and optional pinned machine/action bindings.
5. Choose the adapter explicitly. Manual means actual attributed human records;
   virtual means illustrative scenario inputs and supported model receipts;
   machine means a host-supplied ControlClient bound to an exact machine package.
6. Start only dependency-ready steps. Submit records with provenance, units,
   uncertainty, instrument resolution and confirmed preconditions. Missing or
   unsupported facts remain unknown/unsupported and block completion.
7. Use runtime events/state to drive UI and any visualization. Check the active
   OpenForma adapter before investigating OpenCAD preview/export capabilities.
8. Export completed observations to the existing empirical comparator. Workflow
   completion and schema/geometry validity are not physical verification. Keep
   original plans and append a new revision when refining the design.

CLI example (agent executes it; do not require the user to install/run locally):

```bash
python -m forma_core domains process-runtime \
  examples/process_runtime/vinegar-bicarbonate-declarative.json \
  --output /tmp/chemical-process-run.json
```

MCP uses `forma.opencode.domains`, operation `process-runtime`, with the same JSON
payload. `{"action":"schema"}` discovers the execution contract. The portable
`run` operation replays manual/virtual transcripts only; it must never replay a
physical command history. Inject machine adapters in an authorized live host.

Read **`docs/process-runtime.md`** for adapter boundaries and remaining limits.
For visible workcell execution, use `examples/process_runtime/workcell/request.json`:
`process-runtime` action `simulate` runs declared phases through MachineRuntime and
ProcessRuntime, then exports feedback frames and conservative virtual inventory.
Run `python -m forma_core.processes.execution.render REQUEST TRACE SCENE OUTPUT.gif`
to render that evidence. The viewer checks geometry/plan/trace hashes and uses
sampled feedback positions. It never completes steps or generates measurements.
Read `docs/virtual-workcells.md` before authoring new workcells. The example's
motions represent virtual human manipulation; no physical actuator is designed.
Fluid dynamics, collision checking, mixing homogeneity and reaction kinetics are
not supplied by this bridge. Do not confuse its inventory accounting with chemistry.

## Declarative robot joint execution

For imported robot joints, read `docs/declarative-mujoco-joints.md`. Use
`forma.mujoco-joints` JSON for named axes, native joint/actuator mappings, gains
and pinned assets instead of adapting SO-101-specific code. Discover the schema
with `machine mujoco-schema` and verify bindings with `machine validate-mujoco`.
MachineRuntime owns execution and observed completion. This adapter supplies
scalar-joint contact physics, not IK, balance, locomotion or grasp verification.

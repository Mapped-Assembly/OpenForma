# Declarative process execution

`ProcessRuntime` executes the intended steps in existing ProcessIR. Its sidecar
`ProcessExecutionPlan` pins that process, explicit step bindings and an optional
frozen `ExperimentPlan`. No additional scientific IR, robot or CAD document is
required. The runtime rejects a process that differs from the empirical plan.

## What it tracks

Each native step progresses from pending to running, then completed, failed or
cancelled. Missing evidence leaves it running with an explicit unknown or
unsupported attempt result. Dependencies are checked before start; only one step
is active per run, with one selected adapter per runtime instance. Mixed-adapter
scheduling is not implemented in this initial version. Operations roll up all their steps, so repeated measurement
steps on one operation are not collapsed into one premature completion.

Bindings declare conditions checked **before adapter dispatch**, completion
readings, required material streams/lot observations, required scientific criteria,
model evaluations and optional machine actions. Native textual preconditions need
explicit confirmations. The runtime does not pretend to parse prose into physics.
Readings preserve instrument, calibration, resolution and uncertainty. Compatible
units are converted with the existing dimension-aware converter; temperature
uncertainty never receives a Celsius/Kelvin offset. Missing uncertainty blocks
requirements that demand it. Entire uncertainty intervals must fit the bounds.

MaterialBatch records retain stable lot IDs, stream IDs, quantities, prior-lot
ancestry and evidence. They are append-only observations: they do **not** implement
inventory reservation, consumption accounting, yield inference or conservation.
Actual material balance remains unknown until a separate supported evaluation.

Each attempt is retained, including blocked attempts. Completed step observations
can be exported as an existing ExperimentRun and compared against the original
prediction. Runtime completion itself always reports physical verification as
`not_evaluated`. Synthetic trials never contribute physical verification coverage.

## Adapters

| Adapter | Authority | Completion evidence |
| --- | --- | --- |
| ManualAdapter | Explicit human acknowledgement | Attributed measured records and all declared gates |
| VirtualProcessAdapter | Existing versioned domain evaluator registry | Explicit illustrative scenario records; scientific predictions remain separate receipts |
| MachineProcessAdapter | Existing ControlClient / MachineRuntime | Pinned action receipt with matching parameters, package, adapter and feedback provenance |

The virtual adapter does not invent missing measurements. A step without a model
is a logical scenario acknowledgement; it is not a simulation of its physics.
Model unknown/unsupported/failure statuses block virtual step completion.

For machine execution the host supplies an already authorized client and owns
claiming, arming, heartbeat traffic and watchdog servicing. The process adapter
never discovers device URLs or automatically claims/arms machinery. It dispatches
an action once, stores the returned run ID and polls action evidence on subsequent
submissions. An ambiguous dispatch failure is not automatically retried. Reconcile
with the machine before making any new execution attempt. Do not reuse an adapter
instance between runs. Cancellation requests Hold before recording cancellation;
if Hold fails, the call fails rather than claiming the machine stopped.

Motion completion is not proof that fluid transferred or a chemical reaction
completed. Declare additional observations/conditions for those claims. Machine
bindings are exact package hashes and declared action IDs; changed packages must
be reviewed and repinned. This layer does not bypass MachineRuntime limits or
feedback checks, or add autonomous planning.

## Public interfaces and examples

```python
from forma_core.processes.execution import ProcessExecutionPlan, ProcessRuntime, ManualAdapter

plan = ProcessExecutionPlan.model_validate_json(plan_json)
runtime = ProcessRuntime(plan, "trial-001", adapter=ManualAdapter())
ready = runtime.ready_steps()
runtime.start(ready[0])  # No physical command is sent by start.
# runtime.submit(step_id, StepSubmission(...)) uses actual operator evidence.
state = runtime.snapshot()
```

Use `VirtualProcessAdapter(registry)` for a synthetic scenario or
`MachineProcessAdapter(control_client, machine_id)` in an authorized live host.
Both are supplied to the same ProcessRuntime constructor.

`python -m forma_core domains process-runtime request.json --output result.json`
and MCP `forma.opencode.domains` with operation `process-runtime` share the same
schema/dispatch. `{"action":"schema"}` returns the request schema. A `run`
request contains `plan`, `run_id`, `mode` (`manual` or `virtual`) and explicit
`start`, `submit`, `cancel` commands. It is **stateless transcript replay**, not a
new hosted job service or durable resume mechanism. Never replay a physical action
history; machine execution is deliberately unavailable through that operation.

See [the two executable JSON examples](../examples/process_runtime/README.md).
Their reports demonstrate software behavior only. The CLI exits nonzero for a
failed or cancelled process; unknown/waiting steps remain inspectable in JSON.

## Events and limits

Snapshots expose steps, operation rollups, ready steps, recorded batches and
hash-linked events with run/plan identity, timestamps, sequence and provenance.
Viewers must consume this state, not drive process state from animation callbacks.
Hashes detect accidental edits; they are not digital signatures or operator
identity verification. Hosts must authenticate operators, serialize runtime calls,
secure uploads and persist records. Concurrent/distributed scheduling, crash-safe
machine dispatch recovery, automatic retry, process cancellation compensation,
physical inventory balances and reaction kinetics are not implemented.
The optional [virtual workcell bridge](virtual-workcells.md) adds conservative
virtual inventory and feedback-driven GIF export; it does not provide those physics.

No physical trials were performed. Physical provenance checks enforce declared
record types; software cannot prove the truth of an operator's submitted readings.

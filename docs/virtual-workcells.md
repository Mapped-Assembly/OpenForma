# Declarative virtual workcell execution

Issue [#65](https://github.com/Mapped-Assembly/OpenForma/issues/65) exposed a missing
connection: process steps could complete while the workcell remained stationary.
The `process-runtime` tool now accepts `action: simulate`, composing existing
ProcessRuntime, MachineRuntime, MachinePackage and scientific evaluators. No
benchmark names or chemistry-specific motion are implemented in the execution engine.

## Authoring and ownership

A request contains the same frozen `plan`, an explicit illustrative `submissions`
map indexed by process step, and a `workcell` execution sidecar:

- `machine`: an existing MachinePackage using the installed `forma.ideal` adapter.
  Declare axes, parent joints, geometry body IDs, actions, limits, rates and
  feedback completion rules using the existing machine contracts.
- `geometry_sha256`: canonical fingerprint of the CAD mesh scene. The scene uses
  the existing viewer mesh vocabulary: mm, body ID, positions, indices and RGB.
- `containers`: stable inventory/body IDs, initial constituent masses in grams,
  declared mass capacities and nominal visualization dimensions/colors.
- `phases`: per-step action IDs and parameters, optional transfer
  `{source, target, stream_id, mass_g}`, or an `agitate` container ID.
- `frame_interval_s` and `maximum_duration_s`: bounded virtual host sampling and
  duration. The machine ticks at its own declared update interval.

The native ProcessIR still owns operation topology and step dependencies. Every
phase must resolve to a step; transfer streams must be material streams incident
to that step's operation. Each step must have phases and a scenario submission.
A phase sequence and the old single-action machine binding are mutually exclusive.

The host creates an in-memory **virtual** machine, explicitly claims and arms it,
and maintains leases/heartbeats. It cannot connect to hardware or accept custom
adapter code from JSON. WorkcellAdapter dispatches through MachineProcessAdapter;
a phase advances only when the action's matching package/action/parameter receipt
contains simulated feedback evidence. Each effect commits once. Interrupted or
failed actions cannot move material or unlock downstream steps. Unknown science,
missing conditions and incomplete observations stop progression without inventing
values. The host holds/disarms on exit.

## Conservative virtual inventory

Transfers use rational arithmetic, reject source overdraw and target overflow,
and preserve every declared constituent across the closed inventory boundary.
Partial transfers assume the source's constituent mass fractions apply to the
transferred sample. This is an explicit ideal aliquot assumption, **not** a model
of stratification, emulsions, settling, dissolution or mixing homogeneity.

Inventory effects are discrete transactions after motion completion, not inferred
from how far a vessel has tilted. Transfer paths/rates/efficiency are prescribed,
not computed from fluid mechanics. Container capacity is a nominal mass constraint;
the displayed fill fraction is mass/capacity, not a density-derived liquid level.
`agitate` records that its bound motion completed; homogeneity remains unknown.
No chemical conversion, gas evolution, evaporation or heat flow is inferred.
These virtual states never become measured readings or empirical observations.

## Run and render

```bash
python -m forma_core domains process-runtime examples/process_runtime/workcell/request.json --output /tmp/workcell-trace.json
python -m forma_core.processes.execution.render examples/process_runtime/workcell/request.json /tmp/workcell-trace.json examples/process_runtime/workcell/scene.json /tmp/workcell.gif
```

The first call also works through the existing REST/MCP domain tool dispatch using
operation `process-runtime`. The renderer is a local Python export utility
(`pip install '.[process-render]'`); no new deployed service is implied.

The trace contains every phase's completed machine receipt, sampled simulated
positions, process event hashes, material state, the unchanged frozen-plan hash,
package/geometry/workcell hashes and a digest of the whole trace. The renderer
checks those pins, resolves the existing joint hierarchy, and depth-tests native
CAD triangles. It never sends commands, interpolates unobserved motion, completes
operations or fabricates sensor data. The dotted transfer link is a schematic
indicator for an active declared transfer, not a rendered fluid simulation.
The original empirical comparator still consumes only the separately provided
illustrative observations; a passing synthetic comparison is not derived from
animated contents and provides zero physical verification coverage.

The chemical example has 27 motion phases: lift, move, tilt and return two dosing
vessels; transfer 50 g and 2 g; manipulate the utensil; and record the original
synthetic comparison. Its virtual inventory ends at 52 g in the bowl. The chemical
evaluator independently predicts a stoichiometric bound; the inventory does not
pretend that an acid-bicarbonate reaction has been simulated.

## Boundaries and evidence

The example models **virtual human manipulation** of a manually operated workcell.
It does not add a robot, actuators, contact/collision verification or a human body
model. Motion limits are scenario bounds, not validated human reach or equipment
ratings. Ideal pose feedback proves the software action contract only.

Other remaining limits: no durable host resume, no distributed scheduling,
no mixed hardware/human dispatch in this offline tool, no physical measurement
collection, no energy accounting, and no scientific generalization claim beyond
implemented evaluators. Laboratory evidence is still absent and industrial
applicability is unknown. Artifact hashes are integrity checks, not signatures.

Regression tests cover real machine feedback gating, action interruption,
conservative/idempotent transfers, atomic overflow/overdraw rejection, a second
material mixture, unresolved bindings, absent preconditions, hardware rejection,
feedback-only pose transforms, hash tampering, and the full example.

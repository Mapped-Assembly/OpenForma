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

## Strict entity interactions

The optional `workcell.interactions` section declares geometry identities, allowed
entity actions, initial rigid poses, bounded supporting patches and scalar
mechanical transmissions. This extends the existing execution sidecar; it is not
a new domain IR. Each phase may declare `before` and `after` interaction actions.

`grasp` requires coincident actor/object grip anchors and an unoccupied hand.
Carried transforms derive from the hand and a captured rigid offset. Nonactor
entities cannot have independently commanded pose axes in this mode. `release`
requires the owning hand and a valid receiving support patch. All declared contact
anchors must be on that finite patch; the patch must face upwards. Release is
atomic, so a rejected placement leaves the grasp intact. Fixed equipment retains
its declared pose. Supported loads follow the carrier, with support orientation
checked on each sampled feedback update. `use` requires contact with the declared
control/grip anchor. `actuate` grants a phase-scoped human input for a declared
scalar transmission; absent contact, out-of-range travel or an undeclared input
blocks completion. The transmission's pitch/ratio is a scenario assumption.

Initial retention can declare adhesion or a fixture holding an object to a carrier
(`initial_retention: true`, with a valid `initial_support`). This differs from
ordinary support and must be explicit: it allows an attached item to remain with
an inverted carrier. Its strength is **unverified**. This is useful for a wet-web
visual proxy retained on a mould, not evidence that the real web will adhere.

Interaction state updates from machine feedback before process completion. A
failed interaction prevents inventory effects and downstream completion. After
commands commit only on a matching completed machine action receipt. The trace
includes authoritative entity poses, ownership/support relationships, and action
events. Viewers must use those recorded entity poses for strict workcells.

This verifies **sampled kinematic motion provenance and declared anchors only**.
It does not verify forces, friction, stability, full mesh collision, continuous-time
collision, human balance, material formation, or actuator energy. Human body axes
remain authored kinematics. Virtual human input axes are not physical motors.
Legacy workcells without this section remain unverified choreography, explicitly
separate from strict interaction evidence. The geometry consumer must resolve all
rendered body identities against the declared entity inventory; no silent visual
fallback is allowed for a strict scene.

For long sequences, `ProcessRuntime.snapshot(include_events=False)` returns the
same current state with an empty history array plus `event_count` and
`last_event_sha256`. The default snapshot still includes the complete append-only
history. The workcell host uses compact polling and exports full history at the
end; this avoids repeatedly copying all prior observations for every pose sample.

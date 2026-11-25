# Electronics engineering contracts (initial slice)

`ElectricalIR` now carries optional engineering facts alongside its existing
parts, component views, pins, nets, buses and rails. This is a framework change;
no pallet robot design, payload qualification or hardware control is included.

## Inspection and ownership

Before this change, ElectricalIR already validated part and pin references;
`validate_circuit` checked shorts, nominal voltages, pin reuse and missing power.
It did not carry typed envelopes, harnesses, simultaneous load budgets or
revision-bound evidence. `ComponentView.object_id` already linked domain views;
legacy `PartDefinition`/`BOMLineItem` already separated definitions and instances.
The asset library already had content-addressed CAD versions. `MachineDefinition`
already owned axis limits/units and watchdog timing, and runtime commands and
feedback already distinguished simulation from physical measurements.

The new contracts extend these boundaries rather than replacing them:

| Fact | Authority / reference |
| --- | --- |
| Physical instance | Existing `ComponentView.object_id`, shared with MechanicalIR |
| MPN and pins | Existing `ElectricalPart.part_number`, `pins` |
| Manufacturer, part revision, datasheet, mass | `ElectricalPart.engineering`; unknown is `null` |
| CAD geometry | Existing asset `InspectRequest` pins asset ID and version; no embedded geometry |
| Pin voltage/current envelope | `PinDefinition.limits`; existing direction/interface/power_role remain authoritative |
| Connector grouping | Part-local connector IDs referencing existing pin IDs |
| Logical connectivity | Existing nets; refdes is resolved through ComponentView to stable object IDs |
| Physical conductor | Harness wire references net and object/pin endpoints |
| Power operating scenario | Power domains and load envelopes; not a copy of motor constants |
| Runtime commands, units, limits, watchdogs | Existing `MachineDefinition`, pinned by digest and axis ID |
| Startup, fault response, feedback intent | Runtime binding; declarative only |
| Verification claims | Evidence records pinned to design digest and component revisions |

`engineering_bom()` produces definition references and object-ID groups; quantity
is derived, not stored. Consumers resolve MPN, mass, ratings and CAD references
from the part. This is a reference inventory view, not a second priced BOM.
Existing legacy procurement exports still own pricing and sourcing offers.
Legacy HardwareIR export reports engineering fields as unrepresentable and
requires explicit lossy export instead of silently dropping them.

Mechanical assembly parentage stays in the mechanical model. Electrical nets can
connect across any branches of that hierarchy. Use the existing ProductManifest
and DomainLink endpoints to pin mechanical and electrical artifact revisions and
share object IDs; enclosure geometry never creates an electrical connection.
Internal placement and physical routing remain CAD tasks. `route_ref` is only a
reference, not evidence of route clearance or length measurement.

## Validation and unknowns

The new fields are additive to schema 1.0; old ElectricalIR input remains valid.
Older readers may reject the new optional fields. `null` is unknown; empty lists
mean no entries were supplied, not proof that a system needs none. The legacy
free-form `electrical_specs` remains for compatibility; new calculations consume
only the typed fields and never guess constants from text.

Construction rejects duplicate IDs, absent connectors/pins, wrong-net harness
endpoints, dangling protection/mating component references, power cycles,
unconnected sources/loads, contradictory net/domain voltages and double-counted
converter loads. Public power calculations reparse mutable models before use.
`validate_domain` and `validate_product` additionally report voltage-envelope and
protocol incompatibilities, terminals assigned to multiple nets, incomplete
harness data and power overload/unknown findings. Missing pin-envelope warnings
are enabled when a document opts into any engineering contract; untouched legacy
documents keep their existing diagnostic behavior. A single source terminal's
current rating is checked against peak input demand; parallel terminals report
unknown current sharing. Branch-wire currents are not inferred from net membership.

A `PowerDomain.current_limit_a` is a declared usable source capacity for the
scenario, not a substitute for terminal ratings. Voltage must agree with the
net nominal voltage when both are supplied. Source data/protection sizing must
still be justified externally. Connector mating compatibility and fault-current
coordination are not solved by mere reference integrity.

Each load supplies on/idle watts and duty fraction. Peak assumes all loads may
operate simultaneously and sums `max(on, idle)`; average sums
`duty*on + (1-duty)*idle`. Missing inputs propagate as unknown independently.
An unloaded domain has zero declared demand, not verified completeness. A
converter names its upstream domain; upstream demand includes output/efficiency
plus input-side quiescent watts. Do not also add that converter as an upstream
load. No diversity, startup transient, battery capacity or discharge model is
implied. Per-domain results describe the loads explicitly declared in the input.

## MuJoCo to electrical calculation boundary

The implemented `MotorHistory` accepts **normalized joint-output samples** with
interval duration (seconds), torque (N m) and angular velocity (rad/s). It pins
the electrical design digest and simulator artifact digest and identifies the
joint, motor and controller objects. This release does not extract live MuJoCo
telemetry. A future adapter must:

1. Pin the MJCF/assets, solver settings, payload, trajectory, initial conditions,
   MuJoCo version and sampling data in the simulation artifact.
2. Map a joint to the component object and define its coordinate/sign convention.
   Resolve the actuator transmission into joint generalized force; raw `ctrl`
   and raw `actuator_force` are not interchangeable with joint-output torque.
3. Export rotational joint velocity and joint-output actuator torque including
   the inertia, gravity, friction and contact loads actually modelled. Do not
   add payload torque again to torque that already includes that load.
4. Separate actuators when several contribute to a joint. Reject ambiguous
   transmission mappings; translational axes need an explicit lead/radius model.
   Document any motor/gear losses already in the simulation to avoid double count.
5. Sample sufficiently to capture peaks and preserve interval timing. This API
   assumes each provided sample is constant during `duration_s`; sparse output
   is not guaranteed to capture switching, impact or inrush peaks.

For the implemented **effective brushed-DC motoring model**:

- `tau_motor = abs(tau_joint)/(gear_ratio * gear_efficiency)`
- `omega_motor = abs(omega_joint) * gear_ratio`
- `I_motor = tau_motor/Kt + I0`
- `V_required = Ke * omega_motor + I_motor * R`
- `P_bus = V_required * I_motor / controller_efficiency`
- `I_bus = P_bus/V_bus`; winding copper loss is `I_motor² * R`.

Gear ratio is motor speed/output speed. Kt is N m/A, Ke is V/(rad/s), R is ohms.
Actual motor/controller constants require pinned source documents and known part
revisions. The source URI/hash is declared provenance; this code does not fetch
or authenticate datasheets. The no-load-current approximation is conservative
at stall and is not a full loss map. Mechanical power alone is insufficient:
stall torque at zero speed still requires current and produces winding heat.

The calculator checks controller bus-voltage, motor-current and bus-current
limits plus required motor voltage. Exceeding a limit reports infeasible demand;
it does not clip the result or imply that the trajectory can actually occur.
Missing inputs or negative torque-times-speed (braking/regeneration) yield unknown.
BLDC phase conventions, PWM switching, inductance, regeneration, thermal networks,
temperature-dependent resistance and controller quiescent losses are future work.
Use thermal IR for a separate thermal model; copper watts alone are not temperature.

Motor-history estimates are separate from authored duty-cycle budgets. They are
not silently substituted into those budgets: an integration must pin the result,
state the operating scenario and account for controller auxiliary loads, then
update the demand envelope and design revision explicitly.

## Runtime and evidence

`validate_runtime_bindings(ir, machine)` checks machine ID, content digest, axis
and component mapping. It uses the existing refdes-based `axis.component_ref`
through the stable component view. It flags unknown startup/fault/feedback intent
and rejects simulated/measured feedback conflicts. Commands (`move`, `jog`,
`hold`, arm/disarm), degree/mm units, rate/range limits and heartbeat/lease/link-loss
timing remain in the control package. Bindings do not install watchdogs, implement
fault responses, or generate firmware. Physical driver enforcement and wiring-to-
firmware compilation are future integrations.

`design_digest()` excludes evidence to avoid self-reference but includes all
other electrical facts and linked CAD/runtime hashes. Evidence separately names
geometry, dynamics, electrical, thermal or safety; it carries a report/source hash,
method, scope, limitations and exact component revision map. `evidence_status()`
is a conservative aggregation of **supplied scoped claims**, not an independent
review of their truth. A pass requires the full component revision map to match;
partial, absent, unknown or stale claims return unknown. A current fail takes
precedence. Scope text and limitations must accompany any displayed status;
a dynamics pass never changes the electrical or structural-strength status.
External mechanical/simulation artifacts can be pinned in `model_sha256`.
For these records the caller must supply current model digests to
`evidence_status(..., model_sha256=...)`; absent or mismatched model digests make
the claim unknown. Use content-addressed model manifests including revisions,
assets and solver settings. This API does not resolve or authenticate external
models or reports; caller-supplied identities must come from the actual run.

## Example and checks

```bash
python -m examples.electronics.generic_axis
python -m pytest tests/projects/test_electronics_engineering.py -q
```

The example is a synthetic 5 V supply/controller/motor axis, not a real component
selection or a buildable circuit. It deliberately leaves routing, protection,
mass and vendor metadata unknown. Its fixture constants yield 2.0625 W peak,
0.525 W time-average, 1.1 A peak motor current and 0.4125 A peak bus current.
All five verification statuses remain unknown. No MuJoCo run or electrical
measurement is claimed by this example.

Future work includes actual MuJoCo extraction, parameter/source verification,
pin direction/driver conflict rules beyond existing circuit checks, mating-key
compatibility, complete harness return/protection routing, mechanical placement
resolution, branch-current/voltage-drop solvers, thermal coupling, physical
watchdog/fault enforcement and firmware configuration generation. The framework
cannot qualify a 500 kg robot's strength, braking, electrical safety or payload
capacity from a motion simulation or CAD envelope.

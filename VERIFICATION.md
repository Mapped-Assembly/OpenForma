# Smart textile V3 — first layered-material implementation

V3 adds reusable layer declarations and typed cross-domain bindings to the existing
OpenForma IRs. No TextileIR, new runtime or benchmark-specific repository example
was introduced. The product remains a candidate smart wearable; verified cloth or
closed-loop thermal behavior is not claimed.

## Changes from V2

- MechanicalIR: ordered layers, adjacent joins, material axes, optional directional
  stiffness/strain/bend limits and evidence references. Layers resolve existing
  component occurrences rather than becoming a duplicate component system.
- ElectricalIR: typed temperature sensor roles and optional conductor response
  coefficients. No fabricated sensor readings or resistance changes.
- Product composition: typed mounting, thermal representation, observation,
  wearer-contact and thermal-contact relationships. Existing physical identity,
  heat-source and revision/digest checks remain in use.
- The controller module contains three nominal geometric sublayers: 0.100 mm
  substrate, 0.035 mm copper envelope, 0.165 mm coverlay. OpenCAD creates and
  round-trips each solid. These are candidate dimensions, not selected PCB specs.
- The textile band is explicitly a contact layer under an aggregate garment host.
  Its detailed construction and thickness remain unknown. The aggregate host has
  no extra solid or mass. The original curved band is still rigid.

## Passed

- 90 domain tests, including nine new generic layered-material tests; one optional
  domain test skipped. 60 control tests passed; six optional control tests skipped.
- Native product composition valid across four IRs; 44 warnings remain visible.
- Three new valid STEP layer solids, each reimported, with placement error below
  3e-17 mm. The existing 40-solid device STEP is independently reimported and valid.
  The inherited combined human/device STEP retains 56 solids. The three new layer
  STEP files use controller-local coordinates, not global assembly coordinates.
- OpenForma MachineRuntime executes the articulated model through its MuJoCo
  adapter for 14 seconds, recording 351 states. The pelvis is fixed; virtual joint
  servos move the arms and torso. This is not muscle or balance simulation.
- Contact, spring travel and debounce trigger ON at 3.430 s and OFF at 8.926 s.
  This toggles a virtual cooling request, not firmware or a thermal response.
- All 42 declared fixed attachments, including the three sublayers, remain fixed;
  maximum relative-transform element error is 7.77e-16. Only the declared palm /
  button collision pair is enabled; this is not full-body collision verification.
- GIF rendered from those recorded states: no interpolation or simulation steps
  during rendering. Full-body and close-up stills come from the same executed model.
- OpenIndustries validates and hydrates 59 instances and 6,962 sampled pose keys.
  Its review playback can interpolate; the GIF does not use that interpolation.

## Failed

No final structural or runtime check failed. This is not a finding that all product
requirements are feasible. The following unsupported and unknown items block that
conclusion. Earlier geometry placement and test-fixture errors were corrected and
rechecked; their failed intermediate outputs are not delivered as accepted evidence.

## Unsupported

- Cloth deformation, drape, contact pressure, fatigue, conductor strain response.
- Coupled temperature control, transient PCM phase change and TEC performance.
  Both native thermal analysis cases return `unsupported`, with null temperatures.
- Automatic CAD-versus-layer-thickness validation inside the IR inspector. The
  separate CAD receipt checks solidity/placement, not material performance.
- Actual MCU firmware execution, BLE, Qi charging or battery discharge simulation.

## Unknown / requirements preserved

The original requirement file is unchanged: nRF52840, BLE and local control, Qi,
thin-film battery, 3.3 V, a **shared 1 A peak TEC budget**, skin/ambient sensors,
flex PCB, textile, Peltier and PCM. The nominal TEC electrical budget is therefore
3.3 W total; this is not a cooling-capacity prediction. Driver losses, logic demand,
battery peak-current capability, converter compatibility and simultaneous loads
remain unknown; all four native power budgets return null peak/average demand.

Material coefficients, sensor calibration, thermal conductance, TEC curves, heat
rejection and PCM properties are missing. The copper solid is an unpatterned
geometric envelope, not routed wiring. Laminate joining is declared, but adhesive
thickness is not resolved. The controller's original lumped mass is retained and
its new rigid visual sublayers have zero added simulation mass to avoid counting
that mass twice. Their concealment inside the module envelope is intentional;
`layer-exploded.png` exposes them for review.

## Reproduction and source

From the extracted archive, `bash reproduce_v3.sh` applies the included combined
patch to pinned dedicated checkouts, compiles native layer geometry, validates the
bundle, runs MachineRuntime, renders states and composes OpenIndustries. Prerequisite
installation commands are comments in that script. No Gymnasium, Blender, PyTorch,
MyoSim or additional runtime is required.

Local OpenForma V3 commit: `7456aedddd49384b70c6410b64e0516248b99d9b`, based on the
V2 adapter commit `70e535b`. `openforma-v3.patch` includes both changes against
`c22c86e`; `openforma-layers-only.patch` contains only V3. No remote push or PR was
made. The ZIP includes JSON inputs, STEP parts, source patches, test outputs, runtime
trace, rendered views, OI scene and verification receipts.

## Exploded-view BOM and wiring update

The updated exploded view includes a 17-line grouped BOM covering all 40 device
solids plus three internal laminate layers. It draws 23 proposed external branches
covering all 10 declared electrical nets. These are graphical connection routes
between module reference points, not manufactured wire paths. PCB-internal traces
are omitted. Connector positions, gauge, cut length, insulation, strain relief and
routing clearances remain unknown. See `bom.csv`, `external-wires.csv`,
`external-wires.json` and `exploded-view-verification.json`. No electrical or
runtime model was changed. The annotated image uses native OpenCAD tessellations
with Matplotlib for projection, wire overlays and BOM labels.

## Issue-specific evidence update

`issue-evidence/system-architecture.json` validates using OpenForma's existing
SystemArchitecture and architecture usability check. A separate negative fixture
substitutes the ten integer children from issue 283 and confirms matching error
locations. This is a schema-pattern check, not a reproduction of the original
CAID hosted agent run or proof that its bug is fixed.

The existing native schematic renderer was corrected to retain distinct net
endpoints and avoid overlapping fallback modules. It now accepts native ElectricalIR.
`issue-evidence/electrical-interface.svg` and its PNG preserve all 33 endpoints on
10 nets. These are module interface pins; component circuits/package assignments
remain incomplete. Six new renderer tests and eight existing architecture tests
pass, bringing the relevant recorded total to 164 passed, seven optional skips.
The 90-pass domain regression suite was also rerun successfully. Runtime evidence
is unchanged because no runtime or physical model changed.

Latest local source commit: `099eae7` (after `7456aed`). Combined source patch updated.
No PR or remote source publication was performed. `issue-evidence/gaps.json`
records exact physical-design blockers; `issue-evidence/issue-comment.md` is the
prepared comment text. It contains no claim of hardware readiness.

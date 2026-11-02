# ProcessIR v1 — OpenForma's independent process design model

[Implementation issue #26](https://github.com/Mapped-Assembly/OpenForma/issues/26) ·
[Seven-domain epic #24](https://github.com/Mapped-Assembly/OpenForma/issues/24)

`ProcessIR` defines a **flowsheet**: unit operations, their typed input/output
ports, directed material/energy/signal streams, declared design rates, scenarios
and optional, explicit conservation checks. It **does not** schedule jobs,
operate pumps, create simulation measurements, infer material conversion, or
establish legal compliance. OpenIndustries owns runtime simulation and resource
accounting; external providers own compliance evaluation.

## Public API and command examples

```bash
python -m forma_core domains validate-domain examples/domains/process-irrigation.json
python -m forma_core domains validate-domain examples/domains/process-air-to-fuel.json
python -m pytest tests/projects/test_process_ir.py -q
```

The `process` domain participates in the existing lazy `forma_core.domains`
registry. `python -m forma_core domains registry` requires a JSON input path;
use `echo '{}' > /tmp/empty.json` then `python -m forma_core domains registry /tmp/empty.json`.
`schema` works with `{"domain":"process"}`. The same operations are
available via the existing `forma.opencode.domains` MCP bridge; no new
process-specific MCP server or runtime is required.

Process-only examples do not import or instantiate any chemistry model.
The [irrigation fixture](../examples/domains/process-irrigation.json) has a
source, pump, and destination, with illustrative rates. The
[air-to-fuel fixture](../examples/domains/process-air-to-fuel.json) has
capture, electrolysis, methanol synthesis, conceptual downstream conversion,
storage, and a **declarative** 40% power-availability reduction. It does not
predict yield or actual operating performance.

## Identity, ownership, and typed links

Each `ProcessOperation` is identified by `operation_id`, with typed `ports`.
`ProcessStream` has its own ID and references actual operation/port IDs.
Optional numeric design `quantity` states a dimension and unit explicitly.
Conceptual streams may omit quantities without substituting zero.

Composition uses `ArtifactReference` and `DomainLink` from the existing
domain system. `uses_reaction` and `uses_species` connect process operation
or stream entities to *version-pinned ChemicalIR* reaction/species entities.
ChemicalIR remains authoritative for reaction identities and formulas. Revisions,
object types and source hashes are checked at the composition boundary.
The `uses_equipment` (MechanicalIR), `uses_electrical_port` (ElectricalIR),
and `uses_fluidic_port` (FluidicIR) relationships additionally check expected
owner-domain and whether the process source is an operation, energy port,
or material port as appropriate. `uses_thermal_interface` is reserved for
a future ThermalIR provider; it is not an assertion that ThermalIR exists.
These relationships are composition artifacts, not schema dependencies.

The `ProcessIR` package must remain usable without ChemicalIR, FluidicIR,
MechanicalIR, ElectricalIR, ThermalIR or SpaceIR. Missing external evidence or
provider availability is a **composition/evaluation finding**, not an excuse
to fabricate process data.

## Conservation and model limitations

The initial optional process balance adapter checks explicitly supplied
**mass-rate or molar-rate** bookkeeping, independently from any chemical reaction
model. Units are converted to `kg/s` or `mol/s` using deterministic conversion
factors, not density or assumed molecular weight. The documented residual
convention is:

```
inlet + generation - outlet - consumption - accumulation
```

`pass`, `fail`, `unknown` and `unsupported` are distinct results.
A missing declared rate or accumulation/generation/consumption input returns
`unknown`; a wrong incident flow dimension returns `unsupported`. Only an
explicitly complete, numerically consistent set of declared values returns
`pass`. A failed declared balance produces a blocking finding; unknown coverage
produces a nonblocking warning, **never** proof that a process is quantitatively
verified.

The graph supports split/merge, parallel flows, closed paths and explicit
recycles. Strongly connected components are **reported**, not rejected.
A downstream numerical solver must establish tear streams, initial conditions
or its own convergence contract before evaluating recycle loop performance.
Optional declarative `ProcessStep` prerequisites form a distinct **acyclic**
dependency graph. A cycle in that workflow is invalid, while a physical recycle
in the flowsheet is permitted. These steps are design intent, not runtime jobs
or actuator commands; the orchestrator's task DAG is separate.

Mixed mass/mole/volume conversions require independently supplied properties
and supported phase assumptions. There are no implicit chemical yields,
gasoline conversions, power/heat duty estimates or sensor observations.
`ProcessScenario` stores an engineering assumption, **not** a simulation result
or actuator command.

## Implementation boundary

- `forma_core/processes/models.py`: strict typed process designs.
- `forma_core/processes/validation.py`: directed topology/cycle analysis and
  conservative, explicitly requested bookkeeping.
- `forma_core/domains/registry.py`: lazy ProcessIR provider, local validation,
  operation/port discovery and process-owned fact authority.
- `forma_core/domains/compatibility.py`: explicit lossy HardwareIR report.
- `tests/projects/test_process_ir.py`: positive, negative, revisioned
  composition and legacy compatibility fixtures.

Passing these checks does **not** establish thermodynamic, chemical, mechanical
or operating safety. Preserve source dates, evidence status, claimed model
fidelity and unknown values in all subsequent integrations.

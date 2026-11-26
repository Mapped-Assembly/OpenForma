# Declarative empirical physical process verification

An experiment is a declaration of native artifacts, evaluator invocations, protocol,
required inputs and acceptance criteria. A new experiment using a supported model
requires data, not experiment-specific Python. A new scientific capability requires
a reusable, versioned domain evaluator.

The first implementation coupled every experiment to ChemicalIR and stoichiometry.
The current named-evaluation path removes that requirement. ChemicalIR, thermal
models and externally registered domains use the same preparation, observation and
comparison pipeline. No new engineering IR, robot, HardwareIR, CAD or scene is needed.

**No physical experiment has been performed.** The published case-study results are
software benchmarks with explicitly synthetic observations.

## Declaration contract

| Field | Authority and purpose |
| --- | --- |
| `process` | Native ProcessIR operations and dependency-ordered ProcessStep protocol |
| `artifacts` | Native/external documents with existing ArtifactReference identity, revision, provider version and content hash |
| `evaluations` | Stable invocation ID, source artifact ID, registered evaluator ID/version and model-owned parameters |
| `required_inputs` | Actual-input obligations bound by a path to a dimensioned artifact quantity or evaluator parameter; expected values are resolved, not copied |
| `criteria` | Named evaluation/output target, scientific basis, reporting unit, measurement method, tolerance and proxy assumptions |
| `conditions` | Preregistered operating/measurement condition intervals |
| `assumptions`, `limitations` | Explicit applicability and unresolved scientific limitations |

For example, the thermal case declares:

```json
{
  "evaluations": [{
    "evaluation_id": "mix-equilibrium",
    "artifact_id": "water-mixing.thermal",
    "evaluator_id": "adiabatic-equilibrium",
    "evaluator_version": "1.0",
    "parameters": {
      "node_ids": ["warm", "cool", "cup"],
      "closed_adiabatic": true,
      "constant_capacities": true,
      "no_phase_change": true,
      "no_reactions": true
    }
  }],
  "criteria": [{
    "criterion_id": "mixed-temperature",
    "target": {"evaluation_id": "mix-equilibrium", "output_id": "final_temperature"},
    "basis": "equilibrium_temperature",
    "unit": "degC",
    "absolute_tolerance": 1.0,
    "method": "direct"
  }]
}
```

This is an excerpt, not a complete request. Complete authored requests live in
`forma_core/experiments/water-mixing.json` and
`forma_core/experiments/vinegar-bicarbonate-declarative.json`.
Changing masses, temperatures, container properties, identities, sequencing or
criteria changes the declaration and its hashes. No `if water`, `if vinegar`,
benchmark IDs or case-specific constants appear in the evaluator/comparison path.

## Agent workflow

1. Use existing domain selection/generation to author the relevant native IRs.
2. Call `forma.opencode.domains`, operation `experiment`, payload `{"action":"catalog"}`.
   Discover available evaluator IDs, versions, parameter schemas, outputs and
   limitations. `{"action":"schema"}` exposes the generic authoring/run contract.
3. Author an experiment request using native documents and named evaluations.
   Bind actual inputs to source quantities and criteria to the returned output IDs.
   Protocols are declarative ProcessStep descriptions; no automatic safety inference
   or arbitrary expression execution is performed.
4. Call `{"action":"prepare","request":...}` to calculate and freeze the plan.
   For authenticated timestamped persistence use `POST /experiments/plans`.
5. Perform the manual protocol; record actual inputs, conditions, readings, method,
   uncertainty, precision, source records and photographs separately from predictions.
6. Call `{"action":"compare","comparison":{"plan":...,"runs":[...]}}` or save
   via `POST /experiments/reports`. Unsupported models never fall back to a guessed
   equation, and synthetic agreement never contributes physical coverage.
7. Refine through a new plan revision linked to its original scientific hash and
   saved parent receipt. Observations remain attached to the historical prediction.

Python uses `prepare_experiment(request, registry=...)` and
`compare_experiment(plan, runs, registry=...)`. CLI uses
`python -m forma_core domains experiment request.json`. REST `/experiments/tools`
and MCP dispatch the same operations. The UI `/experiments` discovers case studies
from the packaged declarations and renders generic inputs/output criteria; it does
not require a chemical species or a stoichiometric result.

## Existing registry, not a second plugin system

`DomainRegistry.evaluator_catalog()` asks its existing registered providers for an
optional `evaluation_specs()` capability. `DomainRegistry.evaluate_named()` verifies
the exact artifact identity, content hash, provider and model version, then calls
that provider's `evaluate_named()` implementation. Older providers without this
capability remain compatible and produce explicit unsupported/unavailable findings.

Requests and normalized receipts in `domains/evaluation.py` are transport/evaluation
envelopes, not another domain IR. Full provider-owned raw receipts are retained.
Scientific model implementations remain in their domain packages.

| Domain | Named evaluator | Current numerical scope |
| --- | --- | --- |
| Chemical | `stoichiometric-bound@1.0` | Existing exact single-reaction forward bounds; species amount/mass formation and final inventory |
| Thermal | `lumped-resistance@1.0` | Existing single-zone steady-state model |
| Thermal | `adiabatic-equilibrium@1.0` | Equilibrium endpoint and sensible-energy accounting for explicitly contacting, closed, constant-capacity bodies |
| External | Provider-defined | Explicitly registered, independently versioned model and named dimensioned outputs |

An external-provider test adds an unrelated viscosity output without modifying the
experiment or comparison code. It demonstrates software extensibility, not scientific
validation of an external rheology model. Unregistered models remain unsupported.

ThermalIR already supported node capacity and initial temperature. It did not encode
mass-derived capacity, so two optional, unit-checked node quantities were added:
`mass` and `specific_heat`. The equilibrium evaluator calculates `C=m*c`, or uses an
explicitly supplied capacity. Competing capacity definitions are rejected. Missing
properties remain unknown. Absent new fields serialize exactly as before, retaining
old ThermalIR hashes; no new IR or incompatible schema migration is introduced.

## Integrity, comparisons and uncertainty

- Version-2 plans freeze complete declarations, ordered protocol, resolved inputs,
  source pins, evaluator receipts and hashes. The registry identity is explicit.
- Comparison verifies the original hash and reruns the named model. Unavailable
  providers/models block dependent comparisons rather than reusing cached passes.
  The original receipt remains in the report; availability is reported separately.
- A criterion references `evaluation_id/output_id`, not a chemical species.
  Scientific basis must match the evaluator output and observation.
- Convert compatible quantities by dimension. Kelvin/Celsius offsets affect values;
  uncertainty and resolution use scale only. Absolute-temperature ratios/relative
  errors are omitted rather than returning misleading Celsius ratios.
- Signed discrepancy = observed − reference. Ratio-scale quantities additionally
  report absolute/relative error and observed/reference ratio where the denominator
  is nonzero. Only outputs explicitly marked `upper_bound` trigger above-bound flags.
- Instrument uncertainty is an absolute bound. Direct/difference methods preserve
  unknown bounds; a difference sums bounds. An interval inside tolerance passes,
  outside fails, overlapping remains unknown. Inadequate precision stays unknown.
- Physical coverage counts conclusive criteria from physical observations only.
  Repeatability requires distinct sample IDs. Optional cost/currency and elapsed
  time remain attached to each trial.
- Model conservation residuals are distinct from measured material/energy closure.
  A solver returning zero joules does not establish experimental energy accuracy.

The original species-based request/receipt is retained as a legacy v1 adapter.
`tests/projects/fixtures/empirical-v1-plan.json` was frozen by commit `c4ae002`
before generalization; tests require byte-equivalent JSON serialization and successful
integrity verification after reading it. New authoring should use named evaluations.
The no-ID `benchmark` action retains the old fixture for existing callers.

## Persistence and evidence

Existing authenticated REST endpoints and private content-addressed storage are reused:

- `POST /experiments/plans`: `{request, parent_record_sha256?}`.
- `POST /experiments/evidence`: raw JPEG/PNG/WebP/text/JSON, up to 8 MiB.
- `POST /experiments/reports`: `{plan_record_sha256, runs}`.
- `GET /experiments/records/{plans|reports|evidence}/{sha256}`: owner-scoped recovery.

Actual run timestamps must follow plan freeze. Ownership comes from authentication,
never a submitted user ID. Files are hashed on upload and checked on retrieval.
Photographs are preserved; no automatic reading extraction or authenticity guarantee
is claimed. Keep saved digests/downloads for recovery; there is no new experiment
index or autosaved unsent form state. MCP operations return portable records;
authenticated REST provides durable timestamped persistence.

## Architecture audit and remaining limitations

Audited baseline `3a1125432ce3b7412478c82bbc07f6b26fb1dfea`, current source,
CONTRIBUTING.md, open issues and active PRs. No repository AGENTS.md was present.
Reuse includes domain generation/selection, ChemicalIR/ProcessIR/ThermalIR,
ProcessStep, exact stoichiometry, bounded thermal evaluation, ArtifactReference,
evidence/provenance and private artifact storage. Related issues: #40, #42, #44,
#59. Pending kinetics PR #47 is not imported or required.

The original gap was the frozen-prediction/observation loop. The follow-up removes
its chemistry coupling. Native IR generation remains the existing pipeline; agents
compose the discovered evaluator/protocol declaration through existing tools.
No second natural-language generator or per-experiment execution script is added.

Remaining scientific limitations:

- No transient mixing/cooling, evaporation, precipitation, phase-change, emulsion,
  gelatinization, fermentation or industrial scale-up solver is implied.
- The new equilibrium model requires explicit no-reaction/no-phase-change,
  constant-capacity and adiabatic assumptions. It cannot predict equilibration time.
- Feed/property uncertainties are retained but not propagated into prediction bounds.
- Protocol prose and relationships between independently authored domain artifacts
  still require review. No general procedural-semantics or safety solver is claimed.
- No parameter fitting, automatic model correction, physical authenticity guarantee
  or independent calibration/validation data split is implemented.
- Generalization tests cover data-driven variants and an external adapter; physical
  generalization across processes remains unmeasured.

See [the thermal case study](water-mixing-case-study.md) for reproducible inputs,
independent expected values and the published synthetic benchmark report.

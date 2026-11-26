# Empirical physical process verification

OpenForma can freeze native chemical/process artifacts, calculate a supported
conditional prediction, publish a manual protocol, and compare later observations
against the original criteria. No HardwareIR, CAD, scene, robot, MuJoCo or automatic
execution is required. **The included benchmark has not been physically performed.**
All observations in automated tests are synthetic software inputs, even where a test
exercises the physical-record validation path.

## Architecture audit (2026-11-26)

Baseline: `3a1125432ce3b7412478c82bbc07f6b26fb1dfea` on `main`. No repository
`AGENTS.md` was present. The request's Python typing/documentation conventions apply.
Inspected `CONTRIBUTING.md`, all open issues and active PRs before implementation.

| Existing contract / implementation | Reuse and gap |
| --- | --- |
| `forma_core/domains/selection.py`, `interaction.py`, `generation.py` | Independent domain selection and agent-driven generation already exist. Keep `/generate` and MCP's domain authoring flow; do not introduce a second generator. |
| `chemistry/models.py`, `stoichiometry.py` | Native species, reactions, Claim/EvidenceRecord, exact inventory accounting, limiting reagent and versioned prediction receipt. Call this evaluator instead of rebuilding arithmetic. |
| `processes/models.py` | Native batch streams and `ProcessStep` dependency DAG. Use steps for human protocols, with no execution backend. |
| `thermal/models.py` and domain registry | Thermal design and bounded steady-state evaluation exist, but cannot predict this reaction's heat or evaporation from its equation. Energy remains unsupported in this slice. |
| `domains/base.py`, `providers.py` | Reuse strict models, canonical JSON SHA-256 and `ArtifactReference`. Pin complete request, native artifacts, protocol and criteria. |
| `persistence/project_artifacts.py` | Reuse private, content-addressed local/Supabase/S3 artifact storage. Add owner-derived namespaces for plans, evidence and reports; no new measurement database. |
| Existing project revisions | Store domain generation as before. Experimental receipts are separate immutable artifacts, not fake HardwareIR project revisions. Refinements link predecessor receipts. |
| `domains/tools.py`, CLI, `opencode/models.py`, `apps/api/opencode_mcp.py` | Add one `experiment` operation, shared with REST and Python. MCP remains a stateless portable tool; hosted receipt persistence is through authenticated REST. |
| Next.js + FastAPI, `vercel.json` | Add `/experiments` page and authenticated `/experiments/*` API router. Reuse `useFormaAuth`, `webConfig.apiBaseUrl` and `require_user_context`. |

Related issues: #40 (chemical evaluation), #42 (public solver orchestration), #44
(observation comparison), #59 (general scoped evidence). This slice advances #44,
but does not close its time-series or full uncertainty requirements. Active PR #47
adds first-order kinetics and was intentionally not imported; the two-reactant
benchmark is outside that model anyway. Active #49/#52/#53 concern robot runtime
and spatial contracts, and #16 concerns a liver-chip demonstration; none is required.

Documentation lag confirmed: `docs/unified-generation.md` said no limiting-reagent
solver was installed, while `stoichiometry.py` already implements it. This change
exposes it through the empirical workflow without claiming general automatic model
selection or completing every part of #42.

## Public workflow

1. Use the existing remote `/generate` or `forma.opencode.domains` generation flow
   to select domains and author native artifacts from an objective. An external
   agent can author without server LLM credentials; no local clone is required.
2. Use `experiment` with `{"action":"schema"}` to discover the experiment request.
   Supply the ChemicalIR/ProcessIR artifacts, a pinned stoichiometry request,
   explicit conditions, feed tolerances, assumptions and criteria. Protocols are
   authored `ProcessStep` descriptions, deterministically ordered by dependencies.
   There is no automatic chemistry-to-safe-protocol inference.
3. Review and freeze via `POST /experiments/plans`. The server recomputes the
   prediction and records its own creation time before physical execution.
4. Perform the manual protocol. Record actual species feed amounts (including
   concentration calculations for mixtures), conditions, raw readings, instrument
   resolution, uncertainty if known, deviations and source records. Keep photographs.
5. Upload photographs/raw bytes to `POST /experiments/evidence`; submit runs to
   `POST /experiments/reports` using the saved plan's record digest. Each run also
   pins the scientific plan hash. Physical timestamps must follow plan freeze and
   precede submission. These are user assertions, not proof of measurement authenticity.
6. Download a reproducible JSON report with embedded original artifacts, predictions,
   observations, evidence references, evaluator version and input hashes. Raw image
   bytes remain separately downloadable private evidence artifacts.
7. Refine by creating a new plan revision, supplying both `parent_plan_sha256` and
   `parent_record_sha256`. The server verifies experiment identity and consecutive
   revision; prior plans and reports remain retrievable without alteration.

### Phone interface

On a deployment of this branch, open `/experiments`, sign in if required, load the
initial benchmark or paste a generated request, and freeze it before starting.
The page displays the protocol and criteria, accepts instrument readings and photos,
and supports fresh repeat trials and report downloads. Blank fields never become
measurements. A missing uncertainty can be submitted and produces an inconclusive
comparison. Raw evidence does not undergo automated image interpretation.

Keep the saved plan/report digest or downloaded JSON for recovery. Paste the digest
into **Restore plan/report** to resume on another authenticated device. There is no
server-side experiment list/index in this first slice. Unsaved form state is not
persisted across navigation. Downloaded report JSON is portable; its attachment
references are not a self-contained image archive. Model refinement is available
through REST/SDK/MCP rather than a dedicated UI editor. The page offers authored
request input, not a second natural-language chat surface.

### API and MCP

| Interface | Operation |
| --- | --- |
| Python | `prepare_experiment(ExperimentRequest)`; `compare_experiment(plan, runs)` |
| CLI | `python -m forma_core domains experiment request.json` (developer reproducibility only) |
| MCP | `forma.opencode.domains` with `operation: "experiment"`, `payload` containing JSON text |
| REST, pure | `POST /experiments/tools` with the same action payload |
| REST, saved | `POST /experiments/plans` with `{request, parent_record_sha256?}` |
| REST, evidence | `POST /experiments/evidence`, raw bytes, supported Content-Type, at most 8 MiB |
| REST, saved comparison | `POST /experiments/reports` with `{plan_record_sha256, runs}` |
| REST, recovery | `GET /experiments/records/{plans|reports|evidence}/{sha256}` |

Pure actions: `schema`, `benchmark`, `prepare` with `request`, and `compare` with
`comparison: {plan, runs}`. CLI/MCP reports are portable but not implicitly uploaded.
All REST endpoints use existing authentication. Storage namespace ownership comes
from the authenticated identity, never a client-supplied owner. Missing/private
storage fails explicitly; there is no successful acknowledgement of an unsaved
record. Production must use durable Supabase/S3 storage and deployed authentication.

## Initial benchmark: acid/bicarbonate, open-batch mass-loss proxy

The packaged fixture is `forma_core/experiments/vinegar-bicarbonate.json` and is
available remotely through `{"action":"benchmark"}`. It reuses the native species,
reaction and process streams from `examples/domains/chemistry-generation.json`.

Nominal inputs: 2.00 g sodium bicarbonate and 50 mL food vinegar with an **explicit
5 g acetic acid / 100 mL basis**, giving 2.5 g acid. An unspecified “5%” label is
insufficient. No concentration basis, density or measurement is inferred. Actual
feed differences outside preregistered tolerances prevent comparison and require a
new plan before execution. Feed precision and uncertainty are retained but not
propagated into the nominal bound.

`NaHCO3 + C2H4O2 → C2H3NaO2 + H2O + CO2`

With supplied nominal molar masses, bicarbonate limits the theoretical result:

`2 / 84.0066 × 44.0095 = 440095/420033 g ≈ 1.047763 g CO2`.

This assumes complete forward reaction; it does not predict the actual yield or
reaction duration. Both input and property assumptions remain in the receipt.
The 0.20 g acceptance tolerance is a chosen screening criterion, not a confidence
interval or certified accuracy. A 1 g kitchen scale cannot resolve this test; use
an available 0.01 g scale or choose a different experiment rather than claiming a
pass with inadequate equipment. No equipment prices have been verified.

Weigh the same open system before/after (tray, bowl, cup and spoon all retained),
slowly mix in an open bowl with headroom, and record stable readings, temperature,
labels, deviations and evidence. The native protocol includes stop conditions and
ordinary food-ingredient precautions. Never seal the gas-evolving mixture.

The difference is explicitly a **proxy** for CO2 formation. Splashing, removed
objects, evaporation and retained dissolved CO2 can bias it. Confirming assumptions
is an operator assertion, not a measurement of those biases. Even an interval-based
`pass` means agreement within this conditional proxy comparison only. It is not a
full mass-balance closure, proof of completion, isolated yield, safety certification
or evidence of industrial-scale applicability.

## Comparison and metrics

- Original receipts are recomputed before comparison. Modified predictions,
  protocol or tolerances are rejected; run hashes bind exact historical plans.
- Separate net formation, total inventory and isolated product. Total inventory
  includes initial product. Isolated product requires a recovery assumption and
  currently rejects nonzero initial product inventory as unsupported.
- Exact compatible batch-unit conversion: g/mg/kg and mol/mmol/kmol. No inferred
  density, gas volume, temperature conversion or molar-mass conversion at the
  observation boundary. Incompatible quantity/condition units remain unsupported.
- Signed discrepancy = observed − reference; absolute error = its magnitude;
  relative error = absolute error / |reference|; ratio = observed / reference.
  Zero denominators remain null. Above-bound observations are retained and flagged.
- Uncertainty fields are absolute bounds. A difference uses the sum of the two
  bounds, not an unjustified independent-Gaussian assumption. Unknown uncertainty
  stays null. An interval entirely within tolerance passes, entirely outside fails,
  and overlapping intervals remain unknown. Excessive instrument resolution also
  leaves the verdict unknown. No statistical significance claim is made.
- Physical coverage counts distinct criteria with conclusive physical comparisons,
  not schema validity or synthetic agreement. Repeatability reports the sample
  standard deviation of compatible physical scalar observations from distinct
  sample IDs; two readings from one trial are not two independent trials.
- Store per-trial elapsed time, optional cost/currency, and explicit unsupported
  criteria. No currency conversion or manufacturing cost extrapolation.
- Full measured material balance remains unknown; energy evaluation is unsupported;
  unseen-process generalization remains unknown until held-out physical studies.
  The solver's theoretical conservation receipt remains separate from these metrics.

## Progressive scientific coverage

| Benchmark | This slice | Next scientific requirement |
| --- | --- | --- |
| Vinegar + baking soda | Conditional stoichiometric prediction and mass-loss proxy comparison | Feed-uncertainty propagation; evaporation/retention controls |
| Saltwater evaporation | Not implemented | Batch phase-change accounting, residual-water measurement, energy inputs |
| Milk + vinegar | Not implemented | pH-dependent precipitation, composition and separation/recovery model |
| Oil + water emulsification | Not implemented | Droplet/stability metrics, multiphase model and time dependence |
| Starch gelatinization | Not implemented | Time-temperature history and measurable property transition |
| Yeast fermentation | Not implemented | Biological kinetics, substrate/product/biomass accounting and matched conditions |

The scalar comparison layer has no benchmark-specific reaction arithmetic. Its
first prediction adapter is deliberately the existing single-reaction stoichiometric
solver. Adding thermal/biological/time-series adapters does not require changing
engineering IR ownership or adding an execution backend. No parameter fitting,
automatic model correction, independent validation-data split, safety evaluator or
industrial scale-up model is implemented.

## Deployment audit and remaining operational gate

`vercel.json` declares Next.js frontend + FastAPI backend services; the latter includes
`forma_core/**`. The existing frontend `webConfig.apiBaseUrl` uses
`NEXT_PUBLIC_API_URL`/`NEXT_PUBLIC_BACKEND_URL` and otherwise a legacy host. Configure
it to the actual backend running this branch. Backend artifact storage and authentication
must already be configured; JSON and photo storage use the same private backend.

The connected Vercel team listed an older `blueprint-oss` project (framework
`services`, reported `live: false`), with `caid-technologies.us`,
`mcp.caid-technologies.us` and `forma-oss.vercel.app` among its domains. Its latest
listed deployment was `0b4c0cad…` from **caid-technologies/Form-OSS**, and the latest
listed production deployment was `ffd6772d…` from that same older repository.
This is not evidence that Mapped-Assembly/OpenForma main or this PR is deployed.
No unrelated project or legacy production domain was changed.

The remote routes/page are implemented and tested through HTTP. A live phone URL
for this feature still requires deploying this PR to an authorized OpenForma host
with the correct frontend API URL, authentication and durable private storage.
No production deployment, hosted LLM run or real kitchen trial is claimed here.

## Reproduce software verification

```bash
python -m pytest tests/projects/test_empirical_verification.py tests/projects/test_empirical_api.py \
  tests/projects/test_stoichiometry.py tests/projects/test_chemical_ir.py \
  tests/projects/test_process_ir.py tests/projects/test_thermal_ir.py -q
```

Tests cover expected arithmetic, shortfall/above-bound results, zero reference,
initial product inventory, unit conversion, missing uncertainty, inadequate
precision, incompatible species/bases/conditions, tampering, stale plans,
refinement, synthetic evidence, repeated trials, transport equivalence, private
storage round trips, owner isolation, pre-freeze observations and storage failure.

Executed checks for this implementation:

- Focused empirical + chemistry/process/thermal suite: **80 passed, 57 subtests passed**.
- Existing generation/domain/provider/MCP suite: **122 passed, 1 skipped, 136 subtests passed**.
  The skip is the pre-existing opt-in native OpenCAD integration test.
- Mobile viewport Playwright tests: **2 passed**, using mocked transport and synthetic inputs.
  The usual browser download returned HTTP 403 and the system Chromium launcher lacked
  its executable. Tests ran with a separately installed Chromium binary; no browser
  dependency or local override was added to the application.
- TypeScript checking, targeted ESLint, production Next.js build, Python package build
  and whitespace checks passed. The wheel contains the benchmark JSON resource.
- Full repository test suite and live hosted inference were not run. No physical
  measurements were collected.

Structural protocol validation does not establish that arbitrary authored prose is
scientifically meaningful or agrees with every calculation input. Review changed
feed quantities, typed streams, conditions and procedure text together before freezing
any refinement. The fixture's linked quantities are explicitly authored; this slice
does not add a general cross-domain procedural semantics solver.

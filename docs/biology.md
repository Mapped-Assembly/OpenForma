# Biological experiment interface

## Independent domain interface

`forma_core.biology.ir.BiologicalIR` provides a versioned envelope around the
existing `BiologicalExperiment` contract described below. It adds no biological
simulation. See [Independent domain IRs](domain-irs.md) for independent authoring
and separately validated fluidic relationships.

Forma's biological layer is an application-facing metadata contract that composes
with the existing reusable `FluidicSystem`. It records the biological question,
compartments, exposures, controls, endpoints, sampling, observations, external
reference outcomes and provenance without turning the generic fluidics schema into
an organ-specific model.

This milestone does **not** implement a toxicity predictor, biological simulator,
clinical safety assessment or wet-lab protocol generator.

## Models

Import the contract from `forma_core.biology`:

```python
from forma_core.biology import BiologicalExperiment, validate_biological_experiment
from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

project = HardwareIntermediateRepresentation.model_validate_json(project_json)
experiment = BiologicalExperiment.model_validate_json(experiment_json)
issues = validate_biological_experiment(experiment, project.fluidic_system)
```

`BiologicalExperiment` contains:

- `BiologicalCompartment`: cell/tissue metadata plus the channel/chamber IDs that physically host it.
- `CompoundExposure`: declared compound, concentration metadata, timing and evidence references.
- `ExperimentalControl`: untreated, vehicle, positive, negative or reference comparison arms.
- `BiologicalEndpoint`: planned viability, injury, function, imaging, omics, biomarker or other measurements.
- `SamplingPoint`: planned time, compartment and endpoints.
- `DatasetReference`: source/version/license/citation metadata for external evidence.
- `BiologicalObservation`: imported or measured values with mandatory dataset provenance.
- `ReferenceOutcome`: external labels such as a DILI reference category, kept separate from observations and predictions.

Internal IDs and references are checked during Pydantic validation. The deterministic
`validate_biological_experiment` adapter checks that biological compartments map to
real fluidic channels/chambers when a `FluidicSystem` is supplied.

## Liver DILI example

`examples/organ-on-chip/liver-dili-biological.json` layers a liver/DILI study
scenario on the existing validated two-channel organ-chip fixture. It uses an FDA
DILIrank reference record to demonstrate provenance, while the concentration field
is deliberately reference-relative rather than a wet-lab dose recommendation.

Run the integrated check:

```sh
python -m forma_core.biology.benchmark \
  examples/organ-on-chip/liver-dili-biological.json \
  --project examples/organ-on-chip/two-channel.json
```

The command validates both the existing fluidic system and the biology-to-fluidics
links and emits JSON suitable for CI or a demo. Its `claims` block is deliberately
explicit that toxicity prediction, biological simulation and clinical safety
assessment are false for this milestone.

## Published evidence workflow

Select a compound and export its published evidence as a validated experiment:

```sh
python -m forma_core.biology.liverchip_evidence \
  examples/biology/ewart-liverchip-v0.json \
  --compound Troglitazone \
  --template examples/organ-on-chip/liver-dili-biological.json \
  --project examples/organ-on-chip/two-channel.json \
  --output /tmp/troglitazone-evidence.json \
  --experiment-output /tmp/troglitazone-experiment.json
```

The report contains dose-response means and descriptive SEM, raw observations,
source hashes and row references, published margins, missing-data flags, and
physical/interface validation. `evidence.status` is independent of
`validation.passed`: structural validity does not establish evidence availability
or drug safety. `complete` means no missing numerical entries among the supplied
response rows, not complete biological evidence.

Response data are available for Clozapine, Olanzapine, Troglitazone, Pioglitazone,
Trovafloxacin and Levofloxacin. Other compounds in the 27-compound manifest retain
their published margins and reference labels but report `unavailable` response
evidence. Unknown names fail with exit code 2; names and IDs match exactly apart
from case and outer whitespace. Broken physical mappings return exit code 1.
A valid report, including one reporting unavailable evidence, returns zero.

The checked-in `examples/organ-on-chip/liver-dili-ewart-biological.json` demonstrates
Troglitazone: **36 source rows, 32 numerical measurements, four missing results**.
It also works directly with the existing interface checker:

```sh
python -m forma_core.biology.benchmark \
  examples/organ-on-chip/liver-dili-ewart-biological.json \
  --project examples/organ-on-chip/two-channel.json
```

Imported observations carry compound ID, dose and unit, measurement status,
missing reason and original missing marker. `observed` records still require
exactly one numerical or categorical value; `missing` records require a reason
and no measured value. `DatasetReference.sha256` is optional for older datasets.
Existing experiment files remain valid.

This creates a fresh evidence scenario using the template's compartments and
organ model. It excludes the template's illustrative acetaminophen exposure,
controls, sampling plan and labels instead of applying them to a different drug.
Dose is stored on observations as a human Cmax multiple. Source day 3 is encoded
as nominal 72 hours; no exposure duration, donor ID or chip ID is invented.
Compartment association does not imply that the Forma fixture generated the
measurements or is equivalent to the publication's device.

See [Ewart reproduction details](ewart-liverchip.md) for licensing, source
limitations, the donor-2 discrepancy, and the corrected result that remains
unreproduced. This workflow does not produce a new toxicity prediction.

## LiverBench v0

`examples/biology/liverbench-v0.json` is a hand-audited 24-compound reference slice
joined across two FDA Liver Toxicity Knowledge Base resources:

- DILIrank 2.0: 1,336 FDA-approved drugs ranked as `vMost`, `vLess`, `vNo` or
  `Ambiguous-DILI-concern` on the FDA source page.
- DILIst: 1,279 drugs with a binary human DILI classification on the FDA source
  page (768 positive, 511 negative; 0 = negative, 1 = positive).

The v0 slice is balanced 12 positive / 12 negative and split 12 train / 6 dev /
6 heldout. It deliberately includes eight challenge records: four DILIst-positive
drugs that are only `vLess-DILI-concern` in DILIrank, and four DILIst-negative
records that are `Ambiguous-DILI-concern` in DILIrank. The purpose is to prevent
the initial benchmark from containing only easy high-concern versus no-concern
examples.

Every row retains the FDA source record IDs, source compound names, DILIrank
severity/concern, DILIst classification, route, cohort and split. The repository
stores only this small derived manifest rather than mirroring the complete FDA
lists.

Validate the dataset and exercise all 24 records through the biological→fluidic
interface:

```sh
python -m forma_core.biology.liverbench \
  examples/biology/liverbench-v0.json \
  --project examples/organ-on-chip/two-channel.json
```

That command should report interface coverage; it is **not** a toxicity-performance
result. LiverBench itself generates no predictions.

To score a real model, provide a prediction bundle:

```json
{
  "model_name": "example-model",
  "model_version": "commit-or-version",
  "predictions": [
    {"record_id": "ketoconazole", "predicted_label": 1, "score": 0.93},
    {"record_id": "valproic-acid", "predicted_label": 1, "score": 0.87}
  ]
}
```

Then run, normally against the held-out split:

```sh
python -m forma_core.biology.liverbench \
  examples/biology/liverbench-v0.json \
  --predictions results/predictions.json \
  --split heldout
```

Complete prediction sets report sensitivity, specificity, false-negative rate,
accuracy, balanced accuracy and AUROC when scores are supplied. Incomplete or
unknown record IDs fail closed instead of silently computing a partial result.
Unit tests use synthetic predictions only to verify scorer math; those test values
must never be presented as model performance.

### Reference baseline v0

The first reproducible comparison point is intentionally simple: the **FDA DILIrank
SeverityClass reference baseline**. It uses no learned parameters and never reads
DILIst labels while generating predictions:

- score = `SeverityClass / 8`
- predicted DILI-positive when `SeverityClass >= 5`
- fixed threshold = 5

Generate and score it with:

```sh
python -m forma_core.biology.liverbench_baselines \
  examples/biology/liverbench-v0.json \
  --baseline dilirank-severity-v0 \
  --split heldout
```

The checked prediction bundle is
`examples/biology/liverbench-v0-dilirank-severity-baseline.json`, and the standard
LiverBench scorer can evaluate it directly:

```sh
python -m forma_core.biology.liverbench \
  examples/biology/liverbench-v0.json \
  --predictions examples/biology/liverbench-v0-dilirank-severity-baseline.json \
  --split heldout
```

Expected held-out results for the six v0 compounds are:

| Metric | Result |
| --- | ---: |
| Sensitivity | 0.667 |
| Specificity | 0.667 |
| False-negative rate | 0.333 |
| Accuracy | 0.667 |
| Balanced accuracy | 0.667 |
| AUROC | 0.889 |

The confusion matrix is `TP=2, TN=2, FP=1, FN=1`. The two errors are the two held-out
challenge records: simvastatin (DILIst-positive with DILIrank SeverityClass 3) and
zolpidem (DILIst-negative with SeverityClass 7). A constant-negative no-skill floor
has held-out balanced accuracy 0.5 and AUROC 0.5.

This baseline is **not independent biological validation**. FDA states that DILIst
was created by augmenting DILIrank with additional literature datasets, so the two
sources are related. Treat these numbers as a transparent reference/sanity floor,
not as organ-on-chip predictive performance. Independent biological-response data
or wet-lab measurements are still required for a stronger scientific claim.

FDA source pages:

- https://www.fda.gov/science-research/liver-toxicity-knowledge-base-ltkb/drug-induced-liver-injury-rank-dilirank-20-dataset
- https://www.fda.gov/science-research/liver-toxicity-knowledge-base-ltkb/drug-induced-liver-injury-severity-and-toxicity-dilist-dataset

## Molecular and optional assay baselines

See [LiverBench baseline status](liverbench-baselines.md) for the reproducible
PubChem molecular baseline and the optional local IC10/Cmax assay adapter.
The adapter reports missing-data abstentions and suppresses incomplete-split
metrics. External assay data are not bundled; source licensing and coverage
remain explicit prerequisites for independent biological evaluation.

## Boundary

Keep generic physical primitives in `forma_core.fluidics`. Organ, cell, compound,
assay and dataset semantics belong in the biological/application layer. Future
DILI prediction or digital-twin work should consume this contract and produce
provenanced observations/predictions rather than adding hidden biological behavior
to the fluidic validator.

## Prediction task and independent evaluation

See the [versioned prediction plan](liverchip-prediction-plan.md) for the measured-assay
DILI task, candidate external panel, compound-level exclusion audit and admission
gates. No independent chip test cohort is currently admitted.

# Unified generation verification — 2026-11-04

Baseline: default branch `ed8fa32ef7f9dc9dbdb97070aa48418105f1635e`.
Environment: Python 3.12, Pydantic 2.14.1; dependencies installed from root
`requirements.txt` plus `keyring` and `pytest`. Web dependencies installed with
`npm ci --prefix apps/web`. No provider credentials or live inference were used.

## Executed checks

| Check | Result |
| --- | --- |
| Domain CI command below | 230 passed, 4 skipped; 236 subtests passed |
| New integration tests (`tests/projects/test_unified_generation.py` and `tests/agents/test_domain_generation_persistence.py`) | 15 passed |
| Final external-update failure regression plus new integration tests and existing external-provider tests | 36 passed |
| Completed full unittest discovery on this branch (before the final external-update guard) | 1,259 tests: 1,240 passed, 8 skipped, 10 failures, 1 error |
| Full unittest discovery on a separate clean baseline worktree | 1,244 tests: 1,225 passed, 8 skipped, the identical 10 failures and 1 error |
| `python -m compileall -q forma_core apps/api tests` | Passed |
| `npx --prefix apps/web tsc --noEmit --project apps/web/tsconfig.json` | Passed |
| React server rendering of `EngineeringResultView` with the checked-in chemistry report | Rendered 27,917 bytes; inspected selected domains, balance result, unsupported calculations, unknowns, artifact JSON and download control |
| `git diff --check` | Passed |

Domain CI command (also configured in `.github/workflows/domain-irs.yml`):

```bash
python -m pytest tests/projects/test_unified_generation.py \
  tests/agents/test_domain_generation_persistence.py \
  tests/projects/test_domain_irs.py tests/projects/test_external_domain_providers.py \
  tests/projects/test_chemical_ir.py tests/projects/test_process_ir.py \
  tests/projects/test_thermal_ir.py tests/projects/test_geometry_bindings.py \
  tests/projects/test_component_instances.py tests/projects/test_wiring_compiler.py \
  tests/projects/test_biology.py tests/projects/test_fluidics.py \
  tests/projects/test_cad_generation.py tests/opencode/test_mcp.py -q
```

Full discovery command: `python -m unittest discover -s tests -t .`.
The full suite is **not green**. Matching failure identifiers were compared
between the feature worktree and a clean worktree at the baseline commit.
The final external-update edge case was exercised after the domain CI run in the passing 36-test focused run. A redundant full-suite rerun was interrupted by automatic approval review at an SDK upload-test diagnostic. Inspection showed `api.example.test` is used with mocked SDK transport/fake credentials, but its process was no longer available to resume. That interrupted rerun is not counted as completed.

## Inspected chemistry output

Generated through the standard CLI:

```bash
python -m forma_core generate "Design the bicarbonate/vinegar chemical system" \
  --request-file examples/domains/chemistry-generation.json \
  --output examples/domains/chemistry-generation-result.json
```

The authored request is an explicit reproducible fixture, not an LLM claim.
The public SDK test separately exercises automatic selection, isolated context
extraction, individual authoring and composition with a scripted structured
provider. This verifies integration and scope boundaries; it does not establish
that a production model always chooses correctly.

Inspected the generated JSON, not just exit status:

- Exactly `chemical` and `process` artifacts; no mechanical/electrical/thermal placeholders.
- Independently parseable, hash-pinned artifacts and typed `uses_reaction` / `uses_species` links.
- `NaHCO3 + C2H4O2 → C2H3NaO2 + H2O + CO2`, each coefficient 1; element/charge check `pass`, no residuals.
- ProcessIR 1.1 batch feeds of 2 g and 50 mL; unknown rates stay absent. No invented duration, conversion, output mass or measurements.
- Local/composition validation `is_valid: true` is explicitly separate from engineering evaluation.
- `quantitative_stoichiometry` and `batch_mass_balance` are `unsupported`. The approximate 1.05 g reference is not presented as computed.
- Vinegar percentage basis, completion, duration, gas collection efficiency and actual yield remain unresolved.
- Report `status: partial`; CLI exits 1 while preserving the complete report.

Real SQLite tests verify owner-scoped persistence, round-trip artifact hashes,
worker completion/replay, API reload, idempotent writes, and stale-update rejection.
External owner failure retains the last pinned artifact with a blocked update.
A mechanical bracket and a motorized wired assembly use the same public SDK
facade; an out-of-scope motor mention does not add ElectricalIR in the advisory
selection fixture. Legacy projects retain format-driven compatibility.

## Pre-existing full-suite failures

These same identifiers fail in the clean baseline and feature worktrees:

- `test_owner_can_read_canonical_only_project_through_project_endpoint (tests.api.test_project_access.ProjectReadAccessTests.test_owner_can_read_canonical_only_project_through_project_endpoint)`
- `test_partial_default_generation_retry_appends_revision_and_reuses_run (tests.agents.test_generation_worker.GenerationWorkerIntegrationTests.test_partial_default_generation_retry_appends_revision_and_reuses_run)`
- `test_pipeline_step_observer_emits_core_metadata_events (tests.agents.test_pipeline.PipelineMetadataTests.test_pipeline_step_observer_emits_core_metadata_events)`
- `test_websocket_mcp_rejects_non_admin_user_context (tests.api.test_a2a_transport_auth.A2ATransportAuthTests.test_websocket_mcp_rejects_non_admin_user_context)`
- `test_short_mcp_api_key_falls_back_to_admin_auth (tests.api.test_auth_mode.AuthModeTests.test_short_mcp_api_key_falls_back_to_admin_auth)`
- `test_agent_suggestions_are_returned_and_persisted_with_the_assistant_turn (tests.projects.test_context_gathering.ContextGatheringIntegrationTests.test_agent_suggestions_are_returned_and_persisted_with_the_assistant_turn)`
- `test_proceed_dispatches_a_real_build_stage_when_a_dispatcher_is_available (tests.projects.test_context_gathering.ContextGatheringIntegrationTests.test_proceed_dispatches_a_real_build_stage_when_a_dispatcher_is_available)`
- `test_non_test_python_uses_only_the_config_environment_boundary (tests.tooling.test_config.AppConfigTests.test_non_test_python_uses_only_the_config_environment_boundary)`
- `test_root_requirements_avoid_local_paths_for_vercel (tests.tooling.test_core_package.CorePackageTests.test_root_requirements_avoid_local_paths_for_vercel)`
- `test_script_root_is_an_index_not_a_flat_executable_directory (tests.tooling.test_script_layout.ScriptLayoutTests.test_script_root_is_an_index_not_a_flat_executable_directory)`
- `test_concern_directories_are_discoverable_packages (tests.tooling.test_suite_layout.TestSuiteLayoutTests.test_concern_directories_are_discoverable_packages)`

## Unverified boundaries

No live provider classification/generation, hosted deployment, interactive
browser session, experimental chemistry, or physical qualification was run.
The report UI was type-checked and server-rendered; browser interaction is still
a review/deployment check. Existing chemistry evaluators check equations, not
reaction feasibility, kinetics, limiting reagent or yield. Existing HardwareIR
stage retry failures above are unchanged. ProcessIR 1.1 requires a compatible
reader for its new optional batch field. See [architecture and migration
boundaries](unified-generation.md#remaining-boundaries-and-migration-risks).

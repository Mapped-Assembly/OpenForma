# Unified engineering generation

## Audit baseline and root cause

Audited the default branch at `ed8fa32ef7f9dc9dbdb97070aa48418105f1635e` (2026-11-04), including issues #20, #21, #23 and #24. All seven native domain schemas were present. There was **no standalone runnable `EngineeringOrchestrator` class**: #23 described the intended control plane. `HardwarePipelineOrchestrator`, the domain generation functions, and the legacy project lifecycle were the implementation seams.

The architecture inconsistency was in the callers, not missing domain schemas. `build_generation_response` called `generate_project_with_workflow`, which always chose one of two HardwareIR pipelines. The standard CLI, frozen-brief workers, revision storage, and web response handler also assumed HardwareIR. The separate domain tools already implemented selection, private context, generation, revisioned composition, validators and portable persistence, but their callers had to connect these steps themselves.

| Entry point | Baseline | Unified behavior |
| --- | --- | --- |
| Web `/generate` → `apps/api/main.py` → `a2a.build_generation_response` | HardwareIR, component/wiring/BOM stages, hardware response view | New objectives use the shared domain control plane; result view shows selected domains, findings, unknowns and downloadable JSON |
| A2A / ordinary MCP `forma.generate_project` | Same legacy response builder | Same unified builder, no architecture-mode flag |
| Python `generate_project_with_workflow` | Always HardwareIR | Domain report by default; an explicitly supplied existing HardwareIR retains compatibility |
| `HardwarePipelineOrchestrator` | Legacy sequential generation only | Adds `generate_engineering`, using its configured provider and the existing domain functions; the hardware-named low-level method remains a compatibility adapter |
| `forma-core generate` | HardwareIR and legacy output persistence | Portable engineering report; `--request-file` accepts caller-authored offline inputs; explicit `--simulation` remains a legacy demonstration fixture |
| `forma-oss build` | HardwareIR | Empty/new project uses domains; existing format determines iteration/compatibility; local manifest preserves the report |
| `forma.opencode.domains` / `forma-core domains generate` | Separate authoring tools requiring plan/designs | `generate` also accepts an engineering objective or caller-authored continuation and runs the same control plane; old low-level operations remain available |
| Frozen DesignBrief worker | `HardwareIntermediateRepresentationGenerationEngine` | `EngineeringGenerationEngine` calls the shared entry point; stored HardwareIR revisions automatically retain the original engine |
| Canonical project revisions | HardwareIR-only state | Tagged engineering reports or legacy HardwareIR, using the same immutable, owner-scoped state service |
| Iteration | HardwareIR editor; separately managed domain updates | CLI dispatches by stored format; domain updates preserve untouched artifacts and diagnose stale links; API continuation can supply `existing`, `update_domains`, `contexts`, `designs` and `links` |
| Legacy import/export, compile, firmware, CAD and cloud CLI delivery | HardwareIR-specific consumers | Remain explicit compatibility consumers; domain JSON is rejected by legacy transport rather than silently truncated |

Duplicated orchestration responsibilities were selection, authoring context, validation, and packaging in the sequential hardware pipeline versus `domains/*`. The unified path extends `domains/generation.py`, reuses the one registry and selection-decision contract, and calls owning providers. It does not introduce an IR registry, universal engineering schema, or second orchestrator. `design_lifecycle.py` remains the derived geometry/visual lifecycle for legacy projects; domain identity/revision/hash tracking continues to use ProductBundle and its existing references.

## Flow and ownership

1. Accept a natural-language objective and optional explicit context/previous bundle.
2. Discover the existing registry, including external capability advertisements.
3. Obtain and validate a digest-bound `SelectionDecision`, with reasons. Scope describes requested work: mentioning a motor does not request wiring; chemistry does not request mechanical geometry; thermal analysis must actually be requested.
4. Extract context separately against each selected domain's declared requirements. Missing requirements block that domain; assumptions and other unknowns remain visible.
5. Generate each native artifact through `generate_product` and `provider_author`, or invoke an advertised external owner's author operation. External artifacts stay pinned references. SpaceIR remains owned by OpenIndustries.
6. Ask for typed cross-domain links against immutable artifacts, then run existing local and composition validators. Never silently refresh stale relationship pins during an update.
7. Invoke only advertised deterministic evaluators. Native adapters expose the already implemented chemical element/charge checks, process rate balances, and bounded thermal cases. Evaluator results carry their input reference, provider version and output hash; failure becomes unknown.
8. Return `forma.engineering-result`: existing ProductBundle, separate validation/evaluation reports, assumptions, unresolved information and provenance. `succeeded` describes completed generation, not physical qualification. Unsupported evaluator coverage remains explicit even when artifacts are valid.

The report is a workflow envelope, not an eighth IR. Native artifacts serialize independently. The canonical revision service stores the envelope without creating placeholder hardware. Domain revisions retain their provider-owned identity, revision, hash and provenance. New generated measurements are rejected; existing imported observations cannot be created by a generative claim.

## Public use

With an existing configured provider:

```python
from forma_core.generation import generate_project_with_workflow

result = generate_project_with_workflow(
    "default",
    "Design a chemical system using 2 g sodium bicarbonate and 50 mL of 5% vinegar. "
    "Represent reactants, products, process operations and material quantities; validate conservation.",
)
print(result.model_dump_json(indent=2))
```

The standard API accepts the same prompt at `POST /generate`. Existing provider/authentication configuration applies. Responses contain `engineering_result`; `project_ir` is reserved for legacy compatibility responses. Owner-scoped canonical revisions can be read through the existing project endpoint.

Offline, reproducible integration input:

```bash
python -m forma_core generate "Design the bicarbonate/vinegar chemical system" \
  --request-file examples/domains/chemistry-generation.json \
  --output /tmp/chemistry-result.json
```

The request file supplies its full objective, selection, contexts, designs, typed links and unknowns. It is an explicitly authored fixture, **not** a live LLM experiment. This example exits **1** and still writes a complete report because unresolved information is preserved. Equivalent operations are available through `forma-core domains generate` and `forma.opencode.domains` (`operation: generate`, JSON text payload). Agents can discover schemas and author the same inputs without server-side inference. Low-level plan/design compatibility remains supported.

## Chemistry benchmark

The conceptual reaction is:

`NaHCO3 + C2H4O2 → C2H3NaO2 + H2O + CO2`

ChemicalIR owns the species/reaction and their evidence. ProcessIR owns feed → reaction → gas/residue operations and directed material streams. `uses_reaction` and `uses_species` relationships pin the two artifacts; no mechanical, electrical, fluidic or thermal placeholder is generated.

ProcessIR **1.1** adds optional typed batch quantities (mass, amount, volume), because 2 g and 50 mL are inventories rather than rates. Version 1.0 remains accepted; absent batch fields serialize exactly as before, preserving old payloads/hashes. A batch quantity in a 1.0 document is rejected. Older readers must upgrade before reading 1.1.

The deterministic chemical evaluator verifies element and charge conservation. **No installed evaluator calculates molar mass, limiting reagent, theoretical CO2 yield, kinetics or reaction completion.** The approximately 1.05 g CO2 reference supplied with the benchmark is not returned as a computed or measured result. The 5% vinegar concentration basis is unspecified, so acid inventory is unknown. Duration, conversion, gas collection efficiency and actual yield remain unknown. No fictitious stream rates or measurements are substituted.

## Remaining boundaries and migration risks

- Natural-language classification and extraction use the configured structured provider. Offline tests replay deterministic provider responses; they do not measure a production model's accuracy. No live provider run is included in this change.
- Domain-native CAD/progressive imagery and web-research adapters are not integrated. Requests for them return explicit unresolved diagnostics. Existing hardware projects retain their implementations.
- Domain reports persist in canonical revisions and are downloadable/reloadable. They are not projected into the legacy public hardware gallery or its BOM/wiring views. Legacy cloud CLI delivery rejects the new format explicitly; use portable JSON or the generation API.
- Domain iteration uses explicit update scopes and preserves stale relationship diagnostics until the caller reauthors affected links. This is not an autonomous repair optimizer or a geometry rebinding solver.
- An external owner must advertise and implement authoring/resolution/evaluation capabilities to run them. Unavailable SpaceIR remains blocked; its schema is not vendored.
- Element balance, topology, unit checks and limited numerical cases are distinct from experimental verification, chemical viability, equipment safety and compliance. There is no new yield solver or compliance ruleset.
- Existing legacy stage-level retries and CAD lifecycle stay intact. Domain requests retain independent successes but do not claim the legacy stage checkpoint machinery supplies per-token resumable model calls.

See the [verification record](unified-generation-verification.md) for executed tests, baseline failures and the inspected benchmark output.

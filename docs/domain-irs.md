# Independent domain IRs

Issue #20 replaces HardwareIR as the required entry point for **new domain
authoring**. A product selects a subset of MechanicalIR, ElectricalIR,
BiologicalIR and FluidicIR. Each document has an artifact ID, revision, schema
version, serialization, authoring context and local validation. A product
manifest references those artifacts and separately validates their relationships.

This is an additive migration. The hosted legacy generator/editor and existing
`forma-core generate` command still consume HardwareIR 0.2. New consumers use
`forma_core.domains`, `forma-core domains`, or `forma.opencode.domains`. There is
no database flag-day migration and no requirement to instantiate HardwareIR
when authoring or validating an individual domain.

## Contracts and authority

| Contract / module | Authoritative facts | Current capability and boundary |
| --- | --- | --- |
| `MechanicalIR`, `forma_core.mechanics.models` | Dimensions, materials/geometry references, placements, mechanical CAD and motion intent | Reuses `MechanicalNotes`, solid operations and mechanism contracts. No new stress, tolerance or motion solver. |
| `ElectricalIR`, `forma_core.electronics.models` | Pins, electrical specifications, logical nets, buses, power rails and current estimate | Reuses existing electrical primitives and low-voltage circuit rules. This is the foundation for #17, not a second electronics model. No harness, PCB or certification claim. |
| `BiologicalIR`, `forma_core.biology.ir` | Experimental conditions, compartments, exposures, controls, endpoints, sampling, observations and evidence | Versioned envelope around the **existing** `BiologicalExperiment`. Its wire format and internal-reference validator are unchanged. No biological simulation added. |
| `FluidicIR`, `forma_core.fluidics.ir` | Fluid topology, channels, chambers, pumps, valves, membranes and supplied flow constraints | Versioned envelope around the **existing** `FluidicSystem`; uses its topology and calculation checks unchanged. |
| `ProductManifest` | Artifact identities, exact revisions/digests and cross-domain relationships | Contains references rather than domain semantics. One artifact per selected domain in v1. |
| Compatibility source | Shared procurement/BOM metadata, cross-disciplinary assembly instructions and other unmigrated project records | Preserved outside domain documents until consumers migrate. No guessed classification of mixed prose into mechanical versus electrical authority. |

`BiologicalIR.experiment` and `FluidicIR.system` are envelopes, not competing
versions of their existing models. Their envelope versions can evolve separately
from each other. Existing direct `BiologicalExperiment` and `FluidicSystem`
callers continue to work.

Mechanical and electrical primitive classes now live in their domain packages.
The old project-model imports re-export the **same classes**. Existing mechanical
source generators also retain their old import paths. Domain imports do not
load the legacy project package, database, providers, or unrelated IR schemas.

## Field and consumer inventory

Every current HardwareIR top-level field has an explicit migration disposition:

| HardwareIR field(s) | Domain authority or migration disposition |
| --- | --- |
| `hardware_ir_version` | Legacy schema marker retained in the compatibility source; each new IR owns `schema_version`. |
| `overview` | Product description retained in compatibility metadata. |
| `requirements` | Mixed legacy context retained; new domain authoring accepts separate typed context. No automatic splitting of ambiguous prose. |
| `system_architecture` | Legacy architecture retained; not used as a mandatory parent of the domain IRs. |
| `part_definitions[].part_definition_id`, `components[].ref_des` | Stable identities preserved on import. Each domain view has `object_id` separately from display `ref_des`; `part_definition_id` identifies the shared part. |
| `part_definitions[].pins`, `.electrical_specs` | ElectricalIR. |
| `part_definitions[].dimensions_mm` | MechanicalIR. |
| Remaining `part_definitions` fields | Procurement/identity metadata retained in compatibility source. Electrical name/part-number/category fields are descriptive projections for existing circuit rules; conflicting edits are rejected on export. |
| `components[].rationale`, `.configuration` | Shared selection/instance metadata retained in compatibility source; not guessed into either domain. |
| `bom` | Procurement aggregation retained once in the compatibility source. Existing instance-count/BOM invariants are rechecked on export. |
| `nets`, `buses`, `pin_mappings`, `power_rails`, `estimated_current_draw_ma` | ElectricalIR. Absent legacy current becomes `None`, not an invented zero. Existing free-text pin mappings remain free-text compatibility data. |
| `mechanical`, `cad_model` | MechanicalIR. |
| `fluidic_system` | FluidicIR.system. |
| `assembly` | Cross-disciplinary assembly procedure retained as product data, not assigned wholesale to mechanics. |
| `firmware` | Existing FirmwareTarget retained unchanged; a future firmware domain is outside this issue. |
| `constraints`, `fabrication_notes` | Mixed prose retained; callers supply domain-specific context explicitly. |
| `assembly_metadata`, `project_version_history` | Preserved compatibility/project records; new product files also retain immutable snapshots. |
| `validation`, `is_valid` | Original report preserved for exact round trips. Edited exports clear the stale report, set `is_valid=false`, and mark `recheck_required`. |
| Unknown source fields, including nested extensions | Original JSON retained and reported. An edited legacy export refuses fields that normalization cannot represent unless loss is explicitly acknowledged. |

| Consumer | Existing behavior | Migration in this change |
| --- | --- | --- |
| `agents/orchestrator.py`, `agents/pipeline.py`, `agents/workflows.py`, specialist prompts | HardwareIR generation with shared context | Preserved. `domains.agents.plan_with_agent` and `provider_author` use the existing provider method with separate scope/domain calls. |
| `validation.py`, `wiring.py` | Circuit validation and wiring compilation | Existing circuit rules moved to `electronics.rules` and re-exported; standalone electrical validation uses them without constructing HardwareIR. Existing wiring compilation remains compatible. |
| `workspaces/projects/models.py` | Legacy normalization, shared definitions and BOM validation | Retained as compatibility contract; mechanical/electrical leaf types are re-exported aliases. |
| `workspaces/projects/state.py`, persistence/database repositories | Hosted HardwareIR revisions | Existing records unchanged. Domain products have their own portable persistence with digest-checked writes and immutable revision snapshots. Hosted native-domain persistence is a subsequent migration. |
| `workspaces/projects/objects.py`, `context_governance.py` | Legacy namespaces and projected contexts | Remain compatible through explicit export. New generators receive only their domain context and declared cross-domain projections. |
| SDK / `generation.py` | Legacy generation facade | New public SDK: `domains.planning`, `domains.generation`, `domains.composition`, `domains.storage`, `domains.compatibility`. The old facade continues to work. |
| `forma_core/cli/main.py`, `forma_cli` | Existing core and hosted CLI commands | Additive `forma-core domains` command; hosted CLI remains on the compatibility format. |
| `apps/api/opencode_mcp.py`, `opencode/models.py` | Authenticated project authoring and compile tools | Add `forma.opencode.domains` for stateless plan/schema/generate/validate/import/export operations. Returned bundles can be stored locally; compatibility payloads can use the existing compile tool. |
| `workspaces/projects/cad_generation.py`, firmware exporters, `utils.py` | OpenCAD generation, firmware, wiring diagrams | Compatibility export preserves their existing input schema. |
| OpenIndustries `src/lib/form.ts` / `form-model.ts` | HardwareIR 0.1/0.2 imports and approximate placement/mesh geometry | Explicit HardwareIR export remains the downstream handoff. Native ProductBundle import is not claimed. |

## Selection and missing context

The available domain set is `I`; scope selection produces `S(P) ⊆ I`. Scope
describes **requested engineering work**. A motor appearing in a rendering does
not automatically request a wiring design. The agent reads the brief and returns
`ProductScope`; deterministic selection maps that scope to domain types and
reasons. Offline callers supply the scope directly. There is intentionally no
keyword-only fallback that silently guesses intent.

The four issue fixtures are in
[`examples/domains/selection-fixtures.json`](../examples/domains/selection-fixtures.json):

| Scope | Selected types |
| --- | --- |
| Passive bracket | MechanicalIR |
| Motorized assembly including wiring | MechanicalIR, ElectricalIR |
| Passive chip body, fluid network and biological experiment | MechanicalIR, FluidicIR, BiologicalIR |
| Same system including electrical pumping control | MechanicalIR, ElectricalIR, FluidicIR, BiologicalIR |

Each context includes its own `scope` and required inputs:

- Mechanical: geometric requirements, loads, manufacturing constraints.
- Electrical: supply, load and interface requirements.
- Biological: question, experimental conditions and evidence sources.
- Fluidic: fluid properties, operating conditions and flow requirements.

An author must explicitly state when a constraint is not applicable; missing
information remains missing. The context schemas reject unrelated fields.
Missing electrical specifications keep ElectricalIR **selected but blocked**.
Mechanical generation can complete and its validity remains independently
inspectable. Omitted or blocked domains produce no placeholder artifacts.

## Generation and updates

```python
from forma_core.domains.agents import plan_with_agent, provider_author
from forma_core.domains.generation import generate_product

# provider is an already-configured Forma structured provider.
plan = plan_with_agent(provider, "product-1", brief, contexts=domain_contexts)
product = generate_product(plan, provider_author(provider))
```

The scope selector receives the product brief. Each author receives only a
`GenerationRequest` containing its domain schema, relevant typed context,
requested identity/revision, previous **same-domain** document when updating,
and any declared cross-domain inputs. It does not receive the whole brief,
other contexts, other artifacts or legacy metadata by default.

`DeclaredInput` pins an upstream artifact revision and object/port, and names the
top-level domain field to project. This v1 projection is deliberately explicit
and coarse: requesting `parts` shares that field, not just one part. The source
must be generated and locally valid. Missing/stale dependencies block dependent
tasks; dependency cycles terminate with visible blocked tasks.

Pass `existing=product, update_domains={"mechanical"}` to update just mechanics.
Other artifacts retain their exact content and revision. Updated relationships
remain stale until the caller explicitly supplies rechecked links. Failed
updates retain the previous artifact with a failed/blocked plan status. Existing
artifacts cannot silently disappear when a new plan selects fewer domains.

## Identity, ownership and validation

A shared motor has the same `object_id` in mechanical and electrical views.
Display `ref_des` and immutable physical identity are separate fields. Its
mechanical view owns dimensions/placement; its electrical view owns pins and
connectivity. `same_object` links make the shared identity explicit and validate
matching part definitions. In imported projects, existing refdes become the
initial object IDs unless an exported identity mapping is present.

Link endpoints identify artifact, revision, object, optional port, units and
frame. Spatial links require explicit units and frames; incompatible frames or
units are diagnosed rather than silently transformed. `hosted_by` links connect
biological compartments to fluidic channels/chambers. The existing biological
`fluidic_component_ids` require corresponding revision-pinned composition links.

`validate_product` returns independent `local`, `composition`, and
`blocked_domains` sections. A broken host/object/port/revision link never changes
a valid MechanicalIR or BiologicalExperiment into a locally invalid document.
Manifest SHA-256 fingerprints detect artifacts changed without a matching
revision reference. `is_valid` means the declared checks passed and selected
work completed; it does not mean physical, manufacturing or safety qualification.

## Offline public workflow

```bash
python -m forma_core domains generate examples/domains/motor-generation.json --output /tmp/motor-product.json
python -m forma_core domains validate /tmp/motor-product.json
```

`motor-generation.json` supplies two separately authored domain designs and
contexts; it makes no model call and creates no unrelated IRs. To use real
authoring, supply a `DomainAuthor` callback or `provider_author` through the SDK.
The MCP `generate` operation accepts the same independently authored documents.

All CLI operations read JSON from a path or `-` and optionally write `--output`:

| Operation | Input |
| --- | --- |
| `schema` | `{"domain": "mechanical"}`; returns document and context schemas |
| `plan` | `{product_id, brief, scope, contexts}` |
| `generate` | `{plan, designs, existing?, inputs?, links?, update_domains?}` |
| `validate` | ProductBundle |
| `validate-domain` | One standalone domain document |
| `import-hardware` | `{product_id, hardware_ir}` |
| `export-hardware` | `{product, legacy_seed?, allow_lossy?}` |

Generation persists partial results but exits 1 if the composed product is not
valid. Export returns `{payload, findings}` and exits 1 if conversion is refused.
Use `payload`, not the outer report, with existing HardwareIR consumers.

For a new generated product, supply explicit procurement/BOM/assembly metadata
as `legacy_seed` when exporting component inventories. The motor fixture's seed
is `examples/domains/motor-hardware.json`. Missing part numbers/prices/roles are
not guessed by the adapter. Imported products carry their original source and
do not require a separate seed.

`save_product` writes portable JSON atomically and keeps content-addressed files
under `<output>.revisions/`. Replacing an existing product requires the current
canonical JSON digest (`--expected-sha256` in the CLI). A stale writer is refused.
Independent schemas also support direct `model_dump_json` / `model_validate_json`.

## Compatibility and verified scope

- Unchanged imports round-trip the original JSON data exactly, including 0.1
  quantities, unknown fields, firmware, metadata and history. Existing 0.1
  instance/BOM normalization is exposed in the report; original source survives.
- Changed exports update supported domain-owned fields, reject unresolved
  inventory/identity conflicts, and explicitly report unsupported material,
  geometry-reference, unknown-current and biological fields. `allow_lossy` never
  bypasses identity or composition failures. Biology remains available as its
  own artifact even if a caller explicitly requests a lossy legacy export.
- The new reference motor passes public generation, composition validation,
  persistence, CLI/MCP and compatibility export. Its BOM/net identities, wiring
  diagram and generated OpenCAD source match the legacy fixture. The composed
  product also passes native OpenCAD compilation and produces a real STEP file
  from its explicitly authored bracket solid.
- A manual downstream check used OpenIndustries commit
  `21c3aa4e60e980c506b0f2742d3606077ba4af39`: `readFormDocument` and `importForm`
  accepted the exported 0.2 document, preserved all three component IDs and the
  netlist, and produced the expected M1 placement envelope. This checks the
  actual import boundary, not a physical robot or fabrication-ready mesh.

Run regression coverage with:

```bash
python -m pytest tests/projects/test_domain_irs.py tests/projects/test_component_instances.py tests/projects/test_wiring_compiler.py tests/projects/test_biology.py tests/projects/test_fluidics.py tests/projects/test_cad_generation.py tests/opencode/test_mcp.py -q
```

Further migration work for #2/#17 can move hosted persistence, editors and
downstream importers onto these contracts incrementally. Those consumers must
not introduce a competing electrical/biological source of truth or silently
reinterpret retained mixed-domain metadata.

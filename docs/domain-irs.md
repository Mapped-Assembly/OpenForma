# Independent domain IRs

Issue #20 replaces HardwareIR as the required entry point for **new domain
authoring**. A product selects from a registry of independently owned domain
representations, including native MechanicalIR, ElectricalIR, BiologicalIR and
FluidicIR. Each native document has an artifact ID, revision, schema
version, serialization, authoring context and local validation. A product
manifest references those artifacts and separately validates their relationships.

New objectives through `/generate`, the Python SDK, CLI and generation workers
now use the [unified domain control plane](unified-generation.md). Existing
HardwareIR projects retain their compatibility adapters automatically. The
low-level `forma_core.domains`, `forma-core domains` and `forma.opencode.domains`
operations remain independently usable. No HardwareIR is required for a new
domain product. ChemicalIR, ProcessIR and ThermalIR join the original four domains.

## Ownership, providers and composition

| System | Responsibility |
| --- | --- |
| OpenForma | Product/subsystem design and its native engineering domain IRs |
| OpenIndustries | SpaceIR, scene compilation, visualization and operational runtime |
| Domain registry | Discoverable IR types, ownership and provider capabilities |
| Composition layer | Domain selection, artifact references, dependency resolution and cross-domain validation |

An **IR definition** owns its independently versioned schema and semantics. A
**domain provider** implements authoring, loading, local validation and identity
inspection for that definition. **Orchestration/composition** chooses which
representations are required and references their artifacts; it does not acquire
ownership of their schemas. A domain provider is distinct from the optional LLM
provider used by `selection_mode: "provider"`.

`providers.py` defines a schema-independent `DomainProvider` protocol and
`ProviderMetadata`. Metadata advertises the stable domain ID, IR owner,
provider ID/version, supported schema IDs/versions, authoring/validation/
resolution/discovery capabilities, required context, relationship types, fact
authorities, explicit domain dependencies, availability and limitations. There
is no universal document model that external IRs must inherit. External payloads
need not even use OpenForma's envelope field names: `describe()` extracts their
identity and `entities()` exposes stable entity/port identifiers.

`DomainRegistry` holds one explicitly configured provider per domain. Register
an adapter with `register(metadata, provider)`; replace an existing reservation
only with `replace=True`. Advertisements for absent providers are permitted when
marked unavailable. Schema discovery/selection reads metadata without importing
domain implementations. Native schemas continue to load lazily through the
existing adapters; their document wire formats are unchanged.

A provider can be a local package adapter or a proxy to an independently deployed
service. The interface passes JSON and references, not Python IR classes. This
change implements the registry/adapter boundary only: it does not implement a
network transport, dynamic package loading, remote credentials or service startup.
Registrations are trusted application configuration, not client-supplied code or
URLs. Composition never fetches a reference URL directly; only an explicitly
registered provider can resolve it.

### SpaceIR belongs to OpenIndustries

The default catalog reserves `space`, owned by **OpenIndustries**, with provider
ID `openindustries.space`, version `unregistered`, no advertised schemas and
unavailable status. This is an ownership reservation, not a SpaceIR definition.
`schema({"domain": "space"})` reports external-provider metadata and null document
and context schemas. OpenForma does not import OpenIndustries or copy its internals.

The future owner's responsibilities include coordinate frames/transforms,
regions/volumes, boundaries/openings, topology, placement/occupancy envelopes,
spatial constraints, source evidence and uncertainty. None of those semantics
are added to MechanicalIR by this change. Existing component placements remain
mechanical design facts; site topology belongs to SpaceIR.

An OpenIndustries provider can integrate by advertising its own schema IDs,
versions and context requirements, implementing the provider protocol (locally
or as a service proxy), then replacing the reservation in the application's
registry. No native MechanicalIR/ElectricalIR/BiologicalIR/FluidicIR schema needs
to change. Provider-hosted authoring returns a pinned `ArtifactReference`;
OpenForma's current generator accepts that reference instead of authoring the
foreign document. Native authoring continues through the existing callback or
supplied-design path. Accordingly, the native registry adapters advertise local
validation but not provider-hosted authoring or a remote artifact catalog.

### References across repositories

`ArtifactReference` preserves domain, provider identity/version, schema ID/version,
stable artifact ID, optional owner-defined revision, canonical-JSON SHA-256,
optional locator, and provenance. Revisions can be integers or opaque strings;
content-addressed external artifacts need no invented revision. Native legacy
references lacking the new provider/schema fields receive explicit native-adapter
defaults on read. External references must supply those fields.

Link endpoints pin entity/port IDs and a revision or digest, with applicable
units and named coordinate frames. Links retain relationship type, declared fact
authority and provenance. Artifact IDs should be globally stable, preferably
owner-namespaced. Providers verify the actual document identity; the manifest is
not a substitute for the owner's validation.

The manifest contains references. A portable bundle may carry independently
serialized native artifacts or an explicit external cache, but resolution never
copies foreign documents into the bundle automatically, and HardwareIR never
absorbs them. `compose` accepts native `documents` plus external `references` and
`links`. `validate-domain` accepts the original native payload or, for an external
IR, `{"reference": ..., "artifact": ...}`. A provider outage returns unresolved
status and `is_valid: null` for the latter; it is not evidence of invalid local
semantics. `validate` separately reports `local`, `composition` and
`blocked_domains`. Missing providers/artifacts, unsupported provider/schema
versions, digest drift and broken links are composition diagnostics. Native
local validity remains independently inspectable, including during native updates.

External artifacts cannot be silently exported through HardwareIR. A resolved,
valid external reference produces an `unrepresentable` finding and requires
`allow_lossy` for a native-only compatibility export. Unresolved dependencies or
broken composition cannot be bypassed with that flag. The original external
reference and any cached bytes remain intact in the product bundle.

### Registry-based selection

Given product/task `P`, requested scope `C` and advertised registry `I`, selection
computes `S(P,C) ⊆ I`. The six existing scope booleans remain supported and map
through registry metadata. `scope.domains` explicitly requests other registered
domains, including required but unavailable ones. Provider-declared required
dependencies are selected transitively when registered; absent dependencies are
reported explicitly. Unavailability never silently removes a selected domain.

Each task includes selection reason, required context declarations, missing
inputs, provider availability/capabilities, dependencies and unresolved dependency
messages. Unknown domain IDs require registration, including an unavailable
advertisement if the implementation is not installed. Unselected domains create
no placeholder documents.

`registry({})` exposes the catalog through SDK/CLI/MCP. The agent handoff adds
`registry_sha256`, binding decisions to the advertised capabilities. Submit it
with `request_sha256`; it is required when `scope.domains` is nonempty. Old
native-only decisions retain their original project digest behavior. Neither
digest is an authentication token. Application configuration can inject a
registry into `select_domains`, `generate_product`, `validate_product`,
`export_hardware_ir` and `call_domain_tool`.

For example, this deterministic public request selects mechanics and the
currently unavailable spatial domain without loading OpenIndustries:

```bash
python -m forma_core domains plan examples/domains/external-space-selection.json
```

The selecting caller sees blocked work, required context and the unavailable
SpaceIR provider. It can still author/validate mechanics independently. To
compose a future resolved artifact, pass the reference returned by its owning
provider to `compose` or `generate.references`; do not invent an external schema
or submit its fields through the native `designs` mapping.

### Limits of this extension

- One active provider and one artifact per domain per product; multiple-provider
  routing, provider negotiation and multi-artifact domains are deferred.
- JSON artifacts and canonical-JSON SHA-256; binary artifact adapters are deferred.
- External authoring/discovery is invoked by the owning caller/provider. Automatic
  orchestration of those operations, and generation-time field projections from
  foreign artifacts, are deferred. Such projections remain explicitly blocked.
- Provider-declared relationship types receive identity/availability checks.
  Existing `same_object`, `spatial` and biological `hosted_by` checks remain;
  arbitrary relationship semantics and frame-transform evaluation are deferred.
  Spatial checks still require explicit mm/m units and matching frames, reporting
  an unresolved transform rather than silently converting coordinates.
- No SpaceIR implementation, scene compiler, new simulator, remote plugin
  runtime or circular OpenForma/OpenIndustries dependency is introduced.

The lightweight provider in `tests/projects/test_external_domain_providers.py`
uses a deliberately unrelated JSON envelope. It is not a proposed SpaceIR schema.

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

The advertised domain set is `I`; scope selection produces `S(P,C) ⊆ I`. Scope
describes **requested engineering work**. A motor appearing in a rendering does
not automatically request a wiring design. The agent reads the brief and returns
`ProductScope`; deterministic selection maps that scope to domain types and
reasons. The selecting agent can be the calling ChatGPT, Codex, another MCP
client, a human or a program. OpenForma does not require a server-side LLM.
Callers can also supply scope directly. There is intentionally no
keyword-only fallback that silently guesses intent.

The four deterministic scope-mapping fixtures are in
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

## Agent-independent public workflow

For end-to-end external-agent generation, start with `generate` and only `{brief}`.
Follow the returned action/schema and submit its continuation to the same operation;
see [guided generation](external-agent-generation.md). The lower-level selection
exchange remains available below for callers managing individual steps.

The default `plan` operation accepts a project and returns a selection request
for **the calling agent**. It does not load provider configuration, call another
model or guess the selection. The same exchange works through Python, CLI and
`forma.opencode.domains` MCP:

1. Call `plan` with `{product_id, brief, contexts?}`.
2. Read the returned `project`, domain catalog, capability descriptions and
   `decision_schema`. Its status is `awaiting_selection`; this is not a plan yet.
3. Interpret the brief and call `plan` again with the returned project fields
   plus `decision: {request_sha256, scope, reasons}`. Decide all six booleans,
   and give a brief-grounded reason for each selected domain.
4. Use the resulting plan's tasks, reasons and missing inputs to gather context,
   discover each selected domain's `schema`, author its IR and call `generate`.
   Call `validate` on the returned bundle.

For example, prepare a bracket project:

```bash
printf '%s\n' '{"product_id":"bracket","brief":"Design a passive shelf bracket. No electronics."}' | python -m forma_core domains plan -
```

[`bracket-selection.json`](../examples/domains/bracket-selection.json) contains a
complete external-agent response to that exact request. Submit it with:

```bash
python -m forma_core domains plan examples/domains/bracket-selection.json
```

It selects only MechanicalIR and reports missing geometry, load and manufacturing
context. The request digest covers normalized identity, brief and contexts;
changed inputs reject a stale decision. This is a consistency check, not an
authentication token. The authenticated MCP route retains its existing access
controls. Decisions require real JSON booleans, all six capability fields, and
reasons for exactly the selected domains. No selected domains is valid for
software-only work. OpenForma validates decision structure, not whether an
agent's interpretation of the brief is correct.

SDK clients use the same stateless tool:

```python
from forma_core.domains.tools import call_domain_tool

request = call_domain_tool("plan", {"product_id": "product-1", "brief": brief})
# The calling agent authors decision according to request["decision_schema"].
plan = call_domain_tool("plan", {**request["project"], "decision": decision})
```

Existing `{product_id, brief, scope, contexts?}` callers retain the direct,
deterministic mapping path. There is no implicit fallback to another model.
An internal Forma provider is optional: explicitly pass
`selection_mode: "provider"` with optional `provider` and `model`. This mode
requires existing live credentials and rejects simulation; its errors recommend
the external-agent path when credentials are absent. Provider settings cannot
be combined with an external scope or decision.

The optional internal-provider benchmark contains eight descriptions, including
exclusions and software-only work. Expected domains are scored after selection
and never included in the prompt:

```bash
python -m forma_core.domains.benchmark examples/domains/selection-benchmark.json
```

This benchmark reports `blocked` with `inference_performed: false` without live
provider configuration. Recorded-provider tests verify that optional adapter;
external-agent tests exercise the actual intake, decision, generation and
validation exchange while forbidding provider calls. Neither is a general
accuracy measurement of an agent's interpretation of natural language.

```bash
python -m forma_core domains generate examples/domains/motor-generation.json --output /tmp/motor-product.json
python -m forma_core domains validate /tmp/motor-product.json
```

`motor-generation.json` supplies two separately authored domain designs and
contexts; it makes no model call and creates no unrelated IRs. A calling agent can
author its own documents and submit them through `generate` using CLI, SDK or MCP.
SDK users can also supply a `DomainAuthor` callback; `provider_author` is an
optional internal-provider adapter.

All CLI operations read JSON from a path or `-` and optionally write `--output`:

| Operation | Input |
| --- | --- |
| `schema` | `{"domain": "mechanical"}`; returns document and context schemas |
| `plan` | `{product_id, brief, contexts?}` returns an external selection request; add `decision` or explicit `scope` to complete it. Optional internal inference requires `selection_mode: "provider"` (plus optional `provider`, `model`). |
| `generate` | `{brief}` starts a guided external-agent turn; `{continuation, submission?}` advances/resumes it. Complete caller-authored requests and `{plan, designs, existing?, inputs?, links?, update_domains?}` remain supported. `execution: "provider"` explicitly enables internal inference for brief-based requests. |
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
python -m pytest tests/projects/test_domain_irs.py tests/projects/test_external_domain_providers.py tests/projects/test_component_instances.py tests/projects/test_wiring_compiler.py tests/projects/test_biology.py tests/projects/test_fluidics.py tests/projects/test_cad_generation.py tests/opencode/test_mcp.py -q
```

Further migration work for #2/#17 can move hosted persistence, editors and
downstream importers onto these contracts incrementally. Those consumers must
not introduce a competing electrical/biological source of truth or silently
reinterpret retained mixed-domain metadata.

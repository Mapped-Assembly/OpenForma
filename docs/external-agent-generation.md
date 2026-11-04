# Guided generation for external agents

Start with only an objective, then follow OpenForma's returned instructions.
No prepared design file, server-side LLM credentials, or repository source
inspection is required to discover the next action.

## Start here

Use the existing `forma.opencode.domains` MCP tool with `operation: "generate"`
and `payload` set to a JSON string containing only `brief`. Equivalent CLI:

```bash
python -m forma_core domains generate - --output turn.json <<'JSON'
{"brief":"Design a chemical system using 2 g of sodium bicarbonate and 50 mL of 5% vinegar to generate carbon dioxide. Represent the reactants, products, process operations, material quantities, and expected outcomes. Validate conservation and report unsupported calculations explicitly."}
JSON
```

Python callers use `forma_core.domains.tools.call_domain_tool("generate",
{"brief": objective})` or `forma_core.domains.interaction.advance_generation`.
The latter is also exported by `forma_core.generation`. Domain-only imports
do not load HardwareIR or model-provider configuration.

## Interaction contract

Responses have format `forma.generation-turn`, version 1.0:

| Field | Meaning |
| --- | --- |
| `status` | `awaiting_agent` or `completed`; completion is distinct from engineering validity |
| `next_action` | Instructions, relevant inputs, response JSON Schema, findings, and whether the action may be deferred |
| `continuation` | Portable checkpoint and checksum; retain unchanged |
| `submission_template` | Action ID, expected revision, retry key, and empty response object |
| `result` | Existing `forma.engineering-result` once orchestration completes |

Fill `submission_template.response` and call the same `generate` operation with
`{continuation, submission}`. Follow the returned response schema. To resume
without answering, send only `{continuation}`. No additional discovery calls
or private file access are required.

The existing orchestration requests selection, domain-specific context,
independent authoring, and composition as needed. OpenForma allocates IDs,
revisions and selection digests. The agent supplies semantic relationship and
entity/port references; OpenForma pins newly authored links to the exact artifact
revisions and hashes. Conflicting identities and stale pins are rejected.
Existing links in project iteration are never silently repinned.

Invalid schema content, unbalanced equations, invented measurements and bad
relationships return findings on the same action. Correct the response using the
new submission template; previous independent answers remain in the checkpoint.
Use `unknowns` for unresolved information and `missing` for absent required
context. If `can_defer` is true, replace `response` with `defer: "reason"` to
continue independent work without inventing inputs. Domain selection must be
supplied before selected work can be deferred.

The CLI exits 0 while awaiting an agent. At completion it exits 0 for a succeeded
engineering result or 1 for a partial/failed result, always writing the response.
Unsupported evaluations are not evidence of physical verification.

## Shared implementation and compatibility

`ExternalAgent` implements the existing `StructuredProvider` callback. It pauses
and replays `generate_engineering` using an answer journal. It owns no selection
algorithm, domain registry, IR or orchestration sequence. The configured-provider
path continues to call that same generation function.

The MCP/domain-tool `generate` operation defaults to this handoff for incomplete
natural-language input. `execution: "provider"` explicitly opts into the existing
internal model. Complete caller-authored requests and older `{plan, designs}`
requests remain supported. This transport default does not change hosted web
execution or existing HardwareIR projects.

External artifacts remain references to their owning providers. Completed owner
authoring calls are journaled across pauses; resolution and validation remain
with the owner. No SpaceIR schema is copied into OpenForma. The final result runs
existing local/cross-domain checks and available generation evaluator adapters.

## Checkpoints and limits

- Checkpoints are caller-owned JSON. Checksums detect damage, not malicious
  modification. Pending instructions, validation and evaluations are reconstructed
  on resume; caller-edited cached results are not trusted as verified outputs.
- Retry keys are idempotent against a checkpoint. Conflicting key reuse and mixed
  action/revision submissions are rejected. Changed registry capabilities require
  a new run and selection.
- Portable snapshots may fork. They are not globally serialized hosted sessions.
  Publishing must use the existing project's owner/revision guards. This operation
  does not write hosted projects.
- Continuations carry their answer history; large models increase payload size.
  The existing MCP input limit is 2,000,000 characters. This version does not add
  a hosted run store or automatically compact large checkpoints.
- Saved journals reuse completed owner authoring calls. Replaying an older
  checkpoint or losing a response after a remote call can repeat it. Owner
  authoring must be replay-safe; this is not distributed exactly-once execution.
- Validation/evaluation is recomputed on resume. A remote owner's availability
  or behavior may change. Failed/deferred work remains explicit; repairs are
  supplied by the external agent, without hidden calls to another model.

## Executed vinegar/baking-soda acceptance run

The calling agent began with only the objective through the public CLI. Each
response was authored after receiving its action and schema. During this authoring
sequence no existing design fixture or repository source was read, and no second
model connection was used. Six responses covered selection, ChemicalContext,
ProcessContext, ChemicalIR, ProcessIR and composition.

The [transcript](evidence/external-agent-vinegar-transcript.jsonl) records submissions
and returned actions/results. It is evidence produced by that run, not its input
fixture. The transport script only called `python -m forma_core domains generate -`
and echoed the returned continuation/template. The implementing assistant also
performed the run; this is not a blinded reliability evaluation of other agents.

Observed: ChemicalIR + ProcessIR only, balanced molecular equation, 2 g and 50 mL
typed batch feeds, six automatically pinned relationships, no local/composition
errors, no measured claims, and explicit unknowns. Interaction status completed;
engineering status partial because acidity basis and performance were unresolved.

The baseline includes the separate stoichiometry solver from #46. The existing
generation evaluator adapter does not yet invoke it with a quantitative request.
This change does not claim that separate integration or invent a 1.05 g yield.

Regression tests: `tests/projects/test_generation_interaction.py` covers handoff,
resume, schema/semantic repair, identity/pins, partial work, retries/staleness,
registry drift, untrusted cached results, external ownership, imports and CLI.

Verification executed on Python 3.12: the expanded domain CI command in
`.github/workflows/domain-irs.yml` passed **266 tests**, with **4 skips** and
**266 subtests passed**. This includes 16 new interaction tests and one new
authenticated HTTP MCP end-to-end test. Python compilation and `git diff --check`
passed. A full repository-wide suite and live hosted deployment were not run
for this change. The acceptance transcript contains the actual CLI execution;
the automated protocol tests use explicit test-agent responses.
After the final owner-result portability guard, the interaction and MCP suites
were rerun: **48 passed, 106 subtests passed**.

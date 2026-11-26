# Run a process through OpenForma

These two **JSON-only authored examples** use the same ProcessRuntime and existing
scientific evaluators. They do not import a benchmark-specific execution script.

| File | Native process | Scientific model |
| --- | --- | --- |
| `vinegar-bicarbonate-declarative.json` | Chemical batch preparation, mixing and observation | Existing stoichiometric bound |
| `water-mixing.json` | Thermal contact and observation | Existing adiabatic equilibrium |

Both files include the pinned ProcessIR, frozen empirical plan, step completion
bindings and an explicitly synthetic transcript. The adjacent `*-report.json`
files record completed virtual workflows with **zero physical trials**. A virtual
completion says the declared software workflow ran; it does not demonstrate a
physical transformation. Recipe timing is not a kinetic simulation.

```bash
python -m forma_core domains process-runtime examples/process_runtime/vinegar-bicarbonate-declarative.json --output /tmp/chemical-run.json
python -m forma_core domains process-runtime examples/process_runtime/water-mixing.json --output /tmp/thermal-run.json
python -m pytest tests/projects/test_process_runtime.py -q
```

For a manual run use the same `plan`, set `mode` to `manual`, choose a new run ID,
and supply actual operator acknowledgements and measured evidence. Start with an
empty `commands` array to inspect ready steps. **Do not relabel these synthetic
fixtures as measured data.** Every reading carries explicit evidence references;
lot observations include quantity, uncertainty and provenance. Preserve prior
transcripts and scientific plans rather than editing their history.

Machine execution uses the same plan plus `bindings[].machine`:
`machine_id`, `package_sha256`, `action_id`, and parameters. A host explicitly
supplies MachineProcessAdapter with its existing ControlClient; arbitrary device
URLs and physical replay are not accepted by the transcript tool. See the actual
MachineRuntime integration tests in `tests/projects/test_process_runtime.py`.

Agents should follow [AGENTS.md](../../AGENTS.md) and
[process-runtime.md](../../docs/process-runtime.md). OpenForma owns execution;
OpenCAD is optional geometry. No workcell-specific application code is required.

For visible motion, use [workcell/request.json](workcell/request.json) with
`action: simulate`. Its reusable host joins both OpenForma runtimes and exports
feedback/virtual inventory frames for the generic renderer. See
[virtual-workcells.md](../../docs/virtual-workcells.md) for the exact fidelity limits.

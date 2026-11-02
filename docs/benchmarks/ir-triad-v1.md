# IR-TRIAD-01 — ChemicalIR / ProcessIR / ThermalIR benchmark

Parent: [OpenForma #24](https://github.com/Mapped-Assembly/OpenForma/issues/24)
Related: [ChemicalIR #25](https://github.com/Mapped-Assembly/OpenForma/issues/25),
[ProcessIR #26](https://github.com/Mapped-Assembly/OpenForma/issues/26),
[ThermalIR #27](https://github.com/Mapped-Assembly/OpenForma/issues/27),
[Engineering Orchestrator #23](https://github.com/Mapped-Assembly/OpenForma/issues/23).

## Research question

Do the three newly implemented IRs preserve their **independent semantic
authority** and correctly distinguish **supported checks**, **unsupported
physics**, **unknown inputs**, **bad data**, and **stale cross-domain
references**?

This benchmark deliberately prioritizes the 2026-11-02 ChemicalIR,
ProcessIR and ThermalIR merge results over MechanicalIR/ElectricalIR and scene
rendering. The test is **executable** against actual OpenForma schemas and
public composition/validation functions, not a rendered demo or language-model
self-assessment.

## Run

From the OpenForma repository after installing its dependencies:

```bash
python -m forma_core.benchmarks.ir_triad_v1
python -m forma_core.benchmarks.ir_triad_v1 --output ir-triad-report.json
python -m pytest tests/projects/test_ir_triad_benchmark.py -q
```

The first command emits stable, sorted JSON to stdout. The second writes
the complete structured report with source-artifact SHA-256 digests, case
identifiers, expected and observed diagnostic states, domain-group summary,
and explicit non-evaluation statuses for physical performance.

The benchmark does not use proprietary Aircela data, an LLM, a network
request, a cloud simulator or a live hardware device. Inputs are synthetic
and independently parsed from checked-in fixtures.

## Actual test content

| Area | Main positive checks | Main negative / unknown controls |
| --- | --- | --- |
| ChemicalIR | H2O electrolysis and CO2→methanol element/charge balances | Changed coefficient fails, unknown charge, unsupported formula grammar, overallocated/incomplete mixture, missing measured source |
| ProcessIR | Typed air-to-fuel topology and a separate, explicitly specified irrigation mass balance | Numeric mismatch, missing rates, incompatible ports, legal recycle distinguished from task DAG, no inferred fuel yield after changing electricity availability |
| ThermalIR | One-zone 20 W→30°C and 10 W→25°C screening cases; reproducible input digests | Reactor heat remains UNKNOWN, no inferred electricity/chemical heat, unsupported conduction, missing heat duty, violated design maximum |
| Composition | Typed reaction/species references, reaction→thermal provenance, ProcessIR→ThermalIR duty port and local warning isolation | Changed reaction revision makes source links stale, wrong entity type fails, missing heat-source link fails, domain subset selection does not instantiate unrelated IRs |

The air-to-fuel *graph* includes capture, water electrolysis, methanol
synthesis and conceptual downstream conversion. The final conversion remains
**unspecified**. No synthetic stoichiometric equation is falsely converted
into a kinetic rate or gasoline yield. The process electricity scenario
parameter `0.6` is a dimensionless illustrative availability input, **not**
an assertion that production falls to 60%.

The nominal thermal heater is a **separate synthetic screening fixture**.
Its numerical resistance/heat-rate assumptions are not imported as actual
methanol reactor conditions.

## Interpretation / score

The score counts **diagnostic-contract cases meeting their expected
outcomes**. A result `UNKNOWN`, `FAIL` or `UNSUPPORTED` is correct when it
rejects an absent/incorrect input. The score is **not** physical-accuracy,
safety, chemical viability, greenhouse yield, process efficiency, or real-world
simulation coverage.

Inspect **every case's observed status** before claiming success. These
safety/unknown categories must remain visible in all reports:

- real_world_performance: **not evaluated**
- chemical_yield: **unknown**
- real_reactor_heat_duty: **unknown**
- physical_safety: **not evaluated**
- regulatory_compliance: **not evaluated**

## Limitations and follow-on engineering work

1. ChemicalIR currently validates only its supported formula grammar and
   explicitly supplied composition/reaction descriptions. It does not
   independently calculate properties, reactor conversion or kinetics.
2. ProcessIR currently validates structural flow graphs and certain
   **explicitly provided** rates/balance terms. It cannot infer an air-to-fuel
   mass balance from a graph with unspecified inlet/outlet rates. Full
   species-specific flowsheets and extent-linked molecular bookkeeping are
   candidates for a later issue.
3. ThermalIR's bounded heat screening is a one-zone lumped-R model. A
   chemical reaction ID plus a flowsheet does not supply reaction enthalpy,
   extent/rate, convection coefficients or safe operating temperature.
4. Composition detects stale references but has no automatic solver for
   reauthoring invalid source inputs.
5. This benchmark does **not** establish BRep face bindings, OpenCAD geometry
   quality, OpenIndustries spatial scene accuracy, real compliance results, or
   a human-authorized physical operation. Those remain separately owned.

The benchmark should be treated as the **first vertical slice of epic #24's
integration milestone**, not the entire milestone's definition of done.

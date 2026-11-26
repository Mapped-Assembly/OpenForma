# Case study: declarative thermal mixing

This case verifies that a nonchemical process can use the same empirical workflow
without ChemicalIR, a stoichiometry request or experiment-specific code. The manual
experiment needs water, cups, a scale, a thermometer, a spoon and a timer.
**The reported results are synthetic software benchmarks; no physical trial was run.**

## Native declaration and model

ThermalIR declares three bodies:

| Body | Initial temperature | Heat capacity definition |
| --- | --- | --- |
| Warm water | 40 °C | 100 g × assumed 4.18 J/(g·K) = 418 J/K |
| Cool water | 20 °C | 100 g × assumed 4.18 J/(g·K) = 418 J/K |
| Receiving cup/spoon | 20 °C | Assumed effective capacity 20 J/K |

The ProcessIR declares preparation, thermal contact/mixing and measurement steps.
A named `adiabatic-equilibrium@1.0` invocation references the exact ThermalIR artifact.
The acceptance criterion references its `final_temperature` output and specifies
±1 °C tolerance. Input obligations resolve masses and initial temperatures directly
from the pinned ThermalIR quantities. Changing mass automatically changes `C=m*c`.

For explicitly declared constant capacities and negligible external heat transfer:

\[
T_f=\frac{\sum_i C_iT_i}{\sum_i C_i},\qquad
\Delta E_i=C_i(T_f-T_i),\qquad \sum_i\Delta E_i=0.
\]

For the nominal declaration:

\[
T_f=\frac{418(40)+418(20)+20(20)}{418+418+20}
=\frac{3185}{107}\approx29.766355\ ^\circ\mathrm C.
\]

The cup absorbs energy; omitting it would predict 30 °C. Its 20 J/K capacity is an
explicit assumption, not measured calibration. Include additional relevant bodies,
use justified properties or revise the assumption before making stronger claims.
The solver does not infer water properties from names and contains no water/cup IDs.
It also handles other explicitly declared constant-capacity bodies within its scope.

## Reproducible software benchmark

Files:

- `forma_core/experiments/water-mixing.json`: full native-artifact/protocol declaration.
- `examples/experiments/water-mixing-suite.json`: input edits, independent expected
  values, synthetic readings and expected comparison statuses.
- `examples/experiments/run_benchmark.py`: reusable suite runner through public APIs.
- `examples/experiments/water-mixing-benchmark-report.json`: generated deterministic
  receipts, exact quantities, discrepancies, plan hashes and explicit evidence kind.

```bash
python -m examples.experiments.run_benchmark \
  examples/experiments/water-mixing-suite.json \
  --output /tmp/water-mixing-benchmark-report.json
```

**Six of six software benchmark cases passed their expected assertions.**

| Case | Predicted temperature | Synthetic reading | Expected comparison |
| --- | ---: | ---: | --- |
| Equal 100 g portions, 20 J/K vessel | 29.766355 °C | 29.77 °C ±0.1 °C | Pass |
| 200 g warm + 100 g cool | 33.124019 °C | 33.12 °C ±0.1 °C | Pass |
| 50 g warm + 150 g cool | 24.883178 °C | 24.88 °C ±0.1 °C | Pass |
| Equal portions, 100 J/K vessel | 28.931624 °C | 28.93 °C ±0.1 °C | Pass |
| Deliberate discrepancy | 29.766355 °C | 25 °C ±0.1 °C | Fail |
| Unknown instrument uncertainty | 29.766355 °C | 29.77 °C, uncertainty unknown | Unknown |

All six report `physical_status: not_run` and physical coverage zero. “6/6 passed”
means the software matched independent arithmetic and expected outcomes, including
correct rejection/inconclusiveness. It does not mean six experiments succeeded.

Additional tests cover renamed IDs, different unit declarations, exact energy
conservation, unknown properties, unsupported physics/model versions, tampering,
missing/mismatched actual inputs, historical revisions, external evaluator dispatch,
REST/MCP round trips and Celsius/Kelvin conversion without offsetting uncertainty.

## Manual trial after preregistration

Use the native protocol in the declaration. Prepare nominally 100 g of water near
40 °C and 100 g near 20 °C. Use warm, not boiling, water on a stable surface. Record
actual starting temperatures, masses and container description. If inputs differ
beyond the preregistered tolerances, update and freeze a new declaration before mixing.

Mix and stir gently in the receiving vessel. The protocol asks for a well-mixed
reading at 60 ±5 seconds, with instrument resolution, uncertainty and deviations
recorded. **The model predicts an equilibrium endpoint, not that equilibrium is
reached at 60 seconds.** Inadequate mixing, probe lag, loss to the room, spills or
missing thermal bodies must be recorded as unmet assumptions. Small quantities
and simple equipment reduce setup burden but do not remove these model limitations.

Observed disagreement can motivate a revised capacity or heat-loss model; it must
not overwrite the old prediction. Zero modeled energy residual is only mathematical
conservation within the declared system. Measured energy closure and industrial
applicability remain unknown.

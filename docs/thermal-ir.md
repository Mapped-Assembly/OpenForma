# ThermalIR v1 — independent thermal engineering representation

Issue [#27](https://github.com/Mapped-Assembly/OpenForma/issues/27) ·
[Seven-domain epic #24](https://github.com/Mapped-Assembly/OpenForma/issues/24)

ThermalIR owns **thermal nodes, heat-transfer edges, heat-rate source/sinks,
boundary conditions, property records and conservative thermal analyses**.
It does not own structure/CAD, circuits, reaction chemistry, process
operations, hydraulic flow, OI scene/runtime state, or compliance decisions.

The implementation uses the established `forma_core.domains` registry,
`DomainDocument`, `ArtifactReference`, `ProductManifest`, `DomainLink`,
Python SDK, `forma_core domains` CLI and `forma.opencode.domains` MCP bridge.
There is no new megaschema, HardwareIR field or thermal-specific MCP service.

## Public CLI examples

```bash
python -m forma_core domains validate-domain examples/domains/thermal-heater.json
python -m forma_core domains validate-domain examples/domains/thermal-reactor.json
python -m forma_core domains validate-domain examples/domains/thermal-greenhouse.json
python -m pytest tests/projects/test_thermal_ir.py -q
```

For schema discovery, pass a JSON file containing `{"domain":"thermal"}`
to `python -m forma_core domains schema path/to/file.json`. For native
registry discovery, pass a file containing `{}` to the `registry` operation.

## Separation of design and evaluation

The document contains independent versioned information:
- Thermal nodes and stable heat ports, including optional lumped capacity,
  initial temperature, target temperature and design-maximum temperature.
  A bounded prediction exceeding the specified maximum is a failed *design*
  check, not a legal, regulatory or safety verdict.
- Heat-transfer edges: `lumped`, `conduction`, `convection` or `radiation`.
  Edges may carry a declared effective K/W resistance or W/K conductance.
- Fixed-temperature, prescribed-heat, or adiabatic boundaries.
- Signed-by-role heat loads with nonnegative magnitude in W/kW.
- Material/thermal properties, declared temperature applicability intervals,
  explicit evidence and provenance, and immutable cross-domain source references.
- Optional `SteadyStateCase` analysis **requests**.

Local schema validity is independent of analysis coverage. The optional
`lumped-resistance` evaluator (version `1.0`) **only** handles a
single well-mixed zone connected to a single fixed-temperature boundary through
exactly one `lumped` resistance/conductance. Unknown heat inputs, ambient or
resistance return `unknown`; other transfer mechanisms and multiple paths
return `unsupported`. A failed physical input may return `fail`.

This is *screening*, not a numerical approximation to arbitrary CAD geometry,
CFD, FEA, measured heat transfer or a certified safety result.

## Units and thermal balance

Quantity dimensions are strict: absolute temperature uses K/degC, differential
temperature uses K/delta_degC, thermal resistance K/W, conductance W/K,
heat rate W/kW, heat capacity J/K, and so forth. Absolute °C is **not** a
differential quantity and no temperature below 0 K is allowed.

The supported steady-state convention is:

```
Q_net = Q_sources - Q_sinks
Q_transfer = (T_zone - T_ambient) / R_effective
residual = Q_net - Q_transfer
T_zone = T_ambient + R_effective * Q_net
```

All relevant declared values are included in a SHA-256 input digest on the
result. The result also records the evaluator version, residual in watts,
ambient/zone temperatures, status and limitations. A change to an unrelated
product-domain artifact does not silently rewrite the ThermalIR document.
Changing an input used by a case changes its own analysis digest. Downstream
orchestrators must use that digest and composition pinning to invalidate
affected cached evaluations.

### Reproducible example

The [heater fixture](../examples/domains/thermal-heater.json) explicitly states:
- 20 W source, 20 °C ambient, effective thermal resistance 0.5 K/W.
- Predicted one-zone screening temperature **30 °C**.
- Replacing 20 W with 10 W predicts **25 °C**.
- These are synthetic declarations, **not derived from equipment ratings**.

[Conceptual methanol reactor](../examples/domains/thermal-reactor.json)
has **unknown** reaction heat. The existence of a reaction in ChemicalIR or a
vessel in OpenCAD cannot determine rate/extent, reaction enthalpy or heat duty.
To provide a numerical reactor load, specify an independently justified
calculation model plus both a pinned ChemicalIR reaction and ProcessIR extent/
rate source. Such a declared load remains synthetic unless its evidence
actually supports the conditions and claimed fidelity.

[Greenhouse envelope](../examples/domains/thermal-greenhouse.json)
uses a conduction edge without a supported simplified model and therefore
reports `unsupported`. This demonstrates domain independence from
[OpenIndustries #93](https://github.com/Mapped-Assembly/OpenIndustries/issues/93).
It is not a simulated greenhouse microclimate.

## Cross-domain binding contract

- `uses_thermal_interface`: ProcessIR energy port → ThermalIR heat port;
  composition checks direction, port existence and kind.
- `thermal_source`: ThermalIR heat-load entity → a pinned reaction, process
  operation, mechanical equipment occurrence, or electrical/fluidic port.
  The load records the external `domain`, `artifact_id`, entity/port ID,
  schema_version, revision and SHA-256 digest. Composition requires a matching
  explicit `thermal_source` link for **each** pinned input and verifies both
  ends, entity type where supported and exact pinning; it never reparses chemistry
  inside thermal-local validation.
- ThermalIR fact authority is qualified (`thermal.network`,
  `thermal.heat_transfer`, `thermal.heat_duty`, `thermal.temperature`).
  Reaction equations remain ChemicalIR-owned; process unit operations remain
  ProcessIR-owned; electrical dissipation remains evidence-dependent and
  cannot be inferred directly from power supply ratings.
- An unavailable domain, changed revision or unresolved source produces a
  **composition finding**, not an invalid local ThermalIR.

## Limits and non-goals

No automatic convection correlations, radiation solver, wall-property
inference, transient heat diffusion, full energy simulation, reactor design
certification, electrical fire-risk approval, greenhouse safety qualification
or operational actuator control. Compliance rules and authorization are
separately versioned independent checks.

Legacy HardwareIR cannot represent ThermalIR. Exporting changed thermal
data through the legacy format raises an explicit `unrepresentable` finding;
loss requires caller acknowledgment, and the original independent ThermalIR
artifact remains in the product bundle.

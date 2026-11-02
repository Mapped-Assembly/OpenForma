# ChemicalIR v1 (OpenForma)

ChemicalIR is an **independent chemical design representation**, shipped as part
of [issue #25](https://github.com/Mapped-Assembly/OpenForma/issues/25).
It owns chemical species, mixtures, reaction equations, condition declarations,
model references and source evidence. It does **not** claim a chemical solver,
reactor performance, operating safety, certification or the ability to produce
gasoline.

The contract lives in `forma_core.chemistry.models`; local semantics and
element/charge balancing live in `forma_core.chemistry.validation`. The generic
`forma_core.domains` registry exposes `chemical` / `ChemicalIR` 1.0 via
Python, CLI and the existing MCP domain entrypoint. No HardwareIR is needed to
author or validate a chemical artifact.

## Run the synthetic fixture

From a local OpenForma checkout with its Python dependencies installed:

```bash
python -m forma_core domains schema <(printf '{"domain":"chemical"}')
python -m forma_core domains validate-domain examples/domains/chemical-air-to-fuel.json
python -m pytest tests/projects/test_chemical_ir.py -q
```

The process-substitution form `<(...) ` requires a POSIX shell; on Windows,
write `{"domain":"chemical"}` to a small JSON file and pass that file instead.

[The illustrative air-to-fuel fixture](../examples/domains/chemical-air-to-fuel.json)
includes exactly balanced reaction equations:

- `2 H2O -> 2 H2 + O2`
- `CO2 + 3 H2 -> CH3OH + H2O`

The fixture does **not** include kinetics, concentrations, conversions, energy
consumption, greenhouse-gas performance, catalysts, reactor construction details,
or an asserted methanol-to-gasoline conversion. It is not Aircela's proprietary
process model.

## Chemical domain boundaries

| Domain | Owns |
| --- | --- |
| ChemicalIR | Species identity, reaction definitions, mixture chemical composition |
| FluidicIR | Pipe/channel topology, hydraulic constraints and flow network |
| MechanicalIR | Equipment geometry, structure and material of the physical parts |
| ElectricalIR | Electrical nets, supply and power definitions |
| ProcessIR (planned) | Unit operations, process streams and sequencing; references reactions |
| ThermalIR (planned) | Heat-transfer networks and thermal-duty design |
| OpenIndustries | Spatial scenes, operational simulation, resource usage and observations |
| External compliance provider | Applicable independently versioned requirements and findings |

Use `ArtifactReference` / `DomainLink` with exact revision or digest to
refer to ChemicalIR entities. `uses_species` must identify a species and
`uses_reaction` must identify a reaction. Other domains/providers must
explicitly advertise the same relationship type before composition can
validate its use. A link is a declaration, not a chemistry simulation.

## V1 semantics and limitations

- The chemical formula parser handles ordinary element symbols, positive
  integer counts, and nested parentheses (e.g. `H2O`, `Ca(OH)2`).
  It does **not** parse dot hydrates, isotopes, isotopic labeling, formal charge
  embedded in formula text, coordination brackets, polymers, variable formulae,
  or organic structure encodings. An unsupported formula produces an
  `reaction_balance_unsupported` diagnostic, not `pass`.
- The integer ionic charge belongs to `Species.charge`; omit it only when
  unknown. Balancing uses exact rational stoichiometric coefficients encoded as
  strings. An unknown charge yields a separate `unknown` balance status.
- `check_reaction_balance` yields exactly one of `pass`, `fail`,
  `unknown` and `unsupported`. Structural Pydantic/reference validation
  and `validate_chemical` findings are independent of other domain checks.
  Balance passing is **not** evidence of chemical feasibility or reaction rate.
- Mixture fractions use dimensionless `unit: "1"`; concentrations use
  `unit: "mol/L"`. Fractions over unity fail. Fractions below unity are
  retained and emit `composition_incomplete` (an error if `complete=true`).
  Neither fractions nor quantities are silently normalized or invented.
- V1 conditions accept explicitly dimensioned `temperature` (K, degC),
  `pressure` (Pa, kPa, bar), `ph` (1) and `concentration` (mol/L).
  Unsupported units and impossible absolute conditions produce findings.
- Evidence has distinct publication/revision, retrieval and effective dates.
  External specified/measured sources require publisher, exact locator and
  retrieval timestamp, plus publication date or an explicit undated reason.
  Assumed/illustrative values must say why. Claim references resolve to exact
  evidence IDs; unsupported chemical properties remain unknown.

Legacy HardwareIR is still needed by some older UI/OpenCAD consumers. An edited
ChemicalIR is **not representable** through HardwareIR export; the adapter
reports an explicit `unrepresentable` finding and only permits acknowledged
lossy export when requested. ChemicalIR remains in the independent product
bundle; it is never silently inserted into legacy hardware fields.

This scope establishes independently usable **design semantics and
conservative validation**, not pressure-vessel certification, chemical
safety review, predictive chemistry or autonomous physical execution.

# ChemicalIR v1 (OpenForma)

ChemicalIR is an **independent chemical design representation**, shipped as part
of [issue #25](https://github.com/Mapped-Assembly/OpenForma/issues/25).
It owns chemical species, mixtures, reaction equations, condition declarations,
model references and source evidence. A separate, bounded stoichiometric
evaluator can derive theoretical batch quantities from declared inputs (see
below). Neither the IR nor that calculation establishes actual reactor
performance, operating safety, certification or the ability to produce gasoline.

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

## Single-reaction stoichiometric bounds

Issue #41 adds `forma_core.chemistry.stoichiometry.evaluate_stoichiometry`.
This pure Python evaluator takes an existing `ChemicalIR` plus a typed
`StoichiometryRequest`; it does not change ChemicalIR 1.0 or require any other
domain. Public orchestration/CLI/MCP integration and the portable case-study
workflow are tracked separately in #42.

```python
import json
from pathlib import Path

from forma_core.chemistry.models import ChemicalIR
from forma_core.chemistry.stoichiometry import (
    FeedAmount, StoichiometryRequest, evaluate_stoichiometry,
)
from forma_core.domains.base import fingerprint
from forma_core.domains.providers import ArtifactReference

document = ChemicalIR.model_validate(json.loads(
    Path("examples/domains/chemical-air-to-fuel.json").read_text()
))
reaction = document.reactions[0]
request = StoichiometryRequest(
    chemical=ArtifactReference(
        artifact_id=document.artifact_id, domain="chemical", schema_version="1.0",
        revision=document.revision, sha256=fingerprint(document),
    ),
    reaction_id=reaction.reaction_id,
    # The selected fixture reaction is 2 H2O -> 2 H2 + O2.
    inventory=[FeedAmount(
        species_id=reaction.reactants[0].species_id, value=3, unit="mol",
        claim={"basis": "illustrative", "rationale": "Synthetic batch for accounting only."},
    )],
    inventory_policy="zero_unlisted_nonreactants",
)
result = evaluate_stoichiometry(document, request)
print(result.model_dump_json(indent=2))
# maximum_extent_mol == "3/2"; newly formed H2 == "3" mol, O2 == "3/2" mol.
# No actual conversion, energy demand or reaction rate is predicted.
```

For net coefficients `nu_i` (products minus reactants), the maximum forward
extent is `min(n_i / -nu_i)` over net consumed species. Net species change is
`nu_i * extent`. Scaling every coefficient changes extent's normalization, but
does not change product quantities or remaining inventory. A species appearing
on both sides is accounted for by its **net** coefficient.

### Inputs and completeness

- Supported batch units: `mol`, `mmol`, `kmol`, `g`, `mg`, `kg`. Rates,
  concentrations and volumes cannot be silently treated as batch amounts.
- Supply `MolarMass` entries in `g/mol` or `kg/mol` for any required mass-to-mole
  conversion. Each property requires a declared claim basis and a source or
  rationale; no atomic-weight lookup or property inference occurs. Claim source
  IDs resolve against ChemicalIR evidence plus request evidence. Conflicting
  records under the same ID fail explicitly.
- The default `inventory_policy="explicit"` requires every selected reaction
  participant and declared catalyst to have an initial amount, including zero
  initial products. `zero_unlisted_nonreactants` explicitly assumes zero for
  omitted nonconsumed participants, recording each assumption in the result.
  **An omitted net consumed reactant is always unknown, never zero.** Unrelated
  species in the ChemicalIR are outside the batch unless explicitly supplied.
- Initial product inventory is retained separately from newly formed product.
  Supplied inert, solvent and catalyst quantities remain unchanged. Nonreacting
  roles with nonzero net coefficients produce a conflict. The presence or
  absence of a catalyst is not an assessment of kinetic feasibility.
- A reversible equation may be evaluated only as the explicitly forward
  stoichiometric bound. Equilibrium, reverse, network and continuous-flow modes
  return `unsupported`; other reactions in the document are not auto-executed.

### Results, precision and findings

Inputs follow existing finite float quantity conventions; arithmetic converts
their decimal string values to exact rational numbers. Outputs use integer or
rational strings (`"2"`, `"3/2"`) with explicit units, following the existing
chemical balance convention. Consumers can use `fractions.Fraction` to retain
exactness. There is no rounding or clipping of residual inventory.

Co-limiting species are those whose exact final residue is at most
`limiting_absolute_tolerance_mol + limiting_relative_tolerance * initial_mol`.
This tolerance affects classification only; it does not change the minimum
extent or quantities. Set both tolerances to zero for exact limiting ties.

The receipt includes the full request, evaluated artifact reference, canonical
input digest, model/schema versions, merged evidence, explicit assumptions,
per-species net accounting and structured `Finding` records. Preserve the
referenced ChemicalIR alongside the request to reproduce the calculation.
Revision or content-digest mismatches fail before evaluation. Schema errors
(including negative/nonfinite quantities and duplicate inputs) are structured
Pydantic `ValidationError` records; semantic failures use receipt findings.

The existing exact reaction-balance checker gates evaluation: unbalanced
equations fail; missing formula/charge stays unknown; unsupported formula
notation stays unsupported. Successful calculations report exact element and
charge residuals for **net changes**. Mass conservation of those changes is
checked when all net reacting species have molar masses, using
`mass_balance_tolerance_g` (default `1e-9 g`). Supplied incompatible masses can
fail that check even when atom conservation passes. Missing masses leave mass
coverage `unknown` while valid mole-only results remain available. Per-species
mass outputs are included wherever the corresponding property is supplied.

`pass` means the requested stoichiometric calculation succeeded; inspect the
separate conservation statuses and findings for incomplete property coverage.
It never means actual conversion, isolated yield, rate, feasibility or physical
experimental validation. Supplied conversion/recovery and heat-duty coupling
remain in #39; kinetics and observation comparison are separate milestones.

Run the deterministic solver and existing ChemicalIR regression tests:

```bash
python -m pytest tests/projects/test_stoichiometry.py tests/projects/test_chemical_ir.py -q
```

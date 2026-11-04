"""Independent arithmetic and failure cases for single-reaction batch bounds."""

from __future__ import annotations

from fractions import Fraction
import json
import subprocess
import sys
import unittest

from pydantic import ValidationError

from forma_core.chemistry.models import ChemicalIR, EvidenceRecord
from forma_core.chemistry.stoichiometry import (
    FeedAmount, MolarMass, StoichiometryRequest, StoichiometryResult, evaluate_stoichiometry,
)
from forma_core.domains.base import fingerprint
from forma_core.domains.providers import ArtifactReference


def document() -> ChemicalIR:
    """Return a synthetic balanced bicarbonate/acid accounting fixture."""
    return ChemicalIR.model_validate({
        "artifact_id": "synthetic-acid-bicarbonate", "revision": 1,
        "species": [
            {"species_id": key, "name": key, "formula": formula, "charge": 0}
            for key, formula in [("acid", "C2H4O2"), ("base", "NaHCO3"),
                                 ("salt", "C2H3NaO2"), ("water", "H2O"),
                                 ("gas", "CO2"), ("inert", "He")]
        ],
        "reactions": [{
            "reaction_id": "neutralize",
            "reactants": [{"species_id": key, "coefficient": "1"} for key in ["acid", "base"]],
            "products": [{"species_id": key, "coefficient": "1"} for key in ["salt", "water", "gas"]],
        }],
    })


def request_for(doc: ChemicalIR, acid: float = 2, base: float = 3) -> StoichiometryRequest:
    """Pin a batch with declared zero initial nonreactants and known feeds."""
    return StoichiometryRequest(
        chemical=ArtifactReference(artifact_id=doc.artifact_id, domain="chemical", schema_version="1.0",
                                   revision=doc.revision, sha256=fingerprint(doc)),
        reaction_id="neutralize", inventory_policy="zero_unlisted_nonreactants",
        inventory=[FeedAmount(species_id="acid", value=acid, unit="mol"),
                   FeedAmount(species_id="base", value=base, unit="mol")],
    )


def properties() -> list[MolarMass]:
    """Use explicitly illustrative integer masses from one consistent basis."""
    return [MolarMass(species_id=key, value=value, unit="g/mol",
                      claim={"basis": "illustrative", "rationale": "Synthetic integer atomic-mass basis."})
            for key, value in {"acid": 60, "base": 84, "salt": 82, "water": 18, "gas": 44}.items()]


class StoichiometryTests(unittest.TestCase):
    """Bounds, conservation, provenance and unknowns remain distinguishable."""

    def test_independent_expected_quantities(self) -> None:
        """One mole of each reactant forms one mole of each of three products."""
        for acid, base, extent, limiting in [(2, 3, 2, ["acid"]), (4, 1, 1, ["base"]),
                                            (2, 2, 2, ["acid", "base"]), (0, 3, 0, ["acid"]),
                                            (0, 0, 0, ["acid", "base"])]:
            with self.subTest(acid=acid, base=base):
                doc = document()
                result = evaluate_stoichiometry(doc, request_for(doc, acid, base))
                self.assertEqual("pass", result.status)
                self.assertEqual(str(extent), result.maximum_extent_mol)
                self.assertEqual(limiting, result.limiting_species_ids)
                amounts = {item.species_id: item for item in result.amounts}
                self.assertEqual(str(acid - extent), amounts["acid"].final)
                self.assertEqual(str(base - extent), amounts["base"].final)
                for key in ["salt", "water", "gas"]:
                    self.assertEqual(str(extent), amounts[key].formed)
                self.assertTrue(all(value == "0" for value in result.conservation.element_residuals_mol.values()))
                self.assertEqual("0", result.conservation.charge_residual_mol)
                self.assertEqual("unknown", result.conservation.mass_status)

    def test_preexisting_product_and_nonreacting_inventory(self) -> None:
        """Added water and inert helium survive separately from new formation."""
        doc = document()
        request = request_for(doc)
        request.inventory += [FeedAmount(species_id="water", value=10, unit="mol"),
                              FeedAmount(species_id="inert", value=4, unit="mol", role="inert")]
        result = evaluate_stoichiometry(doc, request)
        amounts = {item.species_id: item for item in result.amounts}
        self.assertEqual(("10", "2", "12"), (amounts["water"].initial, amounts["water"].formed, amounts["water"].final))
        self.assertEqual(("4", "0", "0"), (amounts["inert"].final, amounts["inert"].consumed, amounts["inert"].formed))

    def test_coefficients_and_net_catalyst(self) -> None:
        """Scaling rational coefficients changes extent, not physical amounts."""
        for scale in ["1/3", "1", "7", "1000000000000000000000000000000000000"]:
            with self.subTest(scale=scale):
                doc = document()
                for term in doc.reactions[0].reactants + doc.reactions[0].products:
                    term.coefficient = scale
                doc.reactions[0].reactants.append(type(doc.reactions[0].reactants[0])(species_id="inert", coefficient="2"))
                doc.reactions[0].products.append(type(doc.reactions[0].products[0])(species_id="inert", coefficient="2"))
                doc.reactions[0].catalyst_species_ids = ["inert"]
                request = request_for(doc)
                request.inventory.append(FeedAmount(species_id="inert", value=1, unit="mol"))
                result = evaluate_stoichiometry(doc, request)
                self.assertEqual(Fraction(2) / Fraction(scale), Fraction(result.maximum_extent_mol))
                self.assertEqual(["acid"], result.limiting_species_ids)
                amounts = {item.species_id: item for item in result.amounts}
                self.assertEqual("2", amounts["gas"].formed)
                self.assertEqual(("1", "0", "catalyst"), (amounts["inert"].final, amounts["inert"].consumed, amounts["inert"].role))

    def test_net_consumption_and_unequal_coefficients(self) -> None:
        """A species on both sides is consumed only by its net coefficient."""
        doc = document()
        term_type = type(doc.reactions[0].reactants[0])
        doc.reactions[0].reactants[0].coefficient = "2"
        doc.reactions[0].products.append(term_type(species_id="acid", coefficient="1"))
        result = evaluate_stoichiometry(doc, request_for(doc))
        self.assertEqual("2", result.maximum_extent_mol)
        self.assertEqual("2", next(item for item in result.amounts if item.species_id == "gas").formed)
        doc = ChemicalIR.model_validate({
            "artifact_id": "water-electrolysis",
            "species": [{"species_id": key, "name": key, "formula": formula, "charge": 0}
                        for key, formula in [("water", "H2O"), ("hydrogen", "H2"), ("oxygen", "O2")]],
            "reactions": [{"reaction_id": "neutralize", "reactants": [{"species_id": "water", "coefficient": "2"}],
                           "products": [{"species_id": "hydrogen", "coefficient": "2"},
                                        {"species_id": "oxygen", "coefficient": "1"}]}],
        })
        request = request_for(doc)
        request.inventory = [FeedAmount(species_id="water", value=3, unit="mol")]
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual("3/2", result.maximum_extent_mol)
        amounts = {item.species_id: item for item in result.amounts}
        self.assertEqual(("3", "3/2"), (amounts["hydrogen"].formed, amounts["oxygen"].formed))

    def test_mixed_units_and_mass_conservation(self) -> None:
        """120 g acid + 252 g base yields 88 g gas and leaves 84 g base."""
        for acid_unit, acid_value, base_unit, base_value in [
            ("g", 120, "kg", 0.252), ("mg", 120000, "mmol", 3000), ("kmol", 0.002, "mol", 3),
        ]:
            with self.subTest(acid_unit=acid_unit):
                doc = document()
                request = request_for(doc)
                request.inventory = [FeedAmount(species_id="acid", value=acid_value, unit=acid_unit),
                                     FeedAmount(species_id="base", value=base_value, unit=base_unit)]
                request.molar_masses = properties()
                request.molar_masses[0].unit = "kg/mol"
                request.molar_masses[0].value = 0.06
                result = evaluate_stoichiometry(doc, request)
                self.assertEqual("pass", result.status)
                self.assertEqual("0", result.conservation.mass_residual_g)
                amounts = {item.species_id: item for item in result.amounts}
                self.assertEqual("88", amounts["gas"].formed_mass_g)
                self.assertEqual("84", amounts["base"].final_mass_g)

    def test_missing_property_only_blocks_required_conversion(self) -> None:
        """Mole-only results survive missing masses; conversion never guesses."""
        doc = document()
        request = request_for(doc)
        request.molar_masses = properties()[:1]
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual("pass", result.status)
        self.assertEqual("unknown", result.conservation.mass_status)
        request.inventory[1].unit = "g"
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual("unknown", result.status)
        self.assertEqual("missing_molar_mass", result.findings[0].code)
        self.assertIsNone(result.maximum_extent_mol)

    def test_inventory_policy_and_missing_feed(self) -> None:
        """Explicit policy requires all participants, and consumed feed never defaults."""
        doc = document()
        request = request_for(doc)
        request.inventory_policy = "explicit"
        self.assertEqual("unknown", evaluate_stoichiometry(doc, request).status)
        request.inventory += [FeedAmount(species_id=key, value=0, unit="mol") for key in ["salt", "water", "gas"]]
        self.assertEqual("pass", evaluate_stoichiometry(doc, request).status)
        request.inventory = request.inventory[1:]
        request.inventory_policy = "zero_unlisted_nonreactants"
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual(("unknown", "acid"), (result.status, result.findings[0].location))

    def test_invalid_numbers_and_duplicates(self) -> None:
        """Schema errors are structured, including after unsafe model mutation."""
        for value in [-1, float("nan"), float("inf"), float("-inf")]:
            with self.subTest(value=value), self.assertRaises(ValidationError) as caught:
                FeedAmount(species_id="acid", value=value, unit="mol")
            self.assertEqual("value", caught.exception.errors()[0]["loc"][0])
        doc = document()
        request = request_for(doc)
        request.inventory[0].value = -1
        with self.assertRaises(ValidationError):
            evaluate_stoichiometry(doc, request)
        request = request_for(doc)
        request.inventory.append(request.inventory[0])
        with self.assertRaises(ValidationError):
            evaluate_stoichiometry(doc, request)
        for value in [0, -1, float("nan")]:
            prop = properties()[0].model_dump()
            prop["value"] = value
            with self.subTest(mass=value), self.assertRaises(ValidationError):
                MolarMass.model_validate(prop)

    def test_units_and_unknown_identities(self) -> None:
        """Unit/identity mistakes yield scoped findings rather than numbers."""
        doc = document()
        for unit in ["L", "mol/s", "mol/L"]:
            request = request_for(doc)
            request.inventory[0].unit = unit
            self.assertEqual("feed_unit", evaluate_stoichiometry(doc, request).findings[0].code)
        request = request_for(doc)
        request.molar_masses = properties()
        request.molar_masses[0].unit = "g"
        self.assertEqual("molar_mass_unit", evaluate_stoichiometry(doc, request).findings[0].code)
        request = request_for(doc)
        request.inventory[0].species_id = "missing"
        self.assertEqual("unknown_species", evaluate_stoichiometry(doc, request).findings[0].code)
        request = request_for(doc)
        request.reaction_id = "missing"
        self.assertEqual("unknown_reaction", evaluate_stoichiometry(doc, request).findings[0].code)

    def test_reaction_balance_gate(self) -> None:
        """Known imbalance, missing charge and unsupported formula stay distinct."""
        for kind, expected in [("atoms", "fail"), ("charge", "fail"), ("unknown", "unknown"), ("formula", "unsupported")]:
            with self.subTest(kind=kind):
                doc = document()
                if kind == "atoms":
                    doc.reactions[0].reactants[0].coefficient = "2"
                elif kind == "charge":
                    doc.species[0].charge = 1
                elif kind == "unknown":
                    doc.species[0].charge = None
                else:
                    doc.species[0].formula = "C2H4O2.H2O"
                result = evaluate_stoichiometry(doc, request_for(doc))
                self.assertEqual(expected, result.status)
                self.assertIsNone(result.maximum_extent_mol)

    def test_unsupported_modes(self) -> None:
        """Network, equilibrium, continuous and reverse requests are not silently downgraded."""
        doc = document()
        for mode in ["equilibrium", "reaction_network", "continuous_flow"]:
            request = request_for(doc)
            request.mode = mode
            self.assertEqual("unsupported", evaluate_stoichiometry(doc, request).status)
        request = request_for(doc)
        request.direction = "reverse"
        self.assertEqual("unsupported", evaluate_stoichiometry(doc, request).status)

    def test_role_conflicts(self) -> None:
        """A net consumed or produced species cannot be declared nonreacting."""
        for role in ["inert", "catalyst", "solvent"]:
            doc = document()
            request = request_for(doc)
            request.inventory[0].role = role
            self.assertEqual("nonreacting_role_conflict", evaluate_stoichiometry(doc, request).findings[0].code)
        doc.reactions[0].catalyst_species_ids = ["acid"]
        self.assertEqual("fail", evaluate_stoichiometry(doc, request_for(doc)).status)

    def test_mass_disagreement_and_tolerance(self) -> None:
        """Inconsistent supplied properties fail separately from atom balance."""
        doc = document()
        request = request_for(doc)
        request.molar_masses = properties()
        request.molar_masses[-1].value = 45
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual("fail", result.status)
        self.assertEqual("2", result.conservation.mass_residual_g)
        self.assertEqual("pass", result.conservation.reaction_balance.status)
        request.mass_balance_tolerance_g = 2
        self.assertEqual("pass", evaluate_stoichiometry(doc, request).status)

    def test_limiting_tolerance_never_clips_inventory(self) -> None:
        """Near co-limiting identification retains the actual positive residue."""
        doc = document()
        request = request_for(doc, 2, 2.000000001)
        result = evaluate_stoichiometry(doc, request)
        self.assertEqual(["acid", "base"], result.limiting_species_ids)
        base = next(item for item in result.amounts if item.species_id == "base")
        self.assertEqual(Fraction(1, 10**9), Fraction(base.final))
        request.limiting_relative_tolerance = 0
        request.limiting_absolute_tolerance_mol = 0
        self.assertEqual(["acid"], evaluate_stoichiometry(doc, request).limiting_species_ids)

    def test_stale_pin_and_changed_digest(self) -> None:
        """Revision and same-revision content edits invalidate pinned requests."""
        doc = document()
        request = request_for(doc)
        first = evaluate_stoichiometry(doc, request)
        request.inventory[0].value = 1
        self.assertNotEqual(first.input_digest, evaluate_stoichiometry(doc, request).input_digest)
        doc.species[0].name = "changed"
        self.assertEqual("stale_chemical_reference", evaluate_stoichiometry(doc, request).findings[0].code)
        request = request_for(doc)
        doc.revision += 1
        self.assertEqual("fail", evaluate_stoichiometry(doc, request).status)

    def test_evidence_and_round_trip(self) -> None:
        """Source records and request configuration survive receipt serialization."""
        doc = document()
        request = request_for(doc)
        request.molar_masses = properties()
        request.evidence = [EvidenceRecord(source_id="fixture-masses", basis="illustrative", title="Synthetic masses",
                                           rationale="Not experimental measurements.")]
        for prop in request.molar_masses:
            prop.claim.source_ids = ["fixture-masses"]
        request = StoichiometryRequest.model_validate(request.model_dump())
        result = evaluate_stoichiometry(doc, request)
        restored = StoichiometryResult.model_validate_json(result.model_dump_json())
        self.assertEqual(result, restored)
        self.assertEqual(result, evaluate_stoichiometry(doc, restored.request))
        self.assertEqual("fixture-masses", restored.evidence[0].source_id)
        request.molar_masses[0].claim.source_ids = ["missing"]
        self.assertEqual("unknown_evidence", evaluate_stoichiometry(doc, request).findings[0].code)
        with self.assertRaises(ValidationError):
            MolarMass(species_id="acid", value=60, unit="g/mol", claim={"basis": "unknown"})

    def test_evidence_collision_and_duplicate_properties(self) -> None:
        """Shared evidence may deduplicate but must not silently change meaning."""
        doc = document()
        record = EvidenceRecord(source_id="source", basis="illustrative", title="Fixture", rationale="Synthetic.")
        doc.evidence = [record]
        request = request_for(doc)
        request.evidence = [record.model_copy()]
        self.assertEqual(1, len(evaluate_stoichiometry(doc, request).evidence))
        request.evidence[0].title = "Conflicting source"
        self.assertEqual("conflicting_evidence", evaluate_stoichiometry(doc, request).findings[0].code)
        request = request_for(doc)
        request.molar_masses = [properties()[0], properties()[0]]
        with self.assertRaises(ValidationError):
            evaluate_stoichiometry(doc, request)

    def test_no_net_reaction(self) -> None:
        """A chemically balanced identity has no finite consumption bound."""
        doc = document()
        doc.reactions[0].products = [term.model_copy() for term in doc.reactions[0].reactants]
        self.assertEqual("unsupported", evaluate_stoichiometry(doc, request_for(doc)).status)

    def test_schema_and_standalone_import(self) -> None:
        """Contracts export JSON schemas without loading other engineering domains."""
        self.assertIn("properties", StoichiometryRequest.model_json_schema())
        self.assertIn("properties", StoichiometryResult.model_json_schema())
        output = subprocess.check_output([sys.executable, "-c", "import json,sys; import forma_core.chemistry.stoichiometry; print(json.dumps(sorted(sys.modules)))"], text=True)
        modules = json.loads(output)
        self.assertFalse(any(name.startswith(("forma_core.mechanical", "forma_core.electrical", "forma_core.processes",
                                              "forma_core.thermal", "forma_core.fluidics")) for name in modules))


if __name__ == "__main__":
    unittest.main()

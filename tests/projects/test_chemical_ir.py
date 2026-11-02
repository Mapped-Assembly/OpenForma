"""Acceptance and negative-path fixtures for independently owned ChemicalIR v1."""

from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from forma_core.chemistry.models import ChemicalIR, ChemicalContext
from forma_core.chemistry.validation import (
    UnsupportedFormula, check_reaction_balance, parse_formula, validate_chemical,
)
from forma_core.domains.base import fingerprint
from forma_core.domains.compatibility import export_hardware_ir
from forma_core.domains.composition import compose_product, validate_product
from forma_core.domains.generation import fixture_author, generate_product
from forma_core.domains.planning import ProductScope, select_domains
from forma_core.domains.registry import create_registry, parse_domain, validate_domain
from forma_core.domains.tools import call_domain_tool


FIXTURE = Path(__file__).resolve().parents[2] / "examples/domains/chemical-air-to-fuel.json"


def fixture() -> dict:
    """Load the synthetic, explicitly illustrative, canonical fixture."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ChemicalIRTests(unittest.TestCase):
    """Chemical local semantics are independent of all other domain IR packages."""

    def test_versioned_document_round_trip_and_registry(self) -> None:
        """New schemas are discoverable without touching legacy hardware IR."""
        document = ChemicalIR.model_validate(fixture())
        self.assertEqual("1.0", document.schema_version)
        self.assertEqual("chemical", document.domain)
        self.assertEqual(fingerprint(document), fingerprint(document.model_dump(mode="json")))
        self.assertEqual(document, ChemicalIR.model_validate_json(document.model_dump_json()))
        self.assertEqual([], validate_domain(document))
        self.assertTrue(call_domain_tool("validate-domain", fixture())["is_valid"])
        self.assertIn("ChemicalIR", str(call_domain_tool("schema", {"domain": "chemical"})["document"]))
        registry = call_domain_tool("registry", {})["domains"]
        self.assertEqual("OpenForma", registry["chemical"]["owner"])
        self.assertEqual(["1.0"], registry["chemical"]["schemas"][0]["versions"])
        broken = fixture()
        broken["schema_version"] = "99.0"
        with self.assertRaises(ValidationError):
            parse_domain(broken)

    def test_exact_atom_and_charge_balance(self) -> None:
        """Water electrolysis and methanol synthesis conserve elements and charge."""
        document = ChemicalIR.model_validate(fixture())
        for reaction in document.reactions:
            result = check_reaction_balance(document, reaction)
            self.assertEqual("pass", result.status)
            self.assertEqual({}, result.residuals)
        self.assertEqual({"Ca": 1, "O": 2, "H": 2}, parse_formula("Ca(OH)2"))
        self.assertEqual({"H": 1, "N": 1, "O": 3}, parse_formula("HNO3"))
        self.assertEqual(Fraction(1, 2), Fraction("1/2"))

    def test_unbalanced_reaction_is_an_error(self) -> None:
        """Balance failure cannot become a successful domain validation."""
        raw = fixture()
        raw["reactions"][0]["products"][0]["coefficient"] = "1"
        findings = validate_domain(raw)
        self.assertIn("reaction_balance_fail", [finding.code for finding in findings])
        self.assertTrue(any(f.severity == "error" for f in findings))
        self.assertFalse(call_domain_tool("validate-domain", raw)["is_valid"])

    def test_unknown_charge_and_unsupported_formula_never_pass(self) -> None:
        """Unknown is distinguishable from unsupported and from a true pass."""
        raw = fixture()
        raw["species"][0]["charge"] = None
        document = ChemicalIR.model_validate(raw)
        self.assertEqual("unknown", check_reaction_balance(document, document.reactions[0]).status)
        self.assertIn("reaction_balance_unknown", [f.code for f in validate_chemical(document)])
        raw = fixture()
        raw["species"][0]["formula"] = "H2O·2H2O"
        document = ChemicalIR.model_validate(raw)
        self.assertEqual("unsupported", check_reaction_balance(document, document.reactions[0]).status)
        self.assertIn("reaction_balance_unsupported", [f.code for f in validate_chemical(document)])
        for value in ("H0", "Xx2", "(OH", "(OH))", "3H2O", "Na+"):
            with self.subTest(formula=value), self.assertRaises(UnsupportedFormula):
                parse_formula(value)
        raw = fixture()
        raw["species"][0]["formula"] = None
        document = ChemicalIR.model_validate(raw)
        self.assertEqual("unknown", check_reaction_balance(document, document.reactions[0]).status)

    def test_charge_imbalance_is_an_error(self) -> None:
        """Explicit ionic charge is included in the same exact conservation check."""
        raw = fixture()
        raw["species"][0]["charge"] = 1
        document = ChemicalIR.model_validate(raw)
        balance = check_reaction_balance(document, document.reactions[0])
        self.assertEqual("fail", balance.status)
        self.assertIn("charge", balance.residuals)

    def test_reference_and_input_schema_reject_bad_documents(self) -> None:
        """Invalid source, species, coefficients and NaN are not silently repaired."""
        cases = []
        raw = fixture()
        raw["reactions"][0]["products"][0]["species_id"] = "missing"
        cases.append(raw)
        raw = fixture()
        raw["species"].append(deepcopy(raw["species"][0]))
        cases.append(raw)
        raw = fixture()
        raw["reactions"][0]["products"][0]["coefficient"] = "0"
        cases.append(raw)
        raw = fixture()
        raw["reactions"][0]["products"][0]["coefficient"] = "0.5"
        cases.append(raw)
        raw = fixture()
        raw["mixtures"][0]["components"][0]["value"] = float("nan")
        cases.append(raw)
        raw = fixture()
        raw["reactions"][0]["claim"]["source_ids"] = ["missing-source"]
        cases.append(raw)
        raw = fixture()
        raw["unexpected_field"] = 1
        cases.append(raw)
        for item in cases:
            with self.subTest(item=item), self.assertRaises((ValidationError, ValueError)):
                parse_domain(item)

    def test_composition_and_conditions_are_conservative(self) -> None:
        """No arbitrary fraction renormalization or unsupported condition units."""
        raw = fixture()
        raw["mixtures"][0]["components"][0]["value"] = 0.1
        raw["mixtures"][0]["complete"] = False
        self.assertIn("composition_incomplete", [f.code for f in validate_domain(raw)])
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])  # Warning, not fake complete.
        raw["mixtures"][0]["complete"] = True
        self.assertFalse(call_domain_tool("validate-domain", raw)["is_valid"])
        raw = fixture()
        raw["mixtures"][0]["components"][0]["value"] = 2.0
        self.assertIn("composition_overallocated", [f.code for f in validate_domain(raw)])
        raw = fixture()
        raw["mixtures"][0]["components"][0]["unit"] = "kg"
        self.assertIn("composition_unit", [f.code for f in validate_domain(raw)])
        raw = fixture()
        raw["conditions"] = [{"condition_id": "heat", "kind": "temperature", "value": -1, "unit": "K"}]
        self.assertIn("physical_temperature", [f.code for f in validate_domain(raw)])
        raw["conditions"][0]["value"] = 25
        raw["conditions"][0]["unit"] = "unlabeled"
        self.assertIn("condition_unit", [f.code for f in validate_domain(raw)])

    def test_standalone_generation_selection_and_revision(self) -> None:
        """No placeholder domains are instantiated; source revision is pinned."""
        ctx = {"scope": "Reaction equation reference", "chemical_requirements": ["Conserve atoms"],
               "evidence_requirements": ["Synthetic fixture only"]}
        self.assertEqual([], validate_domain(ChemicalIR.model_validate(fixture())))
        self.assertTrue(ChemicalContext.model_validate(ctx))
        plan = select_domains("air-to-fuel", "Author chemical equations",
                              scope=ProductScope(domains=["chemical"]), contexts={"chemical": ctx})
        self.assertEqual(["chemical"], [task.domain for task in plan.tasks])
        authored = {k: v for k, v in fixture().items() if k not in {"artifact_id", "revision", "domain", "schema_version"}}
        product = generate_product(plan, fixture_author({"chemical": authored}))
        self.assertTrue(validate_product(product).is_valid)
        self.assertEqual("air-to-fuel.chemical", product.manifest.artifacts[0].artifact_id)
        revised = generate_product(plan, fixture_author({"chemical": authored}),
                                   existing=product, update_domains={"chemical"})
        self.assertEqual(2, revised.manifest.artifacts[0].revision)
        self.assertNotEqual(product.manifest.artifacts[0].sha256, revised.manifest.artifacts[0].sha256)
        self.assertEqual(1, product.manifest.artifacts[0].revision)
        self.assertEqual(["chemical"], [ref.domain for ref in revised.manifest.artifacts])
        bracket_plan = select_domains("bracket", "Passive bracket", scope=ProductScope(physical_structure=True))
        self.assertNotIn("chemical", [task.domain for task in bracket_plan.tasks])

    def test_legacy_export_reports_chemical_loss(self) -> None:
        """Chemical facts must never be silently projected into HardwareIR."""
        document = ChemicalIR.model_validate(fixture())
        product = compose_product("air-to-fuel", [document])
        legacy = json.loads((FIXTURE.parent / "motor-hardware.json").read_text(encoding="utf-8"))
        report = export_hardware_ir(product, legacy_seed=legacy)
        self.assertIsNone(report.payload)
        self.assertIn("unrepresentable", [f.code for f in report.findings])
        acknowledged = export_hardware_ir(product, legacy_seed=legacy, allow_lossy=True)
        self.assertIsNotNone(acknowledged.payload)
        self.assertIn("unrepresentable", [f.code for f in acknowledged.findings])
        self.assertIn(document.artifact_id, product.artifacts)


if __name__ == "__main__":
    unittest.main()

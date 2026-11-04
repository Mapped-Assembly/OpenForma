"""Analytic references and public-interface regression for bounded batch kinetics."""

from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from pydantic import ValidationError

from forma_core.chemistry.kinetics import (
    BatchVolume, EvaluationTime, FirstOrderRateConstant, KineticsRequest, KineticsResult,
    Temperature, evaluate_kinetics, kinetics_result_is_current,
)
from forma_core.chemistry.models import ChemicalCondition, ChemicalIR
from forma_core.domains.base import fingerprint
from forma_core.domains.providers import DomainRegistry, ProviderMetadata
from forma_core.domains.tools import call_domain_tool


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "examples/domains/chemical-first-order-batch.json"


def fixture() -> tuple[ChemicalIR, KineticsRequest]:
    """Read synthetic, explicitly parameterized batch inputs without any provider."""
    data = json.loads(FIXTURE.read_text())
    return ChemicalIR.model_validate(data["document"]), KineticsRequest.model_validate(data["request"])


def repin(document: ChemicalIR, request: KineticsRequest) -> None:
    """Explicitly authorize a changed fixture revision for a new evaluation."""
    request.batch.chemical.revision = document.revision
    request.batch.chemical.sha256 = fingerprint(document)


class KineticsTests(unittest.TestCase):
    """Check analytic values, conservation and numerical/applicability boundaries."""

    def test_analytic_solution_and_rate_normalization(self) -> None:
        """At ten seconds, nA=2/e and extent=1-1/e for the written 2A reaction."""
        doc, request = fixture()
        result = evaluate_kinetics(doc, request)
        self.assertEqual("pass", result.status)
        self.assertEqual("peroxide", result.consumed_species_id)
        for sample, time in zip(result.samples[:4], [0, 1, 10, 100]):
            decay = math.exp(-0.1 * time)
            species = {s.amount.species_id: s for s in sample.species}
            self.assertAlmostEqual(2 * decay, float(Fraction(species["peroxide"].amount.final)), places=13)
            self.assertAlmostEqual(decay, float(Fraction(species["peroxide"].concentration_mol_per_l)), places=13)
            self.assertAlmostEqual(0.2 * decay, float(Fraction(sample.disappearance_rate_mol_per_s)), places=13)
            self.assertAlmostEqual(0.1 * decay, float(Fraction(sample.extent_rate_mol_per_s)), places=13)
            self.assertAlmostEqual(0.05 * decay, float(Fraction(sample.reaction_rate_mol_per_l_s)), places=13)
            self.assertAlmostEqual(1 - decay, float(Fraction(sample.extent_mol)), places=13)
            self.assertEqual(Fraction(species["peroxide"].net_rate_mol_per_s), -Fraction(sample.disappearance_rate_mol_per_s))
            self.assertEqual(Fraction(species["oxygen"].net_rate_mol_per_s), Fraction(sample.extent_rate_mol_per_s))

    def test_conservation_and_initial_products(self) -> None:
        """Every sample retains initial water/inert and exactly closes net balances."""
        doc, request = fixture()
        result = evaluate_kinetics(doc, request)
        bound = Fraction(result.stoichiometric_bound.maximum_extent_mol)
        for sample in result.samples:
            self.assertLessEqual(Fraction(sample.extent_mol), bound)
            self.assertTrue(all(Fraction(item.amount.final) >= 0 for item in sample.species))
            self.assertEqual("pass", sample.conservation.mass_status)
            self.assertEqual("0", sample.conservation.mass_residual_g)
            self.assertEqual("0", sample.conservation.charge_residual_mol)
            self.assertTrue(all(value == "0" for value in sample.conservation.element_residuals_mol.values()))
            species = {s.amount.species_id: s.amount for s in sample.species}
            self.assertEqual(Fraction(10), Fraction(species["water"].final) - Fraction(species["water"].formed))
            self.assertEqual(("1", "0", "0"), (species["inert"].final, species["inert"].formed, species["inert"].consumed))
        self.assertEqual("0", result.samples[0].extent_mol)

    def test_zero_rate_and_zero_initial_reactant(self) -> None:
        """Zero inputs remain exact; no spurious reaction or rate is created."""
        for field in ["rate", "feed"]:
            doc, request = fixture()
            if field == "rate":
                request.rate_constant.value = 0
            else:
                request.batch.inventory[0].value = 0
            result = evaluate_kinetics(doc, request)
            self.assertEqual("pass", result.status)
            self.assertTrue(all(sample.extent_mol == "0" and sample.disappearance_rate_mol_per_s == "0" for sample in result.samples))

    def test_unit_equivalence(self) -> None:
        """Time/rate, volume and temperature conversions preserve physical samples."""
        doc, request = fixture()
        request.times = [EvaluationTime(value=60, unit="s"), EvaluationTime(value=3600, unit="s")]
        baseline = evaluate_kinetics(doc, request)
        for rate, unit, volume, volume_unit in [(6, "1/min", 2000, "mL"), (360, "1/h", 0.002, "m3")]:
            with self.subTest(unit=unit):
                revised = request.model_copy(deep=True)
                revised.times = [EvaluationTime(value=1, unit="min"), EvaluationTime(value=1, unit="h")]
                revised.rate_constant.value, revised.rate_constant.unit = rate, unit
                revised.volume.value, revised.volume.unit = volume, volume_unit
                revised.temperature = Temperature(value=25, unit="degC")
                self.assertEqual(baseline.samples, evaluate_kinetics(doc, revised).samples)

    def test_coefficient_scaling(self) -> None:
        """Amounts and species rates are invariant; extent and extent rate scale inversely."""
        doc, request = fixture()
        baseline = evaluate_kinetics(doc, request)
        for scale in [Fraction(3), Fraction(1, 7)]:
            revised_doc = doc.model_copy(deep=True)
            revised_request = request.model_copy(deep=True)
            for term in revised_doc.reactions[0].reactants + revised_doc.reactions[0].products:
                term.coefficient = str(Fraction(term.coefficient) * scale)
            repin(revised_doc, revised_request)
            result = evaluate_kinetics(revised_doc, revised_request)
            for expected, actual in zip(baseline.samples, result.samples):
                self.assertEqual(Fraction(expected.extent_mol), Fraction(actual.extent_mol) * scale)
                self.assertEqual(Fraction(expected.extent_rate_mol_per_s), Fraction(actual.extent_rate_mol_per_s) * scale)
                for a, b in zip(expected.species, actual.species):
                    self.assertEqual(a.amount.final, b.amount.final)
                    self.assertEqual(a.net_rate_mol_per_s, b.net_rate_mol_per_s)

    def test_extreme_times_and_tiny_conversion(self) -> None:
        """Tiny conversions survive expm1, while underflowed tails are explicitly flagged."""
        doc, request = fixture()
        request.times = [EvaluationTime(value=t, unit="s") for t in [1e-20, 400, 10000, 1e308]]
        result = evaluate_kinetics(doc, request)
        self.assertGreater(Fraction(result.samples[0].extent_mol), 0)
        self.assertAlmostEqual(1e-21, float(Fraction(result.samples[0].extent_mol)), delta=1e-35)
        remaining = next(s.amount.final for s in result.samples[1].species if s.amount.species_id == "peroxide")
        self.assertGreater(Fraction(remaining), 0)
        self.assertEqual("1", result.samples[-1].extent_mol)
        self.assertIn("kinetic_precision_limit", [f.code for f in result.findings])
        request.rate_constant.value = 1e308
        self.assertEqual("pass", evaluate_kinetics(doc, request).status)

    def test_unknown_conditions_and_parameters(self) -> None:
        """Missing declarations and missing k/provenance never produce a time series."""
        doc, original = fixture()
        for name in ["rate_constant", "temperature", "volume", "constant_volume", "isothermal", "competing_reactions"]:
            request = original.model_copy(deep=True)
            setattr(request, name, None)
            result = evaluate_kinetics(doc, request)
            self.assertEqual(("unknown", []), (result.status, result.samples), name)
        request = original.model_copy(deep=True)
        request.rate_constant.temperature = None
        self.assertEqual("unknown", evaluate_kinetics(doc, request).status)
        request = original.model_copy(deep=True)
        request.rate_constant.claim.basis = "unknown"
        self.assertEqual("missing_rate_provenance", evaluate_kinetics(doc, request).findings[-1].code)

    def test_invalid_quantities_and_units(self) -> None:
        """Negative/nonfinite quantities fail schemas; incompatible dimensions fail findings."""
        for value in [-1, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                FirstOrderRateConstant(value=value, unit="1/s")
            with self.subTest(time=value), self.assertRaises(ValidationError):
                EvaluationTime(value=value, unit="s")
        for value in [0, -1, float("inf")]:
            with self.subTest(volume=value), self.assertRaises(ValidationError):
                BatchVolume(value=value, unit="L")
        doc, original = fixture()
        for field in ["rate_constant", "volume", "temperature"]:
            request = original.model_copy(deep=True)
            getattr(request, field).unit = "kg"
            self.assertEqual("fail", evaluate_kinetics(doc, request).status)
        request = original.model_copy(deep=True)
        request.times[0].unit = "mol"
        self.assertEqual("fail", evaluate_kinetics(doc, request).status)
        request = original.model_copy(deep=True)
        request.temperature.value = -1
        self.assertEqual("fail", evaluate_kinetics(doc, request).status)
        request = original.model_copy(deep=True)
        request.rate_constant.value = -1
        with self.assertRaises(ValidationError):
            evaluate_kinetics(doc, request)

    def test_unsupported_regimes(self) -> None:
        """Reversibility, competing reactions and unimplemented models are explicit."""
        doc, original = fixture()
        for field, value in [("constant_volume", False), ("isothermal", False), ("competing_reactions", True),
                             ("model", "arrhenius"), ("model_version", "2.0"), ("rate_law", "second_order")]:
            request = original.model_copy(deep=True)
            setattr(request, field, value)
            self.assertEqual("unsupported", evaluate_kinetics(doc, request).status)
        doc.reactions[0].reversible = True
        repin(doc, original)
        self.assertEqual("unsupported", evaluate_kinetics(doc, original).status)
        doc, request = fixture()
        request.batch.direction = "reverse"
        self.assertEqual("unsupported", evaluate_kinetics(doc, request).status)
        from tests.projects.test_stoichiometry import document, request_for
        other_doc = document()
        request.batch = request_for(other_doc)
        self.assertEqual("kinetic_reactants", evaluate_kinetics(other_doc, request).findings[-1].code)

    def test_temperature_applicability(self) -> None:
        """Temperature-specific k cannot silently be reused at a new condition."""
        doc, request = fixture()
        request.temperature.value = 300
        self.assertEqual("unsupported", evaluate_kinetics(doc, request).status)
        doc, request = fixture()
        doc.conditions = [ChemicalCondition(condition_id="temp", kind="temperature", value=50, unit="degC")]
        doc.reactions[0].condition_ids = ["temp"]
        repin(doc, request)
        self.assertEqual("reaction_temperature_applicability", evaluate_kinetics(doc, request).findings[-1].code)

    def test_freshness_and_round_trip(self) -> None:
        """Every source change invalidates the receipt; unchanged requests recompute identically."""
        doc, request = fixture()
        result = evaluate_kinetics(doc, request)
        self.assertEqual(result, KineticsResult.model_validate_json(result.model_dump_json()))
        self.assertEqual(result, evaluate_kinetics(doc, result.request))
        self.assertTrue(kinetics_result_is_current(result, doc, request))
        for field in ["rate_constant", "volume", "temperature"]:
            revised = request.model_copy(deep=True)
            getattr(revised, field).value += 1
            self.assertFalse(kinetics_result_is_current(result, doc, revised))
        doc.evidence[0].title = "Changed evidence"
        self.assertFalse(kinetics_result_is_current(result, doc, request))
        self.assertEqual("fail", evaluate_kinetics(doc, request).status)
        repin(doc, request)
        self.assertNotEqual(result.input_digest, evaluate_kinetics(doc, request).input_digest)

    def test_provenance_and_mass_coverage(self) -> None:
        """Unknown mass coverage does not erase valid mole predictions or invent uncertainty."""
        doc, request = fixture()
        request.rate_constant.claim.source_ids = ["absent"]
        self.assertEqual("unknown_kinetic_evidence", evaluate_kinetics(doc, request).findings[-1].code)
        doc, request = fixture()
        request.batch.molar_masses = []
        result = evaluate_kinetics(doc, request)
        self.assertEqual("pass", result.status)
        self.assertTrue(all(sample.conservation.mass_status == "unknown" for sample in result.samples))
        self.assertEqual("illustrative", result.request.rate_constant.claim.basis)
        self.assertEqual("synthetic-kinetics", result.stoichiometric_bound.evidence[0].source_id)

    def test_discovery_cli_and_python_equivalence(self) -> None:
        """The existing domains surface exposes matching schemas, samples and exit statuses."""
        doc, request = fixture()
        payload = {"action": "evaluate", "document": doc.model_dump(mode="json"), "request": request.model_dump(mode="json")}
        python_result = call_domain_tool("kinetics", payload)
        command = [sys.executable, "-m", "forma_core", "domains", "kinetics", "-"]
        cli = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT, check=True)
        self.assertEqual(python_result, json.loads(cli.stdout))
        schema = call_domain_tool("kinetics", {"action": "schema"})
        self.assertEqual("first-order-batch", schema["model"])
        cli = subprocess.run(command, input='{"action":"schema"}', text=True, capture_output=True, cwd=ROOT, check=True)
        self.assertEqual(schema, json.loads(cli.stdout))
        payload["request"]["rate_constant"] = None
        cli = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(1, cli.returncode)
        self.assertEqual("unknown", json.loads(cli.stdout)["status"])
        with self.assertRaises(ValueError):
            call_domain_tool("kinetics", {"action": "schema", "unexpected": True})

    def test_missing_provider_never_falls_back(self) -> None:
        """An unavailable registered provider is not silently replaced by native chemistry."""
        registry = DomainRegistry()
        registry.register(ProviderMetadata(domain="chemical", ir_type="ExternalChemicalIR", owner="External",
                                           provider_id="external.chemical", provider_version="1.0", availability="unavailable"))
        self.assertEqual("unsupported", call_domain_tool("kinetics", {"action": "schema"}, registry=registry)["status"])


if __name__ == "__main__":
    unittest.main()

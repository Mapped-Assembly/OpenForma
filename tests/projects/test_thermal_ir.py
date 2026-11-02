"""ThermalIR #27: standalone contracts, bounded checks, and pinned cross-domain semantics."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import unittest

from pydantic import ValidationError

from forma_core.domains.base import fingerprint
from forma_core.domains.compatibility import export_hardware_ir
from forma_core.domains.composition import DomainLink, LinkEndpoint, compose_product, validate_product
from forma_core.domains.generation import fixture_author, generate_product
from forma_core.domains.planning import ProductScope, select_domains
from forma_core.domains.registry import parse_domain, validate_domain
from forma_core.domains.tools import call_domain_tool
from forma_core.thermal.models import (
    ThermalContext, ThermalIR, ThermalQuantity, ThermalSourceReference,
)
from forma_core.thermal.validation import analyze_thermal, temperature_kelvin

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/domains"


def load(name: str) -> dict:
    """Read a synthetic versioned engineering fixture without source inference."""
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ThermalIRTests(unittest.TestCase):
    """Standalone and integrated ThermalIR guarantees."""

    def test_standalone_roundtrip_and_registry(self) -> None:
        """ThermalIR publishes schema independently of all hardware/project IRs."""
        raw = load("thermal-heater.json")
        doc = ThermalIR.model_validate(raw)
        self.assertEqual("thermal", doc.domain)
        self.assertEqual("1.0", doc.schema_version)
        self.assertEqual(doc, ThermalIR.model_validate_json(doc.model_dump_json()))
        self.assertEqual([], validate_domain(doc))
        self.assertEqual(fingerprint(doc), fingerprint(doc.model_dump(mode="json")))
        self.assertEqual("ThermalIR", call_domain_tool("registry", {})["domains"]["thermal"]["ir_type"])
        self.assertIn("ThermalIR", str(call_domain_tool("schema", {"domain": "thermal"})["document"]))
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])
        bad = deepcopy(raw)
        bad["schema_version"] = "2.0"
        with self.assertRaises((ValueError, ValidationError)):
            parse_domain(bad)

    def test_independent_import_no_other_irs_or_hardware(self) -> None:
        """Selective schema discovery never eagerly imports other discipline schemas."""
        code = (
            "import sys; from forma_core.domains.registry import domain_model; "
            "domain_model('thermal').model_json_schema(); "
            "assert 'forma_core.chemistry.models' not in sys.modules; "
            "assert 'forma_core.processes.models' not in sys.modules; "
            "assert 'forma_core.mechanics.models' not in sys.modules; "
            "assert 'forma_core.workspaces.projects.models' not in sys.modules"
        )
        child = subprocess.run([sys.executable, "-c", code], text=True,
                               capture_output=True, check=False)
        self.assertEqual(0, child.returncode, child.stderr)

    def test_heater_benchmark_and_perturbation(self) -> None:
        """20 W × 0.5 K/W above 20 °C is 30 °C; 10 W gives 25 °C."""
        raw = load("thermal-heater.json")
        doc = ThermalIR.model_validate(raw)
        before = analyze_thermal(doc).results[0]
        self.assertEqual("pass", before.status)
        self.assertEqual(Decimal("30"), Decimal(before.zone_temperature_celsius))
        self.assertEqual(Decimal("20"), Decimal(before.net_heat_w))
        self.assertEqual(Decimal("0"), Decimal(before.residual_w))
        raw["loads"][0]["heat_rate"]["value"] = 10
        after = analyze_thermal(ThermalIR.model_validate(raw)).results[0]
        self.assertEqual("pass", after.status)
        self.assertEqual(Decimal("25"), Decimal(after.zone_temperature_celsius))
        self.assertNotEqual(before.input_digest, after.input_digest)
        self.assertEqual("1.0", after.model_version)

    def test_temperature_differences_not_absolute_celsius(self) -> None:
        """0 °C absolute is 273.15 K; delta-degree Celsius is not an absolute unit."""
        self.assertEqual(Decimal("273.15"), temperature_kelvin(
            ThermalQuantity(kind="temperature", value=0, unit="degC")))
        self.assertEqual(Decimal("10"), Decimal(ThermalQuantity(
            kind="temperature_difference", value=10, unit="delta_degC").value))
        with self.assertRaises(ValidationError):
            ThermalQuantity(kind="temperature", value=20, unit="delta_degC")
        with self.assertRaises(ValidationError):
            ThermalQuantity(kind="temperature_difference", value=20, unit="degC")
        with self.assertRaises(ValidationError):
            ThermalQuantity(kind="temperature", value=-274, unit="degC")

    def test_unknown_inputs_are_not_assumed_to_be_zero(self) -> None:
        """Unknown ambient, loss coefficient or watts stays an unknown evaluation."""
        raw = load("thermal-heater.json")
        raw["boundaries"][0]["quantity"] = None
        self.assertEqual("unknown", analyze_thermal(ThermalIR.model_validate(raw)).results[0].status)
        raw = load("thermal-heater.json")
        raw["loads"][0]["heat_rate"] = None
        self.assertEqual("unknown", analyze_thermal(ThermalIR.model_validate(raw)).results[0].status)
        raw = load("thermal-heater.json")
        raw["edges"][0]["resistance"] = None
        self.assertEqual("unknown", analyze_thermal(ThermalIR.model_validate(raw)).results[0].status)
        self.assertIn("thermal_unknown", [f.code for f in validate_domain(raw)])
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])

    def test_design_temperature_limit_is_not_a_compliance_verdict(self) -> None:
        """A declared engineering max is checked without claiming safety approval."""
        raw = load("thermal-heater.json")
        raw["nodes"][0]["maximum_temperature"] = {
            "kind": "temperature", "value": 29, "unit": "degC"
        }
        doc = ThermalIR.model_validate(raw)
        self.assertEqual("fail", analyze_thermal(doc).results[0].status)
        self.assertIn("thermal_fail", [f.code for f in validate_domain(raw)])
        raw["nodes"][0]["target_temperature"] = {
            "kind": "temperature", "value": 35, "unit": "degC"
        }
        with self.assertRaises((ValidationError, ValueError)):
            ThermalIR.model_validate(raw)

    def test_all_thermal_quantity_claims_are_checked(self) -> None:
        """A thermal resistance cannot claim measurement without source evidence."""
        raw = load("thermal-heater.json")
        raw["edges"][0]["resistance"]["claim"] = {
            "basis": "measured", "source_ids": ["absent"]
        }
        with self.assertRaises((ValidationError, ValueError)):
            ThermalIR.model_validate(raw)

    def test_unsupported_conduction_and_greenhouse(self) -> None:
        """No heat-transfer coefficient, emissivity or CFD result is fabricated."""
        raw = load("thermal-greenhouse.json")
        doc = ThermalIR.model_validate(raw)
        self.assertEqual("unsupported", analyze_thermal(doc).results[0].status)
        self.assertEqual("warning", validate_domain(raw)[0].severity)
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])
        raw = load("thermal-heater.json")
        raw["edges"][0]["mechanism"] = "radiation"
        self.assertEqual("unsupported", analyze_thermal(ThermalIR.model_validate(raw)).results[0].status)

    def test_invalid_dimensions_edges_boundaries_and_evidence(self) -> None:
        """Structural impossibilities must fail independently of model coverage."""
        variants = []
        raw = load("thermal-heater.json")
        raw["edges"][0]["resistance"]["value"] = 0
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["edges"][0]["resistance"]["kind"] = "temperature_difference"
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["edges"][0]["source_node_id"] = "missing"
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["edges"][0]["conductance"] = {"kind": "conductance", "value": 2, "unit": "W/K"}
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["boundaries"].append({"boundary_id": "insulated", "node_id": "ambient", "kind": "adiabatic"})
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["boundaries"].append({"boundary_id": "second", "node_id": "ambient", "kind": "fixed_temperature"})
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["loads"][0]["heat_rate"]["value"] = float("nan")
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["loads"][0]["heat_rate"]["unit"] = "J"
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["nodes"][0]["capacity"] = {"kind": "capacity", "value": -1, "unit": "J/K"}
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["evidence"].append({"source_id": "bad", "basis": "measured", "title": "Missing exact source"})
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["loads"][0]["heat_rate"]["claim"] = {"basis": "measured", "source_ids": ["not_found"]}
        variants.append(raw)
        raw = load("thermal-heater.json")
        raw["duplicate"] = True
        variants.append(raw)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises((ValidationError, ValueError)):
                parse_domain(candidate)

    def test_declarative_chemical_heat_duty_is_unknown_until_supported(self) -> None:
        """Reaction name and apparent 3D vessel do not imply a heat rate."""
        raw = load("thermal-reactor.json")
        doc = ThermalIR.model_validate(raw)
        self.assertEqual("unknown", analyze_thermal(doc).results[0].status)
        raw["loads"][0]["heat_rate"] = {"kind": "heat_rate", "value": 50, "unit": "W"}
        with self.assertRaises((ValidationError, ValueError)):
            ThermalIR.model_validate(raw)

    def test_pinned_chemical_process_links_and_invalidation(self) -> None:
        """Cross-IR provenance and typed ports are checked only at composition."""
        from forma_core.chemistry.models import ChemicalIR
        from forma_core.processes.models import ProcessIR, ProcessPort

        chemical = ChemicalIR.model_validate(load("chemical-air-to-fuel.json"))
        process = ProcessIR.model_validate(load("process-air-to-fuel.json"))
        process_op = next(op for op in process.operations if op.operation_id == "methanol_reactor")
        process_op.ports.append(ProcessPort(port_id="heat_out", direction="out", kind="energy",
                                            quantity_kind="power"))
        process = ProcessIR.model_validate(process.model_dump(mode="json"))
        raw = load("thermal-reactor.json")
        raw["loads"][0]["references"] = [
            ThermalSourceReference(
                domain="chemical", artifact_id=chemical.artifact_id,
                entity_id="methanol_synthesis", schema_version="1.0",
                revision=1, sha256=fingerprint(chemical)).model_dump(),
            ThermalSourceReference(
                domain="process", artifact_id=process.artifact_id,
                entity_id="methanol_reactor", schema_version="1.0",
                revision=1, sha256=fingerprint(process)).model_dump(),
        ]
        thermal = ThermalIR.model_validate(raw)
        links = [
            DomainLink(link_id="reaction-origin", kind="thermal_source",
                source=LinkEndpoint(artifact_id=thermal.artifact_id, revision=1, object_id="reaction-heat"),
                target=LinkEndpoint(artifact_id=chemical.artifact_id, revision=1, object_id="methanol_synthesis"),
                fact_owners={"chemical.reaction": "chemical", "thermal.heat_duty": "thermal"}),
            DomainLink(link_id="extent-origin", kind="thermal_source",
                source=LinkEndpoint(artifact_id=thermal.artifact_id, revision=1, object_id="reaction-heat"),
                target=LinkEndpoint(artifact_id=process.artifact_id, revision=1, object_id="methanol_reactor"),
                fact_owners={"process.operation": "process"}),
            DomainLink(link_id="reactor-heat", kind="uses_thermal_interface",
                source=LinkEndpoint(artifact_id=process.artifact_id, revision=1,
                                    object_id="methanol_reactor", port_id="heat_out"),
                target=LinkEndpoint(artifact_id=thermal.artifact_id, revision=1,
                                    object_id="reactor", port_id="heat"),
                fact_owners={"thermal.heat_duty": "thermal"}),
        ]
        product = compose_product("reactor", [thermal, process, chemical], links=links)
        report = validate_product(product)
        self.assertTrue(report.is_valid, report.model_dump())
        omitted = validate_product(compose_product(
            "reactor", [thermal, process, chemical], links=links[1:]))
        self.assertIn("thermal_link_missing", [f.code for f in omitted.composition])
        self.assertFalse(any(f.severity == "error" for f in omitted.local[thermal.artifact_id]))
        stale = chemical.model_copy(update={"revision": 2})
        report = validate_product(compose_product("reactor", [thermal, process, stale], links=links))
        codes = {f.code for f in report.composition}
        self.assertIn("link_revision", codes)
        self.assertIn("thermal_source_reference", codes)
        self.assertFalse(any(f.severity == "error" for f in report.local[thermal.artifact_id]))
        self.assertIn("thermal_unknown", [f.code for f in report.local[thermal.artifact_id]])

        wrong = deepcopy(links)
        wrong[0].target.object_id = "hydrogen"
        report = validate_product(compose_product("reactor", [thermal, process, chemical], links=wrong))
        self.assertIn("thermal_chemical_type", [f.code for f in report.composition])

    def test_illustrative_reaction_heat_must_pin_calculation_model(self) -> None:
        """Even a synthetic derived rate requires pinned chemistry/extent and model."""
        from forma_core.chemistry.models import ChemicalIR
        from forma_core.processes.models import ProcessIR
        chemical = ChemicalIR.model_validate(load("chemical-air-to-fuel.json"))
        process = ProcessIR.model_validate(load("process-air-to-fuel.json"))
        raw = load("thermal-reactor.json")
        raw["loads"][0]["references"] = [
            ThermalSourceReference(domain="chemical", artifact_id=chemical.artifact_id,
                entity_id="methanol_synthesis", schema_version="1.0",
                revision=1, sha256=fingerprint(chemical)).model_dump(),
            ThermalSourceReference(domain="process", artifact_id=process.artifact_id,
                entity_id="methanol_reactor", schema_version="1.0",
                revision=1, sha256=fingerprint(process)).model_dump(),
        ]
        raw["loads"][0]["calculation_model"] = "synthetic-screening-only-v1"
        raw["loads"][0]["claim"] = {"basis": "illustrative", "rationale": "Explicit synthetic heat duty, no real reactor enthalpy/extent model."}
        raw["loads"][0]["heat_rate"] = {"kind": "heat_rate", "value": 10, "unit": "W"}
        result = analyze_thermal(ThermalIR.model_validate(raw)).results[0]
        self.assertEqual("pass", result.status)
        self.assertEqual(Decimal("30"), Decimal(result.zone_temperature_celsius))

    def test_registry_generation_and_updates_are_independent(self) -> None:
        """Thermal selection neither selects chemistry nor creates placeholders."""
        context = {"scope": "Thermal screening", "thermal_requirements": ["Single-zone design"],
                   "boundary_requirements": ["Declared room temperature"],
                   "evidence_requirements": ["Synthetic fixture"]}
        self.assertIsNotNone(ThermalContext.model_validate(context))
        plan = select_domains("heater-screening", "Evaluate heater", scope=ProductScope(domains=["thermal"]),
                              contexts={"thermal": context})
        self.assertEqual(["thermal"], [t.domain for t in plan.tasks])
        fixture = load("thermal-heater.json")
        authored = {key: value for key, value in fixture.items()
                    if key not in {"artifact_id", "revision", "domain", "schema_version"}}
        product = generate_product(plan, fixture_author({"thermal": authored}))
        self.assertTrue(validate_product(product).is_valid)
        revision = generate_product(plan, fixture_author({"thermal": authored}),
                                    existing=product, update_domains={"thermal"})
        self.assertEqual(2, revision.manifest.artifacts[0].revision)
        self.assertNotEqual(product.manifest.artifacts[0].sha256,
                            revision.manifest.artifacts[0].sha256)
        self.assertEqual(1, product.manifest.artifacts[0].revision)
        self.assertEqual(["thermal"], [ref.domain for ref in revision.manifest.artifacts])
        bracket = select_domains("bracket", "Simple bracket",
                                 scope=ProductScope(physical_structure=True))
        self.assertNotIn("thermal", [task.domain for task in bracket.tasks])

    def test_legacy_hardware_export_is_explicitly_lossy(self) -> None:
        """Thermal results stay in independent artifacts when HardwareIR is exported."""
        thermal = ThermalIR.model_validate(load("thermal-heater.json"))
        product = compose_product("heater-screening", [thermal])
        legacy = load("motor-hardware.json")
        blocked = export_hardware_ir(product, legacy_seed=legacy)
        self.assertIsNone(blocked.payload)
        self.assertIn("unrepresentable", [f.code for f in blocked.findings])
        permitted = export_hardware_ir(product, legacy_seed=legacy, allow_lossy=True)
        self.assertIsNotNone(permitted.payload)
        self.assertIn("unrepresentable", [f.code for f in permitted.findings])
        self.assertIn(thermal.artifact_id, product.artifacts)

    def test_cli_validation_matches_public_domain_tool(self) -> None:
        """The public CLI uses the same schema and local findings as the SDK."""
        path = EXAMPLES / "thermal-heater.json"
        result = subprocess.run(
            [sys.executable, "-m", "forma_core", "domains", "validate-domain", str(path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)["is_valid"])


if __name__ == "__main__":
    unittest.main()

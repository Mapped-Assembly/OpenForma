"""ProcessIR #26: independent topology, conservative accounting, and cross-domain links."""

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
from forma_core.processes.models import ProcessContext, ProcessIR
from forma_core.processes.validation import analyze_process, evaluate_balance, process_recycles


EXAMPLES = Path(__file__).resolve().parents[2] / "examples/domains"


def load(name: str) -> dict:
    """Load a reproducible process fixture without inferring any missing values."""
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ProcessIRTests(unittest.TestCase):
    """New process contracts remain usable independently of chemistry and runtime."""

    def test_standalone_registry_schema_serialization(self) -> None:
        """A valid nonchemical design requires only the process package."""
        raw = load("process-irrigation.json")
        document = ProcessIR.model_validate(raw)
        self.assertEqual("process", document.domain)
        self.assertEqual("1.0", document.schema_version)
        self.assertEqual(document, ProcessIR.model_validate_json(document.model_dump_json()))
        self.assertEqual(fingerprint(document), fingerprint(raw))
        self.assertEqual([], validate_domain(raw))
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])
        self.assertEqual("ProcessIR", call_domain_tool("registry", {})["domains"]["process"]["ir_type"])
        schema = call_domain_tool("schema", {"domain": "process"})
        self.assertIn("ProcessIR", str(schema["document"]))
        broken = deepcopy(raw)
        broken["schema_version"] = "99.0"
        with self.assertRaises(ValidationError):
            parse_domain(broken)

    def test_no_other_domain_is_loaded(self) -> None:
        """Schema discovery does not import HardwareIR or unrelated IR modules."""
        code = (
            "import sys; from forma_core.domains.registry import domain_model; "
            "domain_model('process').model_json_schema(); "
            "assert 'forma_core.chemistry.models' not in sys.modules; "
            "assert 'forma_core.workspaces.projects.models' not in sys.modules; "
            "assert 'forma_core.fluidics.ir' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_balance_and_perturbation(self) -> None:
        """Explicit, conserved mass rates calculate deterministically."""
        raw = load("process-irrigation.json")
        original = ProcessIR.model_validate(raw)
        result = analyze_process(original)
        self.assertEqual([], result.recycle_components)
        self.assertEqual("pass", result.balance_results[0].status)
        self.assertEqual(Decimal(0), Decimal(result.balance_results[0].residual_base_units))
        raw["streams"][1]["quantity"]["value"] = 0.8
        changed = ProcessIR.model_validate(raw)
        bad = evaluate_balance(changed, changed.balances[0])
        self.assertEqual("fail", bad.status)
        self.assertGreater(Decimal(bad.residual_base_units), 0)
        self.assertIn("process_balance_fail", [f.code for f in validate_domain(raw)])
        self.assertFalse(call_domain_tool("validate-domain", raw)["is_valid"])

    def test_unknown_and_unsupported_are_not_zero(self) -> None:
        """Missing rates and mismatched quantity bases do not pass a balance."""
        raw = load("process-irrigation.json")
        raw["streams"][1]["quantity"] = None
        doc = ProcessIR.model_validate(raw)
        self.assertEqual("unknown", analyze_process(doc).balance_results[0].status)
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])
        self.assertIn("process_balance_unknown", [f.code for f in validate_domain(raw)])
        raw = load("process-irrigation.json")
        raw["streams"][1]["quantity"] = {"kind": "molar_rate", "value": 1, "unit": "mol/s"}
        raw["operations"][1]["ports"][1]["quantity_kind"] = None
        raw["operations"][2]["ports"][0]["quantity_kind"] = None
        doc = ProcessIR.model_validate(raw)
        self.assertEqual("unsupported", analyze_process(doc).balance_results[0].status)

    def test_cycles_are_legal_recycle_flows(self) -> None:
        """A process recycle is not an orchestrator task-dependency error."""
        raw = load("process-irrigation.json")
        raw["operations"][0]["ports"].append(
            {"port_id": "return", "direction": "in", "kind": "material",
             "quantity_kind": "mass_rate", "phase": "liquid"}
        )
        raw["streams"].append(
            {"stream_id": "recycle", "source": {"operation_id": "crop_bed", "port_id": "drain"},
             "target": {"operation_id": "reservoir", "port_id": "return"}, "kind": "material",
             "phase": "liquid"}
        )
        doc = ProcessIR.model_validate(raw)
        self.assertEqual([["crop_bed", "pump", "reservoir"]], process_recycles(doc))
        self.assertTrue(call_domain_tool("validate-domain", raw)["is_valid"])
        self.assertEqual("pass", analyze_process(doc).balance_results[0].status)

    def test_topology_rejects_bad_references_types_and_units(self) -> None:
        """Port direction/kind/dimensions and IDs are structural constraints."""
        cases = []
        raw = load("process-irrigation.json")
        raw["streams"][0]["target"]["port_id"] = "missing"
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"][0]["source"]["port_id"] = "return"
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"][0]["kind"] = "energy"
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"][0]["quantity"]["unit"] = "mol/s"
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"][0]["quantity"]["value"] = float("nan")
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"].append(deepcopy(raw["streams"][0]))
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["streams"][0]["phase"] = "gas"
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["scenarios"].append({
            "scenario_id": "invalid", "description": "Unknown unit",
            "overrides": [{"operation_id": "missing", "parameter": "power",
                           "quantity": {"kind": "power", "value": 2, "unit": "W"}}],
        })
        cases.append(raw)
        raw = load("process-irrigation.json")
        raw["unexpected_field"] = "drop me"
        cases.append(raw)
        for item in cases:
            with self.subTest(sample=item["streams"][0]), self.assertRaises((ValidationError, ValueError)):
                parse_domain(item)

    def test_nonchemical_domain_selection_and_incremental_update(self) -> None:
        """ProcessIR generates and revises without placeholder ChemicalIR."""
        context = {"scope": "Water distribution", "process_requirements": ["Connected transfer"],
                   "stream_requirements": ["Known source and destination"],
                   "evidence_requirements": ["Synthetic declared rates"]}
        self.assertTrue(ProcessContext.model_validate(context))
        plan = select_domains("irrigation", "Represent irrigation flowsheet",
                              scope=ProductScope(domains=["process"]),
                              contexts={"process": context})
        self.assertEqual(["process"], [task.domain for task in plan.tasks])
        raw = load("process-irrigation.json")
        authored = {key: value for key, value in raw.items()
                    if key not in {"domain", "schema_version", "artifact_id", "revision"}}
        product = generate_product(plan, fixture_author({"process": authored}))
        self.assertTrue(validate_product(product).is_valid)
        self.assertEqual(["process"], [r.domain for r in product.manifest.artifacts])
        updated = generate_product(plan, fixture_author({"process": authored}), existing=product,
                                   update_domains={"process"})
        self.assertEqual(2, updated.manifest.artifacts[0].revision)
        self.assertEqual(1, product.manifest.artifacts[0].revision)
        self.assertNotEqual(product.manifest.artifacts[0].sha256,
                            updated.manifest.artifacts[0].sha256)

    def test_chemical_reaction_binding_and_stale_link(self) -> None:
        """Process references chemical reactions/species through pinned DomainLinks."""
        from forma_core.chemistry.models import ChemicalIR

        chemical = ChemicalIR.model_validate(load("chemical-air-to-fuel.json"))
        process = ProcessIR.model_validate(load("process-air-to-fuel.json"))
        self.assertEqual([], validate_domain(process))
        self.assertEqual([], analyze_process(process).balance_results)
        self.assertEqual(0.6, process.scenarios[0].overrides[0].quantity.value)
        links = [
            DomainLink(
                link_id="electrolysis-reaction", kind="uses_reaction",
                source=LinkEndpoint(artifact_id=process.artifact_id, revision=1,
                                    object_id="electrolyzer"),
                target=LinkEndpoint(artifact_id=chemical.artifact_id, revision=1,
                                    object_id="electrolysis"),
                fact_owners={"chemical.reaction": "chemical"},
            ),
            DomainLink(
                link_id="hydrogen-species", kind="uses_species",
                source=LinkEndpoint(artifact_id=process.artifact_id, revision=1,
                                    object_id="hydrogen_stream"),
                target=LinkEndpoint(artifact_id=chemical.artifact_id, revision=1,
                                    object_id="hydrogen"),
                fact_owners={"chemical.species": "chemical"},
            ),
        ]
        product = compose_product("air-to-fuel", [process, chemical], links=links)
        self.assertTrue(validate_product(product).is_valid)
        bad = deepcopy(links)
        bad[0].target.object_id = "hydrogen"
        self.assertIn("chemical_link_type", [f.code for f in validate_product(
            compose_product("air-to-fuel", [process, chemical], links=bad)).composition])
        upgraded = chemical.model_copy(update={"revision": 2})
        stale = validate_product(compose_product("air-to-fuel", [process, upgraded], links=links))
        self.assertIn("link_revision", [f.code for f in stale.composition])
        self.assertEqual([], stale.local[process.artifact_id])

    def test_legacy_hardware_export_is_explicitly_lossy(self) -> None:
        """HardwareIR cannot silently absorb process design or material streams."""
        process = ProcessIR.model_validate(load("process-irrigation.json"))
        legacy = load("motor-hardware.json")
        product = compose_product("irrigation", [process])
        report = export_hardware_ir(product, legacy_seed=legacy)
        self.assertIsNone(report.payload)
        self.assertIn("unrepresentable", [finding.code for finding in report.findings])
        acknowledged = export_hardware_ir(product, legacy_seed=legacy, allow_lossy=True)
        self.assertIsNotNone(acknowledged.payload)
        self.assertIn("unrepresentable", [finding.code for finding in acknowledged.findings])
        self.assertIn(process.artifact_id, product.artifacts)


if __name__ == "__main__":
    unittest.main()

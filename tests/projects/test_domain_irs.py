"""Acceptance and adversarial fixtures for issue #20's independent domain API."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from forma_core.config import config

from forma_core.domains.agents import InferredProductScope, plan_with_agent, provider_author
from forma_core.domains.base import fingerprint
from forma_core.domains.compatibility import export_hardware_ir, import_hardware_ir
from forma_core.domains.composition import (
    DomainLink, LinkEndpoint, ProductBundle, compose_product, validate_product,
)
from forma_core.domains.generation import DeclaredInput, GenerationRequest, fixture_author, generate_product
from forma_core.domains.planning import ProductPlan, ProductScope, select_domains
from forma_core.domains.registry import domain_model, parse_domain, validate_domain
from forma_core.domains.storage import load_product, save_product
from forma_core.domains.tools import call_domain_tool

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "domains"


def read_json(path: Path) -> dict | list:
    """Read a checked-in offline fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_input() -> dict:
    """Load fresh independent generation inputs for each test."""
    return read_json(EXAMPLES / "motor-generation.json")


def motor_bundle() -> ProductBundle:
    """Exercise the public tool path to obtain a fully composed motor fixture."""
    return ProductBundle.model_validate(call_domain_tool("generate", fixture_input()))


def legacy_motor() -> dict:
    """Return the synthetic legacy fixture with explicit procurement identity."""
    return read_json(EXAMPLES / "motor-hardware.json")


class ContractTests(unittest.TestCase):
    """Domain imports, schema versions, local rules and shared identity boundaries."""

    def test_each_domain_imports_without_legacy_or_unrelated_domains(self) -> None:
        """Import and schema discovery never load unrelated IR contracts."""
        packages = {"mechanical": "mechanics.models", "electrical": "electronics.models",
                    "biological": "biology.ir", "fluidic": "fluidics.ir"}
        for domain, package in packages.items():
            with self.subTest(domain=domain):
                script = (
                    "import sys; from forma_core.domains.registry import domain_model; "
                    f"domain_model({domain!r}).model_json_schema(); "
                    "assert 'forma_core.workspaces.projects.models' not in sys.modules; "
                    + "; ".join(f"assert 'forma_core.{other}' not in sys.modules"
                                for other in packages.values() if other != package)
                )
                subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, capture_output=True)

    def test_reuses_exact_legacy_value_types(self) -> None:
        """Extraction leaves old import paths as identity-preserving aliases."""
        from forma_core.electronics.primitives import ConnectionNet
        from forma_core.mechanics.primitives import MechanicalNotes
        from forma_core.workspaces.projects import models
        self.assertIs(models.ConnectionNet, ConnectionNet)
        self.assertIs(models.MechanicalNotes, MechanicalNotes)

    def test_all_four_documents_round_trip_and_reject_future_versions(self) -> None:
        """Each independently serialized document owns a validated schema version."""
        from forma_core.biology.ir import BiologicalIR
        from forma_core.fluidics.ir import FluidicIR
        from forma_core.fluidics.examples import organ_on_chip_system
        biology = read_json(ROOT / "examples/organ-on-chip/liver-dili-biological.json")
        documents = [parse_domain(p) for p in motor_bundle().artifacts.values()]
        documents.extend([BiologicalIR(artifact_id="biology", experiment=biology),
                          FluidicIR(artifact_id="fluidics", system=organ_on_chip_system())])
        for document in documents:
            with self.subTest(domain=document.domain):
                payload = document.model_dump(mode="json")
                self.assertEqual(document, parse_domain(json.loads(document.model_dump_json())))
                self.assertEqual([], validate_domain(document))
                payload["schema_version"] = "999.0"
                self.assertTrue(validate_domain(payload))

    def test_local_electrical_identity_and_pin_corruption_blocks(self) -> None:
        """No unknown pin, duplicate instance, bus or power source passes local parsing."""
        payload = motor_bundle().artifacts["motor-example.electrical"]
        broken = deepcopy(payload)
        broken["nets"][0]["pins"][0]["pin_id"] = "NO_SUCH_PIN"
        self.assertTrue(validate_domain(broken))
        broken = deepcopy(payload)
        broken["components"].append(deepcopy(broken["components"][0]))
        self.assertTrue(validate_domain(broken))
        broken = deepcopy(payload)
        broken["buses"] = [{"bus_id": "BUS", "bus_type": "I2C", "nets": ["UNKNOWN"]}]
        self.assertTrue(validate_domain(broken))
        broken = deepcopy(payload)
        broken["power_rails"][0]["source_component"] = "UNKNOWN"
        self.assertTrue(validate_domain(broken))

    def test_existing_short_circuit_rule_runs_without_hardware_ir(self) -> None:
        """The new electrical validator preserves the existing low-voltage rule set."""
        payload = deepcopy(motor_bundle().artifacts["motor-example.electrical"])
        payload["nets"][0]["pins"].append({"ref_des": "M1", "pin_id": "GND"})
        self.assertIn("Short Circuit", {f.code for f in validate_domain(payload)})

    def test_unknown_nested_fields_are_not_silently_lost(self) -> None:
        """Legacy permissive leaf types cannot hide extra authored fields."""
        payload = deepcopy(motor_bundle().artifacts["motor-example.electrical"])
        payload["parts"][0]["pins"][0]["invented_voltage_limit"] = 99
        self.assertIn("Unsupported field", validate_domain(payload)[0].message)

    def test_mechanical_dangling_reference_is_local(self) -> None:
        """Mechanical authoring validates its own placements without electrical data."""
        payload = deepcopy(motor_bundle().artifacts["motor-example.mechanical"])
        payload["mechanical"]["component_placements"][0]["ref_des"] = "UNKNOWN"
        self.assertTrue(validate_domain(payload))
        payload = deepcopy(motor_bundle().artifacts["motor-example.mechanical"])
        payload["mechanical"]["component_placements"][0]["position"]["x_mm"] = float("nan")
        self.assertTrue(validate_domain(payload))


class SelectionGenerationTests(unittest.TestCase):
    """Scope selection and independent authoring, including missing-input isolation."""

    def test_issue_selection_fixtures(self) -> None:
        """All four product scopes select exactly the requested domain types, with reasons."""
        for fixture in read_json(EXAMPLES / "selection-fixtures.json"):
            with self.subTest(brief=fixture["brief"]):
                plan = select_domains("fixture", fixture["brief"], scope=ProductScope(**fixture["scope"]))
                self.assertEqual(fixture["expected"], [t.domain for t in plan.tasks])
                self.assertTrue(all(t.reason and t.missing_inputs and t.status == "blocked" for t in plan.tasks))

    def test_no_implicit_scope_guess_or_placeholder_artifacts(self) -> None:
        """Explicit scope is mandatory offline and an empty scope creates no artifacts."""
        with self.assertRaises(ValueError):
            select_domains("p", "ambiguous brief")
        plan = select_domains("p", "no hardware requested", scope=ProductScope())
        bundle = generate_product(plan, fixture_author({}))
        self.assertEqual({}, bundle.artifacts)
        self.assertEqual([], bundle.manifest.artifacts)

    def test_missing_electrical_context_does_not_block_mechanical(self) -> None:
        """A required electrical domain stays selected and blocked while mechanics succeeds."""
        request = fixture_input()
        request["plan"]["tasks"][1]["context"] = {}
        seen = []
        base = fixture_author(request["designs"])

        def author(value: GenerationRequest) -> dict:
            """Record the context boundary observed by each domain author."""
            seen.append(value)
            return base(value)

        bundle = generate_product(ProductPlan.model_validate(request["plan"]), author)
        self.assertEqual(["mechanical"], [r.domain for r in seen])
        self.assertNotIn("supply_requirements", seen[0].context)
        self.assertNotIn("brief", seen[0].model_dump())
        self.assertEqual(["mechanical"], [r.domain for r in bundle.manifest.artifacts])
        self.assertEqual("blocked", bundle.plan.tasks[1].status)
        self.assertIn("electrical", validate_product(bundle).blocked_domains)
        self.assertEqual([], validate_product(bundle).local["motor-example.mechanical"])

    def test_wrong_context_is_blocking_only_for_its_domain(self) -> None:
        """An electrical field cannot leak into an accepted mechanical context."""
        request = fixture_input()
        request["plan"]["tasks"][0]["context"]["supply_requirements"] = ["5 V"]
        result = call_domain_tool("generate", request)
        bundle = ProductBundle.model_validate(result)
        self.assertEqual(["electrical"], [r.domain for r in bundle.manifest.artifacts])
        self.assertEqual("blocked", bundle.plan.tasks[0].status)

    def test_failed_author_preserves_independent_success(self) -> None:
        """A provider failure does not discard a separately generated domain."""
        request = fixture_input()
        base = fixture_author(request["designs"])

        def author(value: GenerationRequest) -> dict:
            """Fail the electrical provider call after mechanics completes."""
            if value.domain == "electrical":
                raise RuntimeError("Electrical provider unavailable")
            return base(value)

        bundle = generate_product(ProductPlan.model_validate(request["plan"]), author)
        self.assertEqual(["mechanical"], [r.domain for r in bundle.manifest.artifacts])
        self.assertEqual("failed", bundle.plan.tasks[1].status)

    def test_declared_inputs_are_projected_and_revision_checked(self) -> None:
        """Only explicitly declared upstream fields are exposed to a dependent author."""
        request = fixture_input()
        base = fixture_author(request["designs"])
        seen = []

        def author(value: GenerationRequest) -> dict:
            """Record each projected request."""
            seen.append(value)
            return base(value)

        dependency = DeclaredInput(target_domain="electrical", field="parts", source=LinkEndpoint(
            artifact_id="motor-example.mechanical", object_id="M1", revision=1))
        bundle = generate_product(ProductPlan.model_validate(request["plan"]), author, inputs=[dependency])
        self.assertEqual(2, len(bundle.artifacts))
        self.assertEqual("parts", seen[1].cross_domain_inputs[0]["field"])
        self.assertNotIn("mechanical", seen[1].cross_domain_inputs[0])
        dependency.source.revision = 9
        blocked = generate_product(ProductPlan.model_validate(request["plan"]), author, inputs=[dependency])
        self.assertEqual("blocked", blocked.plan.tasks[1].status)

    def test_updates_only_requested_domain_and_preserves_other_bytes(self) -> None:
        """Mechanical edits leave the electrical artifact unchanged and stale links visible."""
        request = fixture_input()
        before = motor_bundle()
        request["designs"]["mechanical"]["parts"][0]["dimensions_mm"]["length"] = 21
        after = generate_product(before.plan, fixture_author(request["designs"]), existing=before,
                                 update_domains={"mechanical"})
        self.assertEqual(before.artifacts["motor-example.electrical"], after.artifacts["motor-example.electrical"])
        self.assertEqual(2, after.artifacts["motor-example.mechanical"]["revision"])
        self.assertIn("link_revision", {f.code for f in validate_product(after).composition})

    def test_agent_selection_and_generation_adapters(self) -> None:
        """Providers receive typed scope requests and one domain at a time."""
        request = fixture_input()

        class Provider:
            """Offline provider stub using the same method as Forma's real providers."""

            def __init__(self) -> None:
                """Track prompts for isolation assertions."""
                self.prompts: list[str] = []

            def generate_structured(self, prompt: str, schema_class: type) -> object:
                """Return explicit fixture decisions without a live model call."""
                self.prompts.append(prompt)
                if schema_class is InferredProductScope:
                    return InferredProductScope(**ProductScope(physical_structure=True).model_dump())
                data = request["designs"]["mechanical"]
                return schema_class(artifact_id="p.mechanical", **data)

        provider = Provider()
        plan = plan_with_agent(provider, "p", "A passive bracket; exclude wiring",
                               contexts={"mechanical": request["plan"]["tasks"][0]["context"]})
        result = generate_product(plan, provider_author(provider))
        self.assertEqual(["mechanical"], [ref.domain for ref in result.manifest.artifacts])
        self.assertNotIn("ElectricalIR", provider.prompts[-1])


class ExternalSelectionTests(unittest.TestCase):
    """Calling agents can complete the public workflow without any internal provider."""

    def test_handoff_is_discoverable_and_completed_without_provider(self) -> None:
        """Raw intake returns a contract; submitted decisions preserve independent blocking."""
        fixture = fixture_input()
        project = {"product_id": "motor-example", "brief": fixture["plan"]["brief"],
                   "contexts": {"mechanical": fixture["plan"]["tasks"][0]["context"]}}
        with patch("forma_core.domains.agents.configured_selector", side_effect=AssertionError("Provider called")):
            request = call_domain_tool("plan", project)
            self.assertEqual("awaiting_selection", request["status"])
            self.assertEqual("plan", request["submit_operation"])
            schema = request["decision_schema"]["$defs"]["CompleteProductScope"]
            self.assertEqual(set(ProductScope.model_fields) - {"domains"}, set(schema["required"]))
            self.assertTrue(all(field["description"] for field in schema["properties"].values()))
            self.assertEqual({"mechanical", "electrical", "biological", "fluidic", "chemical", "process", "thermal", "space"}, set(request["domains"]))
            with self.assertRaises(ValidationError):
                ProductPlan.model_validate(request)
            reasons = {"mechanical": "Mount and assemble the motor.", "electrical": "The brief includes motor wiring."}
            plan = call_domain_tool("plan", {**request["project"], "decision": {
                "request_sha256": request["request_sha256"], "scope": fixture["plan"]["scope"], "reasons": reasons,
            }})
            self.assertEqual(["ready", "blocked"], [task["status"] for task in plan["tasks"]])
            self.assertEqual(reasons, {task["domain"]: task["reason"] for task in plan["tasks"]})
            product = call_domain_tool("generate", {"plan": plan, "designs": fixture["designs"]})
            self.assertEqual(["mechanical"], [ref["domain"] for ref in product["manifest"]["artifacts"]])

    def test_changed_inputs_reject_stale_decisions(self) -> None:
        """Identity, brief and context updates all require the selecting agent to reconsider."""
        example = read_json(EXAMPLES / "bracket-selection.json")
        for key, value in [("product_id", "another"), ("brief", "Design the motor wiring."),
                           ("contexts", {"mechanical": {"scope": "Updated inputs"}})]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Selection inputs changed"):
                call_domain_tool("plan", {**example, key: value})

    def test_malformed_or_ambiguous_decisions_are_rejected(self) -> None:
        """Missing booleans, coerced truth values, bad reasons and mixed modes cannot slip through."""
        example = read_json(EXAMPLES / "bracket-selection.json")
        candidates = []
        incomplete = deepcopy(example)
        del incomplete["decision"]["scope"]["wiring"]
        candidates.append(incomplete)
        coerced = deepcopy(example)
        coerced["decision"]["scope"]["wiring"] = "false"
        candidates.append(coerced)
        for reasons in ({}, {"mechanical": "  "}, {"mechanical": "Bracket", "electrical": "No wiring"}):
            candidate = deepcopy(example)
            candidate["decision"]["reasons"] = reasons
            candidates.append(candidate)
        candidates.extend([{**example, "scope": {}}, {**example, "selection_mode": "provider"},
                           {**example, "provider": "simulation"}])
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                call_domain_tool("plan", candidate)

    def test_software_only_decision_has_no_domain_tasks(self) -> None:
        """An explicitly empty physical scope is a valid decision, not an inference failure."""
        request = call_domain_tool("plan", {"product_id": "software", "brief": "A software inventory dashboard."})
        plan = call_domain_tool("plan", {**request["project"], "decision": {
            "request_sha256": request["request_sha256"], "scope": ProductScope().model_dump(), "reasons": {},
        }})
        self.assertEqual([], plan["tasks"])

    def test_cli_external_round_trip_without_provider(self) -> None:
        """Separate CLI calls exchange portable JSON and accept the checked-in agent decision."""
        from forma_core.cli.main import main
        example = read_json(EXAMPLES / "bracket-selection.json")
        project = {key: value for key, value in example.items() if key != "decision"}
        with patch("forma_core.domains.agents.configured_selector", side_effect=AssertionError("Provider called")):
            prepared = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(project))), redirect_stdout(prepared):
                self.assertEqual(0, main(["domains", "plan", "-"]))
            request = json.loads(prepared.getvalue())
            self.assertEqual(example["decision"]["request_sha256"], request["request_sha256"])
            completed = StringIO()
            with redirect_stdout(completed):
                self.assertEqual(0, main(["domains", "plan", str(EXAMPLES / "bracket-selection.json")]))
            self.assertEqual(["mechanical"], [task["domain"] for task in json.loads(completed.getvalue())["tasks"]])

    def test_external_selection_does_not_import_provider_module(self) -> None:
        """The SDK path works even when provider configuration is unavailable."""
        script = (
            "import json, sys; from forma_core.domains.tools import call_domain_tool; "
            "call_domain_tool('plan', {'product_id': 'p', 'brief': 'A bracket'}); "
            "call_domain_tool('plan', json.load(open('examples/domains/bracket-selection.json'))); "
            "assert 'forma_core.llm_providers' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, capture_output=True)


class DescriptionSelectionTests(unittest.TestCase):
    """Public-flow tests with recorded responses; these are not live model evaluations."""

    def test_explicit_provider_mode_calls_selector(self) -> None:
        """Internal inference is available only when the caller explicitly requests it."""
        from unittest.mock import Mock
        provider = Mock()
        provider.generate_structured.return_value = InferredProductScope(
            **ProductScope(physical_structure=True, wiring=True).model_dump())
        brief = "Build a motorized assembly including mounting and wiring; supply ratings are unknown."
        with patch("forma_core.domains.agents.configured_selector", return_value=provider) as factory:
            result = call_domain_tool("plan", {"product_id": "motor", "brief": brief, "selection_mode": "provider"})
        factory.assert_called_once_with(None, None)
        provider.generate_structured.assert_called_once()
        prompt, response_type = provider.generate_structured.call_args.args
        self.assertIn(brief, prompt)
        self.assertIs(InferredProductScope, response_type)
        self.assertEqual(["mechanical", "electrical"], [task["domain"] for task in result["tasks"]])
        self.assertTrue(all(task["status"] == "blocked" for task in result["tasks"]))
        self.assertTrue(all(task["reason"] and task["missing_inputs"] for task in result["tasks"]))

    def test_explicit_provider_cli_uses_the_same_public_path(self) -> None:
        """JSON stdin and provider/model overrides work without a supplied scope."""
        from unittest.mock import Mock
        from forma_core.cli.main import main
        provider = Mock()
        provider.generate_structured.return_value = InferredProductScope(**ProductScope(physical_structure=True).model_dump())
        payload = {"product_id": "bracket", "brief": "A passive shelf bracket; no wiring.",
                   "selection_mode": "provider", "provider": "gmi", "model": "configured-test-model"}
        output = StringIO()
        with patch("sys.stdin", StringIO(json.dumps(payload))), redirect_stdout(output), patch(
            "forma_core.domains.agents.configured_selector", return_value=provider,
        ) as factory:
            self.assertEqual(0, main(["domains", "plan", "-"]))
        factory.assert_called_once_with("gmi", "configured-test-model")
        self.assertEqual(["mechanical"], [task["domain"] for task in json.loads(output.getvalue())["tasks"]])

    def test_explicit_scope_does_not_call_a_provider(self) -> None:
        """Existing offline clients retain a deterministic, network-free mapping path."""
        with patch("forma_core.domains.agents.configured_selector") as factory:
            result = call_domain_tool("plan", {"product_id": "bracket", "brief": "A passive bracket",
                                               "scope": {"physical_structure": True}})
        factory.assert_not_called()
        self.assertEqual(["mechanical"], [task["domain"] for task in result["tasks"]])

    def test_unconfigured_provider_is_reported_without_inference(self) -> None:
        """No live credentials means an explicit failure, never a fabricated selection."""
        from forma_core.domains.benchmark import main
        from forma_core.llm_providers import SimulationProvider
        with patch("forma_core.llm_providers.build_llm_provider", return_value=SimulationProvider()):
            with self.assertRaisesRegex(RuntimeError, "No inference was performed"):
                call_domain_tool("plan", {"product_id": "p", "brief": "Design a bracket", "selection_mode": "provider"})
            output = StringIO()
            with redirect_stdout(output):
                code = main([str(EXAMPLES / "selection-benchmark.json")])
            self.assertEqual(2, code)
            report = json.loads(output.getvalue())
            self.assertEqual("blocked", report["status"])
            self.assertFalse(report["inference_performed"])

    def test_inference_requires_every_scope_decision(self) -> None:
        """Malformed provider output cannot silently omit electrical or other domains."""
        from unittest.mock import Mock
        provider = Mock()
        provider.generate_structured.return_value = {"physical_structure": True}
        with self.assertRaises(ValidationError):
            plan_with_agent(provider, "p", "Design a bracket")

    def test_benchmark_does_not_leak_answers_and_detects_wrong_selection(self) -> None:
        """Recorded responses exercise scoring; expected labels are never passed to the model."""
        from unittest.mock import Mock
        from forma_core.domains.benchmark import SelectionBenchmark, evaluate_selection
        benchmark = SelectionBenchmark.model_validate_json((EXAMPLES / "selection-benchmark.json").read_text())
        provider = Mock()
        # Deliberately return the wrong empty selection for all physical cases.
        provider.generate_structured.return_value = InferredProductScope(**ProductScope().model_dump())
        report = evaluate_selection(provider, benchmark)
        self.assertEqual(8, provider.generate_structured.call_count)
        self.assertEqual(1, report["passed"])
        self.assertEqual("fail", report["status"])
        for call, case in zip(provider.generate_structured.call_args_list, benchmark.cases):
            prompt, schema = call.args
            self.assertIn(case.brief, prompt)
            self.assertNotIn("expected_domains", prompt)
            # Empty capability lists in the registry are not leaked benchmark labels.
            if case.expected_domains:
                self.assertNotIn(json.dumps(case.expected_domains), prompt)
            self.assertIs(InferredProductScope, schema)


class CompositionTests(unittest.TestCase):
    """Composition diagnostics never invalidate the independent local documents."""

    def test_broken_links_have_separate_diagnostics(self) -> None:
        """Objects, ports, revisions and authority conflicts are diagnosed at composition."""
        for field, value, code in [("object_id", "missing", "link_object"),
                                   ("port_id", "missing", "link_port"), ("revision", 999, "link_revision")]:
            with self.subTest(field=field):
                bundle = motor_bundle()
                setattr(bundle.manifest.links[0].target, field, value)
                result = validate_product(bundle)
                self.assertTrue(all(not issues for issues in result.local.values()))
                self.assertIn(code, {f.code for f in result.composition})
                self.assertFalse(result.is_valid)
        bundle = motor_bundle()
        bundle.manifest.links[0].fact_owners["pins"] = "mechanical"
        self.assertIn("fact_ownership", {f.code for f in validate_product(bundle).composition})

    def test_missing_shared_link_and_changed_content_are_diagnosed(self) -> None:
        """Shared IDs require explicit links and artifacts must match pinned digests."""
        bundle = motor_bundle()
        bundle.manifest.links = []
        self.assertIn("shared_link_missing", {f.code for f in validate_product(bundle).composition})
        bundle = motor_bundle()
        bundle.artifacts["motor-example.mechanical"]["parts"][0]["dimensions_mm"]["length"] = 22
        self.assertIn("artifact_hash", {f.code for f in validate_product(bundle).composition})

    def test_biological_links_do_not_require_fluidics_for_local_validity(self) -> None:
        """Biology reuses its existing local contract and diagnoses physical links separately."""
        from forma_core.biology.ir import BiologicalIR
        from forma_core.fluidics.ir import FluidicIR
        from forma_core.fluidics.examples import organ_on_chip_system
        bio = BiologicalIR(artifact_id="bio", experiment=read_json(ROOT / "examples/organ-on-chip/liver-dili-biological.json"))
        fluid = FluidicIR(artifact_id="fluid", system=organ_on_chip_system())
        alone = compose_product("chip", [bio])
        self.assertEqual([], validate_product(alone).local["bio"])
        self.assertIn("host_link_missing", {f.code for f in validate_product(alone).composition})
        links = [DomainLink(link_id=f"{c.id}.{host}", kind="hosted_by",
                            source=LinkEndpoint(artifact_id="bio", revision=1, object_id=c.id),
                            target=LinkEndpoint(artifact_id="fluid", revision=1, object_id=host))
                 for c in bio.experiment.compartments for host in c.fluidic_component_ids]
        self.assertTrue(validate_product(compose_product("chip", [bio, fluid], links=links)).is_valid)

    def test_spatial_links_do_not_silently_convert_units_or_frames(self) -> None:
        """Cross-frame geometry needs an explicit transform outside this first version."""
        bundle = motor_bundle()
        link = bundle.manifest.links[0].model_copy(deep=True)
        link.link_id = "spatial-check"
        link.kind = "spatial"
        bundle.manifest.links.append(link)
        self.assertIn("spatial_context", {f.code for f in validate_product(bundle).composition})
        link.source.units, link.target.units = "mm", "m"
        link.source.frame = link.target.frame = "assembly"
        self.assertIn("spatial_transform", {f.code for f in validate_product(bundle).composition})


class CompatibilityTests(unittest.TestCase):
    """Exact legacy round trips, visible losses, public export and persistence."""

    def test_existing_hardware_fixtures_round_trip_without_field_loss(self) -> None:
        """Representative mechanical, electrical and fluidic projects retain every field."""
        paths = [EXAMPLES / "motor-hardware.json", ROOT / "examples/organ-on-chip/two-channel.json",
                 ROOT / "examples/print_in_place_hinge.json", ROOT / "examples/spur_gear_pair.json"]
        for path in paths:
            with self.subTest(path=path.name):
                raw = read_json(path)
                raw["future_extension"] = {"keep": [1, 2, 3]}
                bundle = import_hardware_ir(raw, product_id="fixture")
                exported = export_hardware_ir(bundle)
                self.assertEqual(raw, exported.payload)
                self.assertIn("preserved_extension", {f.code for f in exported.findings})

    def test_legacy_quantities_preserve_expanded_identity_and_original_source(self) -> None:
        """The established 0.1 normalization is visible and the original record survives."""
        from tests.projects.test_component_instances import motor_payload
        raw = {"hardware_ir_version": "0.1", "components": [motor_payload(quantity=4)]}
        bundle = import_hardware_ir(raw, product_id="legacy")
        self.assertEqual(["M1", "M2", "M3", "M4"],
                         [c["object_id"] for c in bundle.artifacts["legacy.electrical"]["components"]])
        self.assertEqual(raw, export_hardware_ir(bundle).payload)
        self.assertIn("legacy_normalization", {f.code for f in bundle.compatibility.findings})

    def test_no_electrical_placeholder_for_mechanical_cad_fixture(self) -> None:
        """Empty legacy electrical defaults cannot force ElectricalIR into a passive product."""
        raw = {"mechanical": {"enclosure_type": "Solid", "mounting_guidance": "None",
                              "manufacturability_rating": "Easy", "cad_operations": [
                                  {"shape": "box", "size": {"x_mm": 30, "y_mm": 20, "z_mm": 3}}]}}
        bundle = import_hardware_ir(raw, product_id="bracket")
        self.assertEqual(["mechanical"], [r.domain for r in bundle.manifest.artifacts])

    def test_public_generation_export_preserves_downstream_contract(self) -> None:
        """Generate/export preserves net/BOM identity, wiring render and OpenCAD source."""
        from forma_core.utils import generate_mermaid_chart
        from forma_core.validation import validate_project
        from forma_core.workspaces.projects.cad_generation import _cad_source
        from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation
        bundle = motor_bundle()
        self.assertTrue(validate_product(bundle).is_valid)
        output = export_hardware_ir(bundle, legacy_seed=legacy_motor())
        self.assertIsNotNone(output.payload)
        old = HardwareIntermediateRepresentation.model_validate(legacy_motor())
        new = HardwareIntermediateRepresentation.model_validate(output.payload)
        self.assertEqual(old.bom, new.bom)
        self.assertEqual(old.nets, new.nets)
        self.assertEqual(_cad_source(old), _cad_source(new))
        self.assertEqual(generate_mermaid_chart(old), generate_mermaid_chart(new))
        self.assertEqual(validate_project(old), validate_project(new))
        self.assertFalse(new.is_valid)
        self.assertEqual("recheck_required", new.assembly_metadata["domain_export_validation"])

    @unittest.skipUnless(config.boolean("FORMA_CAD_RUN_INTEGRATION_TESTS"), "Native OpenCAD integration is opt-in.")
    def test_composed_product_compiles_with_native_opencad(self) -> None:
        """The representative product exports real STEP through the existing public CAD path."""
        from forma_core.workspaces.projects.cad_generation import ensure_native_cad_model
        from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation
        exported = export_hardware_ir(motor_bundle(), legacy_seed=legacy_motor())
        project = HardwareIntermediateRepresentation.model_validate(exported.payload)
        with tempfile.TemporaryDirectory() as directory, config.override({"FORMA_CAD_WORKSPACE": directory}):
            self.assertTrue(ensure_native_cad_model(project, project_id="domain-native-fixture", required=True,
                                                  authoring_agent="Domain IR native integration fixture"))
            steps = list(Path(directory).rglob("*.step"))
            self.assertTrue(steps)
            self.assertTrue(any("ISO-10303-21" in p.read_text(errors="ignore") for p in steps))

    def test_unrepresentable_biology_requires_explicit_loss_acknowledgement(self) -> None:
        """HardwareIR cannot silently absorb or drop standalone biological data."""
        from forma_core.biology.ir import BiologicalIR
        raw = read_json(ROOT / "examples/organ-on-chip/liver-dili-biological.json")
        for compartment in raw["compartments"]:
            compartment["fluidic_component_ids"] = []
        bundle = compose_product("bio", [BiologicalIR(artifact_id="bio", experiment=raw)])
        result = export_hardware_ir(bundle)
        self.assertIsNone(result.payload)
        self.assertIn("unrepresentable", {f.code for f in result.findings})
        self.assertIsNotNone(export_hardware_ir(bundle, allow_lossy=True).payload)

    def test_source_tampering_and_removed_domain_are_blocked(self) -> None:
        """Compatibility cannot quietly overwrite source provenance or retain removed views."""
        bundle = import_hardware_ir(legacy_motor(), product_id="motor")
        bundle.compatibility.source["constraints"] = ["tampered"]
        self.assertEqual("source_hash", export_hardware_ir(bundle).findings[0].code)
        bundle = import_hardware_ir(legacy_motor(), product_id="motor")
        bundle.artifacts.pop("motor.electrical")
        self.assertEqual("removed_domain", export_hardware_ir(bundle).findings[0].code)

    def test_absent_current_remains_unknown_and_shared_metadata_conflicts_block(self) -> None:
        """Adapters neither invent zero current nor silently overwrite shared part metadata."""
        raw = legacy_motor()
        raw.pop("estimated_current_draw_ma")
        imported = import_hardware_ir(raw, product_id="motor")
        self.assertIsNone(imported.artifacts["motor.electrical"]["estimated_current_draw_ma"])
        bundle = motor_bundle()
        document = parse_domain(bundle.artifacts["motor-example.electrical"])
        document.parts[0].part_number = "CHANGED"
        updated = compose_product("motor-example", [
            parse_domain(bundle.artifacts["motor-example.mechanical"]), document,
        ], links=bundle.manifest.links)
        result = export_hardware_ir(updated, legacy_seed=legacy_motor(), allow_lossy=True)
        self.assertIsNone(result.payload)
        self.assertIn("shared_fact_conflict", {f.code for f in result.findings})

    def test_persistence_requires_revision_digest_and_retains_snapshots(self) -> None:
        """Concurrent or stale writes cannot silently overwrite a product file."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product.json"
            bundle = motor_bundle()
            digest = save_product(bundle, path)
            self.assertEqual(bundle, load_product(path))
            with self.assertRaisesRegex(ValueError, "revision changed"):
                save_product(bundle, path)
            self.assertEqual(digest, save_product(bundle, path, expected_sha256=digest))
            self.assertTrue((Path(str(path) + ".revisions") / f"{digest}.json").exists())

    def test_cli_generate_validate_export_flow(self) -> None:
        """A new contributor can run the public composed-product path without an LLM."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product.json"
            generated = subprocess.run([sys.executable, "-m", "forma_core", "domains", "generate",
                                        str(EXAMPLES / "motor-generation.json"), "--output", str(path)],
                                       cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(0, generated.returncode, generated.stderr)
            checked = subprocess.run([sys.executable, "-m", "forma_core", "domains", "validate", str(path)],
                                     cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(0, checked.returncode, checked.stderr)
            self.assertTrue(json.loads(checked.stdout)["is_valid"])
            data = {"product": read_json(path), "legacy_seed": legacy_motor()}
            exported = subprocess.run([sys.executable, "-m", "forma_core", "domains", "export-hardware", "-"],
                                      input=json.dumps(data), cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(0, exported.returncode, exported.stderr)
            self.assertIsNotNone(json.loads(exported.stdout)["payload"])


if __name__ == "__main__":
    unittest.main()

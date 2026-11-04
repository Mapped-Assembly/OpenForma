"""Public generation integration with deterministic advisory providers, never live measurements."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from typing import Any

from pydantic import BaseModel

from forma_core.domains.base import fingerprint
from forma_core.domains.composition import ProductBundle, validate_product
from forma_core.domains.generation import (
    CompositionDraft, ContextDraft, EngineeringRequest, EngineeringResult, generate_engineering,
)
from forma_core.domains.registry import parse_domain
from forma_core.domains.selection import SelectionDecision, SelectionProject, prepare_selection
from forma_core.domains.tools import call_domain_tool

ROOT = Path(__file__).resolve().parents[2]


def chemistry_request() -> EngineeringRequest:
    """Read the reviewable offline chemistry design, not a model-produced experiment."""
    return EngineeringRequest.model_validate_json((ROOT / "examples/domains/chemistry-generation.json").read_text())


def decision_for(request: EngineeringRequest, scope: dict[str, Any], reasons: dict[str, str]) -> SelectionDecision:
    """Pin a caller decision to the exact current request and registry."""
    selection = prepare_selection(SelectionProject(product_id=request.product_id, brief=request.brief, contexts=request.contexts))
    return SelectionDecision(request_sha256=selection["request_sha256"], registry_sha256=selection["registry_sha256"],
                             scope=scope, reasons=reasons)


class ScriptedProvider:
    """Recorded advisory outputs isolate orchestration tests from network inference."""

    def __init__(self, fixture: EngineeringRequest) -> None:
        """Retain the supplied independent designs and capture every call boundary."""
        self.fixture = fixture
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate_structured(self, prompt: str, schema_class: type[BaseModel]) -> BaseModel:
        """Replay a selection/context/domain/link response under the requested schema."""
        data = json.loads(prompt.rsplit("\n", 1)[1])
        self.calls.append((schema_class.__name__, data))
        if schema_class is SelectionDecision:
            return self.fixture.decision.model_copy(update={"request_sha256": data["request_sha256"],
                                                            "registry_sha256": data["registry_sha256"]})
        if schema_class is ContextDraft:
            return ContextDraft(context=self.fixture.contexts[data["domain"]],
                                assumptions=self.fixture.assumptions.get(data["domain"], []),
                                unknowns=self.fixture.unknowns if data["domain"] == "chemical" else [])
        if schema_class is CompositionDraft:
            references = {r["artifact_id"]: r for r in data["product"]["manifest"]["artifacts"]}
            links = [link.model_copy(deep=True) for link in self.fixture.links or []]
            for link in links:
                for endpoint in (link.source, link.target):
                    endpoint.sha256 = references[endpoint.artifact_id]["sha256"]
            return CompositionDraft(links=links)
        return schema_class.model_validate({**self.fixture.designs[data["domain"]],
                                           "artifact_id": data["artifact_id"], "revision": data["revision"]})


class UnifiedGenerationTests(unittest.TestCase):
    """Natural language entry point, composition, failures and compatibility regressions."""

    def test_chemistry_natural_language_public_sdk(self) -> None:
        """SDK orchestration selects two domains and reports exact supported checks."""
        from forma_core.generation import generate_project_with_workflow
        fixture = chemistry_request()
        provider = ScriptedProvider(fixture)
        result = generate_project_with_workflow("default", fixture.brief, provider=provider,
                                               generation_metadata={"project_id": fixture.product_id})
        self.assertEqual(["chemical", "process"], [t.domain for t in result.product.plan.tasks])
        self.assertTrue(result.validation["is_valid"])
        self.assertEqual("partial", result.status)  # unresolved acidity/performance remain visible
        chemical = result.product.artifacts[fixture.product_id + ".chemical"]
        process = result.product.artifacts[fixture.product_id + ".process"]
        self.assertEqual(["NaHCO3", "C2H4O2", "C2H3NaO2", "H2O", "CO2"], [s["formula"] for s in chemical["species"]])
        self.assertEqual([(2.0, "g"), (50.0, "mL")], [(s["batch_quantity"]["value"], s["batch_quantity"]["unit"]) for s in process["streams"] if s.get("batch_quantity")])
        self.assertTrue(all(s["quantity"] is None for s in process["streams"]))
        balance = result.evaluations[0]["results"][0]
        self.assertEqual("pass", balance["status"])
        self.assertEqual({}, balance["residuals"])
        self.assertEqual("unsupported", result.evaluations[0]["results"][1]["status"])
        self.assertTrue(all(l.source.sha256 and l.target.sha256 for l in result.product.manifest.links))
        self.assertTrue(any(l.kind == "uses_reaction" for l in result.product.manifest.links))
        self.assertNotIn('"measured"', result.model_dump_json())
        self.assertTrue(result.assumptions["chemical"])
        self.assertTrue(result.unresolved)
        for ref in result.product.manifest.artifacts:
            payload = result.product.artifacts[ref.artifact_id]
            self.assertEqual(ref.sha256, fingerprint(payload))
            self.assertTrue(ref.provenance["context_sha256"])
            self.assertEqual(payload, parse_domain(payload).model_dump(mode="json"))
        self.assertEqual(result, EngineeringResult.model_validate_json(result.model_dump_json()))
        authored = [data for name, data in provider.calls if name.endswith("IR")]
        self.assertEqual(["chemical", "process"], [call["domain"] for call in authored])
        self.assertNotIn("process_requirements", authored[0]["context"])
        self.assertNotIn("chemical_requirements", authored[1]["context"])

    def test_bracket_and_motor_scope_regressions(self) -> None:
        """Passive geometry and requested wiring use the same public orchestration path."""
        from forma_core.generation import generate_project_with_workflow
        motor = json.loads((ROOT / "examples/domains/motor-generation.json").read_text())
        for mechanical_only in (True, False):
            scope = dict(chemistry_request().decision.scope.model_dump(), domains=[], physical_structure=True, wiring=not mechanical_only)
            contexts = {t["domain"]: t["context"] for t in motor["plan"]["tasks"] if t["domain"] == "mechanical" or not mechanical_only}
            designs = {k: v for k, v in motor["designs"].items() if k in contexts}
            if mechanical_only:
                designs["mechanical"] = {"parts": [{"part_definition_id": "bracket-part", "material": "steel", "dimensions_mm": {"width": 20}}], "components": [{"object_id": "bracket", "ref_des": "B1", "part_definition_id": "bracket-part"}]}
            request = EngineeringRequest(product_id="motor-example", brief="Design a passive bracket. An existing motor is outside wiring scope." if mechanical_only else "Design a motorized assembly including wiring.", contexts=contexts, designs=designs, links=[] if mechanical_only else motor["links"])
            request.decision = decision_for(request, scope, {d: "Requested geometry" if d == "mechanical" else "Explicitly requested wiring" for d in contexts})
            result = generate_project_with_workflow("default", request.brief, engineering_request=EngineeringRequest(product_id=request.product_id, brief=request.brief), provider=ScriptedProvider(request))
            self.assertEqual(set(contexts), {r.domain for r in result.product.manifest.artifacts})
            self.assertTrue(result.validation["is_valid"], result.validation)

    def test_missing_requirements_and_failed_domain_preserve_other_artifacts(self) -> None:
        """Missing process context and invalid chemistry remain local failures."""
        request = chemistry_request()
        request.contexts["process"] = {}
        request.decision = decision_for(request, request.decision.scope.model_dump(), request.decision.reasons)
        result = generate_engineering(request)
        self.assertEqual({"chemical"}, {r.domain for r in result.product.manifest.artifacts})
        self.assertIn("process", result.validation["blocked_domains"])
        request = chemistry_request()
        request.designs["chemical"]["reactions"][0]["products"][0]["coefficient"] = "2"
        result = generate_engineering(request)
        self.assertEqual({"process"}, {r.domain for r in result.product.manifest.artifacts})
        self.assertEqual("partial", result.status)

    def test_stale_links_after_scoped_revision(self) -> None:
        """An update keeps untouched chemistry byte-for-byte and diagnoses old process pins."""
        request = chemistry_request()
        before = generate_engineering(request).product
        request.existing = before
        request.update_domains = {"process"}
        request.links = None
        after = generate_engineering(request)
        self.assertEqual(before.artifacts[request.product_id + ".chemical"], after.product.artifacts[request.product_id + ".chemical"])
        self.assertEqual(2, after.product.artifacts[request.product_id + ".process"]["revision"])
        self.assertIn("link_revision", {f["code"] for f in after.validation["composition"]})
        self.assertEqual("partial", after.status)

    def test_invented_measurements_rejected(self) -> None:
        """A structurally valid measured claim cannot be invented by the author."""
        request = chemistry_request()
        request.designs["chemical"]["evidence"][0]["basis"] = "measured"
        result = generate_engineering(request)
        self.assertNotIn(request.product_id + ".chemical", result.product.artifacts)
        self.assertIn("experimental measurements", str(result.unresolved))

    def test_no_simulated_hardware_fallback(self) -> None:
        """Missing provider credentials cannot turn a chemical objective into a circuit."""
        from forma_core.generation import generate_project_with_workflow
        with self.assertRaisesRegex(ValueError, "configured provider"):
            generate_project_with_workflow("default", chemistry_request().brief, provider_name="simulation")

    def test_tool_and_cli_use_same_result_contract(self) -> None:
        """OpenCode's shared tool dispatch and the ordinary CLI both return portable reports."""
        request = chemistry_request()
        result = call_domain_tool("generate", request.model_dump(mode="json"))
        self.assertEqual("forma.engineering-result", result["format"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            run = subprocess.run([sys.executable, "-m", "forma_core.cli.main", "generate", request.brief,
                                  "--request-file", str(ROOT / "examples/domains/chemistry-generation.json"), "--output", str(output)],
                                 cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(1, run.returncode, run.stderr)  # partial report, not dropped output
            payload = json.loads(output.read_text())
            self.assertEqual(result["product"], payload["product"])

    def test_domain_only_import_does_not_load_hardware(self) -> None:
        """The portable control plane does not import unrelated legacy contracts."""
        script = "from forma_core.domains.generation import EngineeringRequest; import sys; assert 'forma_core.workspaces.projects.models' not in sys.modules"
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, capture_output=True)

    def test_batch_units_cannot_be_rates(self) -> None:
        """A finite amount is never mislabeled with a flow-rate unit."""
        request = chemistry_request()
        request.designs["process"]["streams"][0]["batch_quantity"]["unit"] = "g/s"
        result = generate_engineering(request)
        self.assertIn("process", result.validation["blocked_domains"])

    def test_api_builder_uses_domains_and_preserves_legacy_dispatch(self) -> None:
        """The existing HTTP/A2A builder needs no legacy/modern mode switch."""
        from apps.api import a2a
        from types import SimpleNamespace
        request = chemistry_request()
        with patch.object(a2a, "ensure_hosted_chat_enabled"), patch.object(a2a, "_resolve_owner_user_integrations", return_value=None):
            response = a2a.build_generation_response(request.brief, engineering_request=request.model_dump(mode="json"))
            self.assertEqual("forma.engineering-result", response["engineering_result"]["format"])
            self.assertNotIn("project_ir", response)
            with patch.object(a2a, "get_latest_project_revision", return_value=SimpleNamespace(state=object())), patch.object(a2a, "_build_legacy_generation_response", return_value={"legacy": True}) as legacy:
                self.assertEqual({"legacy": True}, a2a.build_generation_response("Revise existing hardware", project_id="old", owner_user_id="owner"))
                legacy.assert_called_once()

    def test_external_owner_authoring_and_failed_evaluation_remain_independent(self) -> None:
        """An advertised external adapter owns its schema, authoring and evaluation failures."""
        from tests.projects.test_external_domain_providers import SyntheticProvider
        from forma_core.domains.registry import create_registry
        registry = create_registry()
        external = SyntheticProvider()
        external.metadata.authoring = True
        external.metadata.evaluation = True
        registry.register(external.metadata, external, replace=True)
        request = EngineeringRequest(product_id="external-product", brief="Represent the existing site only.",
                                     contexts={"space": {"site_id": "lab"}}, designs={})
        selection = prepare_selection(SelectionProject(product_id=request.product_id, brief=request.brief, contexts=request.contexts), registry=registry)
        scope = chemistry_request().decision.scope.model_dump()
        scope["domains"] = ["space"]
        request.decision = SelectionDecision(request_sha256=selection["request_sha256"], registry_sha256=selection["registry_sha256"], scope=scope, reasons={"space": "Explicit site-only scope"})
        with patch.object(external, "author", return_value=external.reference(), create=True) as author, patch.object(external, "evaluate", side_effect=RuntimeError("evaluator unavailable"), create=True):
            result = generate_engineering(request, registry=registry)
        author.assert_called_once()
        self.assertEqual({}, result.product.artifacts)
        self.assertEqual("tests.space", result.product.manifest.artifacts[0].provider_id)
        self.assertTrue(result.validation["is_valid"])
        self.assertEqual("unknown", result.evaluations[0]["results"][0]["status"])
        self.assertEqual("partial", result.status)
        request.existing = result.product
        request.update_domains = {"space"}
        with patch.object(external, "author", side_effect=RuntimeError("owner offline")):
            failed_update = generate_engineering(request, registry=registry)
        self.assertEqual(result.product.manifest.artifacts, failed_update.product.manifest.artifacts)
        self.assertEqual("blocked", failed_update.product.plan.tasks[0].status)
        self.assertIn("previous pinned artifact retained", str(failed_update.unresolved))
        self.assertEqual("partial", failed_update.status)

    def test_legacy_consumers_reject_domain_documents_without_loss(self) -> None:
        """No permissive legacy parser or cloud transport may silently erase chemistry."""
        from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation
        from forma_core.config.compatibility import ensure_supported_hardware_ir_version
        result = generate_engineering(chemistry_request())
        for payload in (result.model_dump(mode="json"), result.product.model_dump(mode="json")):
            with self.assertRaises(ValueError):
                HardwareIntermediateRepresentation.model_validate(payload)
            with self.assertRaisesRegex(ValueError, "legacy HardwareIR transport"):
                ensure_supported_hardware_ir_version({"project_ir": payload})

    def test_process_v1_wire_format_and_batch_version_are_explicit(self) -> None:
        """Old process artifacts retain their wire shape; batch quantities require v1.1."""
        from forma_core.processes.models import ProcessIR
        old = ProcessIR(artifact_id="old", operations=[{"operation_id": "feed", "operation_type": "feed"}])
        payload = old.model_dump(mode="json")
        self.assertEqual(fingerprint(payload), fingerprint(parse_domain(payload)))
        request = chemistry_request()
        request.designs["process"]["schema_version"] = "1.0"
        result = generate_engineering(request)
        self.assertIn("schema_version 1.1", str(result.unresolved))

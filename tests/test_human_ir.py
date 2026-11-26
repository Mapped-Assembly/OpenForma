"""HumanIR schema, reference integrity and native domain discovery tests."""

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from forma_core.domains.registry import (
    create_registry, domain_model, entities, parse_domain, validate_domain,
)
from forma_core.humans.models import HumanContext, HumanIR


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "domains"


def fixture(name: str) -> dict:
    """Load a declarative standalone domain fixture."""
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class HumanIRTests(unittest.TestCase):
    """Verify representation and integrity, not physical performance."""

    def test_round_trip_and_registration(self) -> None:
        """Both fixtures parse through the shared registry and preserve identity."""
        self.assertIs(domain_model("human"), HumanIR)
        self.assertIs(domain_model("human", context=True), HumanContext)
        registry = create_registry()
        self.assertEqual(registry.metadata("human").ir_type, "HumanIR")
        self.assertFalse(registry.metadata("human").evaluation)
        for name in ("human-wearable.json", "human-workcell.json"):
            with self.subTest(name=name):
                raw = fixture(name)
                doc = parse_domain(raw)
                self.assertEqual(parse_domain(doc.model_dump(mode="json")), doc)
                self.assertEqual(validate_domain(raw), [])
                self.assertIsNone(doc.profiles[0].stature_m)

    def test_context_and_entity_ports(self) -> None:
        """The registry exposes real participant IDs and ports."""
        registry = create_registry()
        provider = registry.provider("human")
        self.assertEqual(provider.validate_context({
            "scope": "conceptual wearable",
            "participant_requirements": ["wearer"],
            "evidence_requirements": ["explicit unknowns"],
        }), [])
        self.assertTrue(provider.validate_context({"scope": "wearable"}))
        doc = parse_domain(fixture("human-wearable.json"))
        self.assertEqual(entities(doc)["wearer-1"],
                         {"skin-contact", "skin-heat", "local-controls"})

    def test_invalid_artifacts(self) -> None:
        """Invalid revisions, ports, topology, bounds and unknown fields fail."""
        base = fixture("human-workcell.json")
        cases = []
        data = copy.deepcopy(base)
        data["participants"][0]["profile_revision"] = 2
        cases.append(data)
        data = copy.deepcopy(base)
        data["participants"][0]["interfaces"][0]["segment_id"] = "missing"
        cases.append(data)
        data = copy.deepcopy(base)
        data["profiles"][0]["joints"].append({
            "joint_id": "cycle", "parent_segment_id": "hand",
            "child_segment_id": "upper-arm",
        })
        cases.append(data)
        data = copy.deepcopy(base)
        data["profiles"][0]["joints"][0].update(lower_limit_deg=90, upper_limit_deg=10)
        cases.append(data)
        data = copy.deepcopy(base)
        data["participants"][0]["interfaces"].append(
            copy.deepcopy(data["participants"][0]["interfaces"][0]))
        cases.append(data)
        data = copy.deepcopy(base)
        data["participants"][0]["constraints"] = [
            {"constraint_id": "bad", "quantity": "reach", "upper": -1}
        ]
        cases.append(data)
        data = copy.deepcopy(base)
        data["profiles"][0]["mass_kg"] = float("nan")
        cases.append(data)
        data = copy.deepcopy(base)
        data["schema_version"] = "2.0"
        cases.append(data)
        data = copy.deepcopy(base)
        data["profiles"][0]["unsupported"] = 1
        cases.append(data)
        for index, data in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(ValidationError):
                    parse_domain(data)
                self.assertTrue(validate_domain(data))


    def test_public_generation_and_revision_pins(self) -> None:
        """Run both authored examples through the existing engineering control plane."""
        from forma_core.domains.base import fingerprint
        from forma_core.domains.generation import EngineeringRequest, EngineeringResult, generate_engineering
        from forma_core.domains.selection import SelectionDecision, SelectionProject, prepare_selection
        from forma_core.domains.composition import validate_product

        for name in ("human-wearable.json", "human-workcell.json"):
            with self.subTest(name=name):
                raw = fixture(name)
                product_id = raw["artifact_id"]
                raw["artifact_id"] = product_id + ".human"
                request = EngineeringRequest(
                    product_id=product_id,
                    brief="Represent the declared physical human participant.",
                    contexts={"human": {
                        "scope": "conceptual representation",
                        "participant_requirements": ["human participant"],
                        "evidence_requirements": ["no fabricated measurements"],
                    }},
                    designs={"human": raw},
                    unknowns=["Human performance is not measured."],
                )
                selection = prepare_selection(SelectionProject(
                    product_id=request.product_id, brief=request.brief, contexts=request.contexts))
                request.decision = SelectionDecision(
                    request_sha256=selection["request_sha256"],
                    registry_sha256=selection["registry_sha256"],
                    scope={"physical_structure": False, "mechanical_motion": False,
                           "wiring": False, "electrical_control": False,
                           "fluid_network": False, "biological_experiment": False,
                           "domains": ["human"]},
                    reasons={"human": "Explicit human representation."},
                )
                result = generate_engineering(request)
                self.assertTrue(result.validation["is_valid"], result.model_dump_json())
                self.assertEqual(result.status, "partial")
                self.assertEqual([t.domain for t in result.product.plan.tasks], ["human"])
                ref, = result.product.manifest.artifacts
                self.assertEqual(ref.domain, "human")
                self.assertEqual(ref.sha256, fingerprint(result.product.artifacts[ref.artifact_id]))
                self.assertEqual(result.evaluations[0]["results"][0]["status"], "unsupported")
                self.assertTrue(validate_product(result.product).is_valid)
                self.assertEqual(EngineeringResult.model_validate_json(result.model_dump_json()), result)


if __name__ == "__main__":
    unittest.main()

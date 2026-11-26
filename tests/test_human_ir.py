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


if __name__ == "__main__":
    unittest.main()

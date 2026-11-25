"""Engineering regression tests use synthetic fixtures, never hardware qualification."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from examples.electronics.generic_axis import example
from forma_core.domains.base import fingerprint
from forma_core.domains.composition import compose_product, validate_product
from forma_core.domains.registry import validate_domain
from forma_core.electronics.contracts import VerificationEvidence
from forma_core.electronics.models import ElectricalIR
from forma_core.electronics.power import estimate_motor_history, power_budget
from forma_core.electronics.projections import engineering_bom
from forma_core.electronics.validation import design_digest, evidence_status, validate_runtime_bindings


class ElectronicsEngineeringTests(unittest.TestCase):
    """Exercise cross-references, unknown propagation, calculations and stale claims."""

    def setUp(self) -> None:
        """Create independent models for each mutation test."""
        self.ir, self.machine, self.history = example()

    def parse(self, payload: dict) -> ElectricalIR:
        """Revalidate mutable fixture edits at the public document boundary."""
        return ElectricalIR.model_validate(payload)

    def test_round_trip_and_composition(self) -> None:
        """Existing domain composition preserves new fields and runs engineering rules."""
        restored = ElectricalIR.model_validate_json(self.ir.model_dump_json())
        self.assertEqual(restored, self.ir)
        result = validate_product(compose_product("fixture", [restored]))
        self.assertTrue(result.is_valid, result)
        self.assertIn("harness_incomplete", {f.code for f in result.local[self.ir.artifact_id]})

    def test_references_and_harness_net_membership(self) -> None:
        """Unknown components, wrong-net pins and self wires are rejected."""
        for endpoint in ({"object_id": "missing", "pin_id": "+"},
                         {"object_id": "source-1", "pin_id": "-"},
                         {"object_id": "controller-1", "pin_id": "in"}):
            with self.subTest(endpoint=endpoint):
                payload = self.ir.model_dump()
                payload["harnesses"][0]["wires"][0]["start"] = endpoint
                with self.assertRaises(ValidationError):
                    self.parse(payload)

    def test_duplicate_ids_and_connector_pins(self) -> None:
        """Duplicate wire identities and absent connector pins cannot collapse silently."""
        payload = self.ir.model_dump()
        payload["harnesses"][0]["wires"] *= 2
        with self.assertRaises(ValidationError):
            self.parse(payload)
        payload = self.ir.model_dump()
        payload["parts"][0]["engineering"]["connectors"] = [{"connector_id": "J1", "pin_ids": ["missing"]}]
        with self.assertRaises(ValidationError):
            self.parse(payload)

    def test_voltage_protocol_and_terminal_conflicts(self) -> None:
        """Typed ratings and net membership are visible through validate_domain."""
        payload = self.ir.model_dump()
        payload["nets"][0]["voltage"] = 10
        payload["power_domains"][0]["voltage_v"] = 10
        payload["parts"][0]["pins"][0]["interface"] = "UART"
        payload["parts"][1]["pins"][0]["interface"] = "I2C"
        payload["nets"][1]["pins"].append({"ref_des": "PS1", "pin_id": "+"})
        codes = {f.code for f in validate_domain(payload)}
        self.assertTrue({"voltage_range", "protocol_mismatch", "multiple_nets"} <= codes)

    def test_nonfinite_and_invalid_ranges(self) -> None:
        """Reject nonphysical schema values before calculation."""
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value):
                payload = self.ir.model_dump()
                payload["power_domains"][0]["loads"][0]["on_power_w"] = value
                with self.assertRaises(ValidationError):
                    self.parse(payload)
        payload = self.ir.model_dump()
        payload["parts"][0]["pins"][0]["limits"]["voltage_range"] = {"minimum": 5, "maximum": 1}
        with self.assertRaises(ValidationError):
            self.parse(payload)

    def test_power_duty_and_unknown(self) -> None:
        """A missing consumer quantity is never counted as zero."""
        demand = power_budget(self.ir)["input"]
        self.assertEqual(demand.peak_w, 4)
        self.assertEqual(demand.average_w, 2.1)
        self.ir.power_domains[0].loads[0].duty_cycle = None
        demand = power_budget(self.ir)["input"]
        self.assertEqual(demand.peak_w, 4)
        self.assertIsNone(demand.average_w)
        self.ir.power_domains[0].loads[0].on_power_w = None
        self.assertIsNone(power_budget(self.ir)["input"].peak_w)

    def converter_payload(self) -> dict:
        """Replace controller envelope with a downstream converter load."""
        payload = self.ir.model_dump()
        payload["power_domains"][0]["loads"] = []
        payload["power_domains"].append({"domain_id": "output", "net_id": "motor-power",
            "source_object_id": "controller-1", "upstream_domain_id": "input",
            "conversion_efficiency": 0.8, "quiescent_power_w": 0.1,
            "loads": [{"object_id": "motor-1", "on_power_w": 4, "idle_power_w": 0, "duty_cycle": 0.5}]})
        return payload

    def test_converter_losses(self) -> None:
        """Account for converter losses once, including input-side quiescent demand."""
        ir = self.parse(self.converter_payload())
        self.assertAlmostEqual(power_budget(ir)["input"].peak_w, 5.1)
        self.assertAlmostEqual(power_budget(ir)["input"].average_w, 2.6)
        ir.power_domains[1].conversion_efficiency = None
        self.assertIsNone(power_budget(ir)["input"].peak_w)

    def test_power_cycles_and_double_count(self) -> None:
        """Reject cyclic upstream links, duplicate loads and derived-load double count."""
        payload = self.converter_payload()
        payload["power_domains"][0]["loads"] = self.ir.model_dump()["power_domains"][0]["loads"]
        with self.assertRaisesRegex(ValidationError, "do not also list"):
            self.parse(payload)
        payload = self.converter_payload()
        payload["power_domains"][1]["upstream_domain_id"] = "output"
        with self.assertRaises(ValidationError):
            self.parse(payload)
        payload = self.ir.model_dump()
        payload["power_domains"][0]["loads"] *= 2
        with self.assertRaises(ValidationError):
            self.parse(payload)

    def test_overload(self) -> None:
        """Source capacity comparison uses simultaneous peak, not duty-reduced average."""
        self.ir.power_domains[0].current_limit_a = 0.5
        self.ir.power_domains[0].loads[0].duty_cycle = None
        self.assertIn("power_overload", {f.code for f in validate_domain(self.ir)})

    def test_motor_hand_calculation(self) -> None:
        """1.1 A at 1.5 V / 80%, followed by three seconds at 0.0125 W."""
        result = estimate_motor_history(self.ir, self.history)
        self.assertAlmostEqual(result.peak_motor_current_a, 1.1)
        self.assertAlmostEqual(result.peak_w, 2.0625)
        self.assertAlmostEqual(result.average_w, 0.525)
        self.assertAlmostEqual(result.copper_loss_average_w, 0.31)
        self.assertEqual(result.limit_violations, [])
        self.assertEqual(evidence_status(self.ir, "electrical"), "unknown")

    def test_stall_has_copper_loss(self) -> None:
        """Zero mechanical power must not produce zero electrical demand at stall."""
        self.history.samples[0].speed_rad_s = 0
        result = estimate_motor_history(self.ir, self.history)
        self.assertGreater(result.peak_w, 1)
        self.assertAlmostEqual(result.peak_motor_current_a, 1.1)

    def test_unknown_constants_and_regeneration(self) -> None:
        """Unsourced constants and negative mechanical power block the estimate."""
        self.ir.parts[2].engineering.motor.source = None
        self.history.design_sha256 = design_digest(self.ir)
        result = estimate_motor_history(self.ir, self.history)
        self.assertIsNone(result.peak_w)
        self.assertIn("motor.source", result.unknowns)
        ir, _, history = example()
        history.samples[0].speed_rad_s = -2
        self.assertIn("regenerative/braking model", estimate_motor_history(ir, history).unknowns)

    def test_limits_and_history_binding(self) -> None:
        """Reject stale motion histories and surface infeasible demand without clipping."""
        self.history.samples[0].torque_nm = 2
        result = estimate_motor_history(self.ir, self.history)
        self.assertTrue({"motor_current", "bus_current", "required_motor_voltage"} <= set(result.limit_violations))
        self.ir.revision += 1
        with self.assertRaises(ValueError):
            estimate_motor_history(self.ir, self.history)

    def add_evidence(self, discipline: str = "dynamics") -> None:
        """Attach a synthetic scoped claim for stale-evidence regression tests."""
        self.ir.evidence.append(VerificationEvidence(evidence_id="fixture", discipline=discipline, status="pass",
            design_sha256=design_digest(self.ir), component_revisions={c.object_id: "fixture-v1" for c in self.ir.components},
            source={"uri": "fixture:test", "sha256": fingerprint("test")}, method="synthetic unit test", scope="fixture only"))

    def test_evidence_independence_and_staleness(self) -> None:
        """Dynamics does not verify electricity; even an unversioned fact edit stales evidence."""
        self.add_evidence()
        self.assertEqual(evidence_status(self.ir, "dynamics"), "pass")
        self.assertEqual(evidence_status(self.ir, "electrical"), "unknown")
        self.ir.parts[0].engineering.mass_kg = 1
        self.assertEqual(evidence_status(self.ir, "dynamics"), "unknown")

    def test_external_model_evidence_requires_current_digest(self) -> None:
        """A changed or unavailable simulation model cannot retain a passing claim."""
        self.add_evidence()
        self.ir.evidence[0].model_sha256 = {"simulation": fingerprint("model-v1")}
        self.assertEqual(evidence_status(self.ir, "dynamics"), "unknown")
        self.assertEqual(evidence_status(self.ir, "dynamics", model_sha256={"simulation": fingerprint("model-v1")}), "pass")
        self.assertEqual(evidence_status(self.ir, "dynamics", model_sha256={"simulation": fingerprint("model-v2")}), "unknown")

    def test_evidence_unknown_revision_or_partial_coverage(self) -> None:
        """No wildcard revisions or whole-system pass from partial component coverage."""
        self.add_evidence()
        payload = self.ir.model_dump()
        payload["evidence"][0]["component_revisions"]["motor-1"] = None
        with self.assertRaises(ValidationError):
            self.parse(payload)
        del self.ir.evidence[0].component_revisions["motor-1"]
        self.assertEqual(evidence_status(self.ir, "dynamics"), "unknown")

    def test_runtime_pin_and_provenance(self) -> None:
        """Changes to commands/limits invalidate the binding to runtime configuration."""
        self.assertEqual(validate_runtime_bindings(self.ir, self.machine), [])
        self.machine.axes[0].maximum = 180
        self.assertIn("runtime_binding", {f.code for f in validate_runtime_bindings(self.ir, self.machine)})
        self.ir.runtime_bindings[0].feedback = "measured"
        self.assertIn("feedback_provenance", {f.code for f in validate_runtime_bindings(self.ir, self.machine)})

    def test_source_terminal_limit_and_conflicting_voltage(self) -> None:
        """A generous source budget cannot override a lower terminal rating."""
        self.ir.parts[0].pins[0].limits.current_limit_a = 0.1
        self.assertIn("source_pin_overload", {f.code for f in validate_domain(self.ir)})
        payload = self.ir.model_dump()
        payload["nets"][0]["voltage"] = 3.3
        with self.assertRaisesRegex(ValidationError, "conflicts"):
            self.parse(payload)

    def test_mutated_calculation_inputs_are_revalidated(self) -> None:
        """Mutable documents cannot bypass cycle or finite-value checks."""
        self.ir.power_domains[0].upstream_domain_id = "input"
        with self.assertRaises(ValidationError):
            power_budget(self.ir)
        ir, _, history = example()
        history.samples[0].duration_s = float("nan")
        with self.assertRaises(ValidationError):
            estimate_motor_history(ir, history)

    def test_legacy_export_reports_engineering_loss(self) -> None:
        """Engineering metadata must not disappear through legacy export."""
        from forma_core.domains.compatibility import export_hardware_ir
        from tests.projects.test_domain_irs import legacy_motor, motor_bundle
        bundle = motor_bundle()
        documents = [ElectricalIR.model_validate(p) for p in bundle.artifacts.values() if p["domain"] == "electrical"]
        payload = documents[0].model_dump()
        payload["parts"][0]["engineering"] = {"revision": "A"}
        ir = self.parse(payload)
        bundle.artifacts[ir.artifact_id] = ir.model_dump(mode="json")
        next(r for r in bundle.manifest.artifacts if r.artifact_id == ir.artifact_id).sha256 = fingerprint(bundle.artifacts[ir.artifact_id])
        result = export_hardware_ir(bundle, legacy_seed=legacy_motor())
        self.assertIsNone(result.payload)
        self.assertIn("unrepresentable", {f.code for f in result.findings})
        self.assertIsNotNone(export_hardware_ir(bundle, legacy_seed=legacy_motor(), allow_lossy=True).payload)

    def test_inventory_aggregation(self) -> None:
        """Repeated physical instances share a single definition and BOM grouping."""
        payload = self.ir.model_dump()
        payload["components"].append({"object_id": "motor-2", "ref_des": "M2", "part_definition_id": "motor"})
        ir = self.parse(payload)
        row = next(row for row in engineering_bom(ir) if row.part_definition_id == "motor")
        self.assertEqual(row.quantity, 2)
        self.assertEqual(row.object_ids, ["motor-1", "motor-2"])
        self.assertNotIn("part_number", row.model_dump())


if __name__ == "__main__":
    unittest.main()

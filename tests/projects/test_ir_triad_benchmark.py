"""Benchmark #24: reproducible checks for the three newly merged domain IRs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from forma_core.benchmarks.ir_triad_v1 import GROUPS, run_benchmark


class EngineeringTriadBenchmarkTests(unittest.TestCase):
    """The benchmark must enforce diagnostic distinctions and stable evidence."""

    def test_full_benchmark_matches_all_expected_outcomes(self) -> None:
        """Run actual ChemicalIR, ProcessIR, ThermalIR and composition validators."""
        report = run_benchmark()
        self.assertTrue(report["summary"]["all_expected_diagnostics_matched"], [
            item for item in report["cases"] if not item["passed"]
        ])
        self.assertEqual(report["summary"]["total"], report["summary"]["passed"])
        self.assertGreaterEqual(report["summary"]["total"], 25)
        self.assertEqual(set(GROUPS), set(report["summary"]["by_group"]))
        self.assertTrue(all(report["summary"]["by_group"][g]["total"] >= 6 for g in GROUPS))
        self.assertEqual(["chemical", "process", "thermal"], report["priority_domains"])

    def test_expected_unknown_and_failure_are_not_physical_success(self) -> None:
        """Correct reporting includes refusals; it is not reaction/thermal certification."""
        report = run_benchmark()
        expected = {item["case_id"]: item["observed"] for item in report["cases"]}
        self.assertEqual("fail", expected["chemical.unbalanced-detected"])
        self.assertEqual("unknown", expected["chemical.charge-unknown"])
        self.assertEqual("unsupported", expected["chemical.unsupported-formula"])
        self.assertEqual("fail", expected["process.mass-residual-detected"])
        self.assertEqual("unknown", expected["process.missing-rate"])
        self.assertEqual("unknown", expected["process.conceptual-throughput"])
        self.assertEqual("unknown", expected["thermal.reactor-unknown"])
        self.assertEqual("unsupported", expected["thermal.unsupported-network"])
        self.assertEqual("detected", expected["integration.stale-chemical-revision"])
        self.assertEqual("detected", expected["integration.wrong-reaction-entity"])
        self.assertEqual("detected", expected["integration.missing-heat-provenance"])
        self.assertEqual("not evaluated", report["summary"]["real_world_performance"])
        self.assertEqual("not evaluated", report["summary"]["physical_safety"])
        self.assertEqual("unknown", report["summary"]["chemical_yield"])
        self.assertEqual("unknown", report["summary"]["real_reactor_heat_duty"])

    def test_report_is_byte_deterministic(self) -> None:
        """No time, random seed, agent response, or environment-dependent fields."""
        first = run_benchmark()
        second = run_benchmark()
        self.assertEqual(first, second)
        self.assertEqual(len(first["cases"]), len({
            result["case_id"] for result in first["cases"]
        }))
        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )

    def test_python_module_cli_emits_same_report(self) -> None:
        """CLI is a reproducible user entrypoint and writes a JSON artifact."""
        with TemporaryDirectory() as td:
            output = Path(td) / "report.json"
            command = [
                sys.executable, "-m", "forma_core.benchmarks.ir_triad_v1",
                "--output", str(output),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(run_benchmark(), json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()

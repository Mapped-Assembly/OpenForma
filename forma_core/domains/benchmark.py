"""Live project-description selection benchmark; expected answers never enter prompts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import Field

from .agents import StructuredProvider, configured_selector, plan_with_agent
from .base import Domain, DomainModel, Text


class SelectionCase(DomainModel):
    """A description and independently specified expected IR subset."""

    case_id: Text
    brief: Text
    expected_domains: list[Domain]


class SelectionBenchmark(DomainModel):
    """Versioned cases for real selector evaluation, not supplied-scope mapping."""

    schema_version: Literal["1.0"] = "1.0"
    cases: list[SelectionCase] = Field(min_length=1)


def evaluate_selection(provider: StructuredProvider, benchmark: SelectionBenchmark) -> dict[str, Any]:
    """Evaluate project text without passing expected domains or a scope to the selector.

    A fake provider can test this harness, but that is not evidence of live model
    accuracy. The command-line entry point always requires a real provider.
    """
    results = []
    for case in benchmark.cases:
        try:
            plan = plan_with_agent(provider, case.case_id, case.brief)
            selected = [task.domain for task in plan.tasks]
            results.append({
                "case_id": case.case_id, "brief": case.brief,
                "status": "pass" if set(selected) == set(case.expected_domains) else "fail",
                "selected_domains": selected, "expected_domains": case.expected_domains,
                "reasons": {task.domain: task.reason for task in plan.tasks},
                "missing_context": {task.domain: task.missing_inputs for task in plan.tasks},
            })
        except Exception as exc:
            # Provider/schema failures are recorded, never counted as empty selections.
            results.append({"case_id": case.case_id, "brief": case.brief, "status": "error",
                            "error_type": type(exc).__name__, "expected_domains": case.expected_domains})
    passed = sum(case["status"] == "pass" for case in results)
    return {"status": "pass" if passed == len(results) else "fail", "passed": passed,
            "total": len(results), "results": results}


def main(argv: list[str] | None = None) -> int:
    """Run a live benchmark or explicitly report why no inference could be performed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", help="Selection benchmark JSON file.")
    parser.add_argument("--provider", help="Existing Forma live provider selector.")
    parser.add_argument("--model", help="Provider model selector.")
    parser.add_argument("--output", help="Write benchmark JSON to this file instead of stdout.")
    args = parser.parse_args(argv)
    try:
        benchmark = SelectionBenchmark.model_validate_json(Path(args.cases).read_text(encoding="utf-8"))
        provider = configured_selector(args.provider, args.model)
    except (OSError, ValueError, RuntimeError) as exc:
        report = {"status": "blocked", "inference_performed": False, "reason": str(exc)}
        code = 2
    else:
        report = {**evaluate_selection(provider, benchmark), "inference_performed": True,
                  "provider": getattr(provider, "provider_name", args.provider),
                  "model": getattr(provider, "model_name", args.model)}
        code = 0 if report["status"] == "pass" else 1
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

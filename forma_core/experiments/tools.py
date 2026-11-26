"""One shared operation for Python, CLI, REST and authenticated MCP callers."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Literal

from pydantic import Field

from forma_core.domains.base import DomainModel
from forma_core.domains.providers import DomainRegistry
from forma_core.domains.registry import get_registry
from .evaluation import compare_experiment, prepare_experiment
from .models import ExperimentPlan, ExperimentRequest, ExperimentRun


class ComparisonInput(DomainModel):
    """Portable original plan and independent trials."""

    plan: ExperimentPlan
    runs: list[ExperimentRun] = Field(default_factory=list)


class ExperimentToolInput(DomainModel):
    """Explicit action boundary; malformed and ignored arguments are rejected."""

    action: Literal["schema", "catalog", "benchmark", "prepare", "compare"]
    benchmark_id: str | None = None
    request: ExperimentRequest | None = None
    comparison: ComparisonInput | None = None


def experiment_tool(payload: dict[str, Any], *, registry: DomainRegistry | None = None) -> dict[str, Any]:
    """Dispatch pure scientific operations; persistence belongs to the authenticated host."""
    request = ExperimentToolInput.model_validate(payload)
    registry = get_registry(registry)
    if request.action != "benchmark" and request.benchmark_id is not None:
        raise ValueError("benchmark_id is only accepted by the benchmark action.")
    if request.action in {"schema", "catalog", "benchmark"}:
        if request.request is not None or request.comparison is not None:
            raise ValueError("Schema/benchmark accepts only action.")
        if request.action == "catalog":
            benchmarks = []
            for resource in sorted(files('forma_core.experiments').iterdir(), key=lambda r: r.name):
                if resource.name.endswith('.json'):
                    value = json.loads(resource.read_text())
                    benchmarks.append({"benchmark_id": resource.name[:-5], "objective": value["objective"],
                        "domains": sorted({a["reference"]["domain"] for a in value.get("artifacts", [])} |
                                          {"process"} | ({"chemical"} if "chemical" in value else set())),
                        "declarative": bool(value.get("evaluations"))})
            return {"evaluators": registry.evaluator_catalog(), "benchmarks": benchmarks}
        if request.action == "schema":
            return {"authoring_contract": "For new workflows declare process, artifacts, evaluations, required_inputs and criteria.target. Discover evaluator IDs, versions, parameter schemas and named outputs with catalog. Do not supply legacy chemical/calculation fields or write per-experiment code.",
                    "request": ExperimentRequest.model_json_schema(), "run": ExperimentRun.model_json_schema(),
                    "comparison": ComparisonInput.model_json_schema(),
                    "evaluators": registry.evaluator_catalog(),
                    "limitations": ["Only registered versioned models run; unavailable capabilities remain explicit."]}
        # Preserve the original no-ID call for clients of the initial PR.
        name = (request.benchmark_id or 'vinegar-bicarbonate') + '.json'
        resources = {r.name: r for r in files('forma_core.experiments').iterdir() if r.name.endswith('.json')}
        if name not in resources:
            raise ValueError("Unknown benchmark ID; discover choices with catalog.")
        return json.loads(resources[name].read_text())
    if request.action == "prepare" and request.request is not None and request.comparison is None:
        return prepare_experiment(request.request, registry=registry).model_dump(mode="json")
    if request.action == "compare" and request.comparison is not None and request.request is None:
        return compare_experiment(request.comparison.plan, request.comparison.runs, registry=registry)
    raise ValueError("Prepare requires only request; compare requires only comparison.")

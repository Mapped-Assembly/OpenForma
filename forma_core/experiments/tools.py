"""One shared operation for Python, CLI, REST and authenticated MCP callers."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Literal

from pydantic import Field

from forma_core.domains.base import DomainModel
from .evaluation import compare_experiment, prepare_experiment
from .models import ExperimentPlan, ExperimentRequest, ExperimentRun


class ComparisonInput(DomainModel):
    """Portable original plan and independent trials."""

    plan: ExperimentPlan
    runs: list[ExperimentRun] = Field(default_factory=list)


class ExperimentToolInput(DomainModel):
    """Explicit action boundary; malformed and ignored arguments are rejected."""

    action: Literal["schema", "benchmark", "prepare", "compare"]
    request: ExperimentRequest | None = None
    comparison: ComparisonInput | None = None


def experiment_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch pure scientific operations; persistence belongs to the authenticated host."""
    request = ExperimentToolInput.model_validate(payload)
    if request.action in {"schema", "benchmark"}:
        if request.request is not None or request.comparison is not None:
            raise ValueError("Schema/benchmark accepts only action.")
        if request.action == "schema":
            return {"request": ExperimentRequest.model_json_schema(), "run": ExperimentRun.model_json_schema(),
                    "comparison": ComparisonInput.model_json_schema(),
                    "limitations": ["Single-reaction stoichiometric reference; no kinetics or energy solver."]}
        return json.loads(files('forma_core.experiments').joinpath('vinegar-bicarbonate.json').read_text())
    if request.action == "prepare" and request.request is not None and request.comparison is None:
        return prepare_experiment(request.request).model_dump(mode="json")
    if request.action == "compare" and request.comparison is not None and request.request is None:
        return compare_experiment(request.comparison.plan, request.comparison.runs)
    raise ValueError("Prepare requires only request; compare requires only comparison.")

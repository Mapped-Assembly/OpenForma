"""Bounded kinetic discovery/evaluation on the existing domains tool surface."""

from __future__ import annotations

from typing import Any, Literal

from forma_core.domains.base import DomainModel
from .kinetics import (
    MODEL, MODEL_VERSION, RATE_TO_PER_SECOND, TIME_TO_SECONDS, VOLUME_TO_LITRES,
    KineticsRequest, KineticsResult, evaluate_kinetics,
)
from .models import ChemicalIR


class KineticEvaluationInput(DomainModel):
    """Portable, self-contained evaluation input accepted by Python, CLI and MCP."""

    action: Literal["evaluate"]
    document: ChemicalIR
    request: KineticsRequest


def kinetics_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Discover the input/result contract or run the explicitly parameterized model."""
    if payload.get("action") == "schema":
        if set(payload) != {"action"}:
            raise ValueError("Kinetics schema discovery accepts only action.")
        return {
            "model": MODEL, "model_version": MODEL_VERSION, "domain": "chemical",
            "rate_law": "-d[A]/dt = k[A]", "input": KineticEvaluationInput.model_json_schema(),
            "result": KineticsResult.model_json_schema(),
            "supported_units": {"time": list(TIME_TO_SECONDS), "rate_constant": list(RATE_TO_PER_SECOND),
                                "volume": list(VOLUME_TO_LITRES), "temperature": ["K", "degC"]},
            "limitations": [
                "One net consumed species; irreversible isothermal constant-volume batch without competing reactions.",
                "Supplied k requires provenance and a reference temperature matching operating and pinned reaction conditions.",
                "No inferred reaction order, parameter fitting, equilibrium or temperature correction.",
                "Prediction is distinct from a theoretical bound or measured performance; no uncertainty interval is inferred.",
                "Binary64 exponential with explicit precision-limit findings; exact rational species accounting thereafter.",
            ],
        }
    request = KineticEvaluationInput.model_validate(payload)
    return evaluate_kinetics(request.document, request.request).model_dump(mode="json")

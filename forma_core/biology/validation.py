"""Deterministic cross-system checks for biological experiment metadata."""

from __future__ import annotations

from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from forma_core.fluidics.models import FluidicSystem

from .models import BiologicalExperiment


class BiologicalIssue(BaseModel):
    """Machine-readable finding from biological experiment validation."""

    model_config = ConfigDict(extra="forbid")

    code: str
    entity_id: str
    message: str
    severity: Literal["warning", "error"] = "error"


def validate_biological_experiment(
    experiment: BiologicalExperiment,
    fluidic_system: FluidicSystem | None = None,
) -> list[BiologicalIssue]:
    """Validate biological-to-physical references without inferring biological outcomes.

    ``BiologicalExperiment`` validates its own internal references at parse time.
    This function checks the application boundary: biological compartments may
    reference the physical channels/chambers that host or expose them. It does
    not score toxicity, infer safety, or simulate biological response.
    """

    issues: list[BiologicalIssue] = []
    physical_refs = {
        component_id
        for compartment in experiment.compartments
        for component_id in compartment.fluidic_component_ids
    }
    if not physical_refs:
        return issues

    if fluidic_system is None:
        return [BiologicalIssue(
            code="fluidic_context_missing",
            entity_id=experiment.id,
            message=(
                "Biological compartments reference fluidic components, but no FluidicSystem "
                "was supplied for cross-system validation."
            ),
        )]

    host_ids = {channel.id for channel in fluidic_system.channels} | {
        chamber.id for chamber in fluidic_system.chambers
    }
    for compartment in experiment.compartments:
        for component_id in compartment.fluidic_component_ids:
            if component_id not in host_ids:
                issues.append(BiologicalIssue(
                    code="unknown_fluidic_component",
                    entity_id=compartment.id,
                    message=(
                        f"Biological compartment '{compartment.id}' references unknown or unsupported "
                        f"fluidic host '{component_id}'. Expected a declared channel or chamber ID."
                    ),
                ))

    return issues

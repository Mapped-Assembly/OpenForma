"""Closed, adiabatic equilibrium of constant-capacity bodies; no rate prediction."""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from pydantic import Field, model_validator

from forma_core.domains.base import DomainModel, Text, unique
from .models import ThermalIR


class EquilibriumRequest(DomainModel):
    """Declare contacting nodes and the assumptions required for sensible-heat accounting."""

    node_ids: list[Text] = Field(min_length=2)
    closed_adiabatic: bool | None = None
    constant_capacities: bool | None = None
    no_phase_change: bool | None = None
    no_reactions: bool | None = None

    @model_validator(mode='after')
    def distinct_nodes(self) -> EquilibriumRequest:
        """Reject duplicate bodies rather than double-counting their energy."""
        unique(self.node_ids, 'equilibrium node ID')
        return self


def evaluate_equilibrium(document: ThermalIR, request: EquilibriumRequest) -> dict[str, Any]:
    """Solve Tf=sum(Ci*Ti)/sum(Ci) exactly within explicitly declared model bounds."""
    document = ThermalIR.model_validate(document.model_dump(mode='json'))
    request = EquilibriumRequest.model_validate(request.model_dump(mode='json'))
    flags = [request.closed_adiabatic, request.constant_capacities, request.no_phase_change, request.no_reactions]
    if any(f is False for f in flags):
        return {'status': 'unsupported', 'message': 'Nonadiabatic, variable-capacity, phase-change or reacting systems need another model.'}
    if any(f is None for f in flags):
        return {'status': 'unknown', 'message': 'All equilibrium applicability assumptions must be declared.'}
    nodes = {n.node_id: n for n in document.nodes}
    if set(request.node_ids) - nodes.keys():
        return {'status': 'fail', 'message': 'Unknown thermal node.'}
    selected = [nodes[key] for key in request.node_ids]
    if any(n.kind == 'boundary' for n in selected):
        return {'status': 'unsupported', 'message': 'Boundary nodes are not finite-capacity bodies.'}
    if any((n.capacity is None and (n.mass is None or n.specific_heat is None)) or n.initial_temperature is None for n in selected):
        return {'status': 'unknown', 'message': 'Each body requires capacity (or mass and specific heat) and initial temperature.'}
    if any(b.node_id in request.node_ids and b.kind != 'adiabatic' for b in document.boundaries) or any(
            load.node_id in request.node_ids for load in document.loads):
        return {'status': 'unsupported', 'message': 'Selected bodies have imposed temperatures or heat loads.'}
    if any((edge.source_node_id in request.node_ids) != (edge.target_node_id in request.node_ids) for edge in document.edges):
        return {'status': 'unsupported', 'message': 'Selected bodies exchange heat with nodes outside the closed system.'}
    # The request explicitly declares mutual thermal contact. Edges are not required:
    # this model predicts the equilibrium endpoint, not contact geometry or duration.
    capacities = {}
    for node in selected:
        if node.capacity is not None:
            capacities[node.node_id] = Fraction(str(node.capacity.value))
        else:
            mass_kg = Fraction(str(node.mass.value)) * (Fraction(1, 1000) if node.mass.unit == 'g' else 1)
            specific_heat = Fraction(str(node.specific_heat.value)) * (1000 if node.specific_heat.unit == 'J/(g*K)' else 1)
            capacities[node.node_id] = mass_kg * specific_heat
    temperatures = {n.node_id: Fraction(str(n.initial_temperature.value)) +
        (Fraction('273.15') if n.initial_temperature.unit == 'degC' else 0) for n in selected}
    final = sum((capacities[k] * temperatures[k] for k in request.node_ids), Fraction(0)) / sum(capacities.values())
    changes = {k: capacities[k] * (final - temperatures[k]) for k in request.node_ids}
    if any(n.maximum_temperature is not None and final > Fraction(str(n.maximum_temperature.value)) +
           (Fraction('273.15') if n.maximum_temperature.unit == 'degC' else 0) for n in selected):
        return {'status': 'fail', 'message': 'Predicted endpoint exceeds a supplied node maximum.'}
    return {'status': 'pass', 'final_temperature_kelvin': str(final),
        'energy_changes_j': {k: str(v) for k, v in changes.items()}, 'energy_residual_j': str(sum(changes.values())),
        'capacities_j_per_k': {k: str(v) for k, v in capacities.items()},
        'message': 'Conditional equilibrium endpoint; no mixing time, phase change or ambient heat loss is predicted.'}

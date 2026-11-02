"""Bounded, auditable steady-state thermal screening; never CFD or FEA.

Only one lumped zone connected to one fixed-temperature ambient via exactly
one declared lumped thermal resistance/conductance is supported in v1. All
other networks may be valid designs without a supported numeric prediction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainModel, Finding, fingerprint
from .models import SteadyStateCase, ThermalIR, ThermalQuantity


class ThermalEvaluation(DomainModel):
    """A calculation receipt with explicit coverage and input identity."""

    case_id: str
    model: str
    model_version: str
    status: Literal["pass", "fail", "unknown", "unsupported"]
    input_digest: str
    zone_temperature_kelvin: str | None = None
    zone_temperature_celsius: str | None = None
    ambient_temperature_kelvin: str | None = None
    net_heat_w: str | None = None
    transferred_heat_w: str | None = None
    residual_w: str | None = None
    message: str


class ThermalAnalysis(DomainModel):
    """Pure design-time report, not an operational or compliance decision."""

    results: list[ThermalEvaluation] = Field(default_factory=list)


def temperature_kelvin(quantity: ThermalQuantity) -> Decimal:
    """Convert an absolute temperature to kelvin, not an interval."""
    if quantity.kind != "temperature":
        raise ValueError("Expected an absolute temperature.")
    value = Decimal(str(quantity.value))
    return value if quantity.unit == "K" else value + Decimal("273.15")


def heat_watts(quantity: ThermalQuantity) -> Decimal:
    """Convert a heat-rate magnitude to watts with explicit dimensional check."""
    if quantity.kind != "heat_rate":
        raise ValueError("Expected heat rate.")
    magnitude = Decimal(str(quantity.value))
    return magnitude if quantity.unit == "W" else magnitude * Decimal("1000")


def effective_resistance(edge: object) -> Decimal | None:
    """Return explicit effective K/W or None when transfer data are missing."""
    resistance = getattr(edge, "resistance", None)
    if resistance is not None:
        return Decimal(str(resistance.value))
    conductance = getattr(edge, "conductance", None)
    if conductance is not None:
        return Decimal(1) / Decimal(str(conductance.value))
    return None


def evaluate_case(document: ThermalIR, case: SteadyStateCase) -> ThermalEvaluation:
    """Evaluate a single-zone lumped-resistance request with exact decimal input.

    Source heat is positive and sink heat negative. The one-zone steady balance
    is Q_net - (T_zone - T_ambient)/R = 0, so T_zone = T_ambient + Q_net * R.
    Changing a selected input invalidates the receipt digest; unrelated
    artifacts are not included.
    """
    node = next(n for n in document.nodes if n.node_id == case.zone_node_id)
    ambient = next(n for n in document.nodes if n.node_id == case.ambient_node_id)
    edge = next(e for e in document.edges if e.edge_id == case.edge_id)
    loads = [next(load for load in document.loads if load.load_id == load_id)
             for load_id in case.load_ids]
    boundaries = [item for item in document.boundaries
                  if item.node_id in {case.zone_node_id, case.ambient_node_id}]
    input_digest = fingerprint({
        "case": case.model_dump(mode="json"),
        "zone": node.model_dump(mode="json"),
        "ambient": ambient.model_dump(mode="json"),
        "edge": edge.model_dump(mode="json"),
        "loads": [item.model_dump(mode="json") for item in loads],
        "boundaries": [item.model_dump(mode="json") for item in boundaries],
    })

    def outcome(status: Literal["pass", "fail", "unknown", "unsupported"],
                message: str, **kwargs: str) -> ThermalEvaluation:
        """Create one stable, version-pinned screening receipt."""
        return ThermalEvaluation(case_id=case.case_id, model=case.model,
                                 model_version=case.model_version,
                                 input_digest=input_digest, status=status,
                                 message=message, **kwargs)

    if node.kind == "boundary" or ambient.kind != "boundary":
        return outcome("unsupported", "A supported screening case requires a zone/body and a boundary node.")
    if set((edge.source_node_id, edge.target_node_id)) != {case.zone_node_id, case.ambient_node_id}:
        return outcome("unsupported", "Selected edge is not the zone-to-ambient connection.")
    if edge.mechanism != "lumped":
        return outcome("unsupported", "Only an explicitly lumped resistance/conductance is evaluated in v1.")
    if len([e for e in document.edges
            if case.zone_node_id in (e.source_node_id, e.target_node_id)]) != 1:
        return outcome("unsupported", "Additional thermal connections require a network solver.")
    if any(b.node_id == case.zone_node_id and b.kind == "fixed_temperature" for b in boundaries):
        return outcome("unsupported", "A fixed zone temperature is not solved by the one-zone model.")
    ambient_conditions = [b for b in boundaries
                          if b.node_id == case.ambient_node_id and b.kind == "fixed_temperature"]
    if len(ambient_conditions) != 1 or ambient_conditions[0].quantity is None:
        return outcome("unknown", "Fixed ambient temperature is missing.")
    if any(b.kind == "prescribed_heat" for b in boundaries):
        return outcome("unsupported", "Prescribed boundary heat is not part of the one-zone evaluator.")
    if any(item.heat_rate is None for item in loads):
        return outcome("unknown", "A selected thermal load has unknown power; no zero is inferred.")
    if any(item.node_id != case.zone_node_id for item in loads):
        return outcome("unsupported", "A selected thermal load is outside the zone.")
    resistance = effective_resistance(edge)
    if resistance is None:
        return outcome("unknown", "Effective thermal resistance/conductance is missing.")
    ambient_k = temperature_kelvin(ambient_conditions[0].quantity)
    net = sum((heat_watts(item.heat_rate) *
               (Decimal(1) if item.direction == "source" else Decimal(-1))
               for item in loads if item.heat_rate is not None), Decimal(0))
    zone_k = ambient_k + net * resistance
    if zone_k < 0:
        return outcome("fail", "Predicted zone temperature is below absolute zero.",
                       ambient_temperature_kelvin=str(ambient_k), net_heat_w=str(net))
    if node.maximum_temperature is not None:
        maximum_k = temperature_kelvin(node.maximum_temperature)
        if zone_k > maximum_k:
            return outcome(
                "fail", "Predicted screening temperature exceeds the declared design maximum; "
                "this is not a legal or safety qualification.",
                ambient_temperature_kelvin=str(ambient_k),
                zone_temperature_kelvin=str(zone_k),
                zone_temperature_celsius=str(zone_k - Decimal("273.15")),
                net_heat_w=str(net),
            )
    transferred = (zone_k - ambient_k) / resistance
    residual = net - transferred
    result = "pass" if abs(residual) <= Decimal(str(case.tolerance_w)) else "fail"
    return outcome(
        result,
        "Lumped one-zone energy balance satisfied; screening result is not a safety qualification."
        if result == "pass" else "Explicit one-zone energy balance failed.",
        ambient_temperature_kelvin=str(ambient_k),
        zone_temperature_kelvin=str(zone_k),
        zone_temperature_celsius=str(zone_k - Decimal("273.15")),
        net_heat_w=str(net),
        transferred_heat_w=str(transferred),
        residual_w=str(residual),
    )


def analyze_thermal(document: ThermalIR) -> ThermalAnalysis:
    """Evaluate only explicitly requested, bounded thermal design cases."""
    return ThermalAnalysis(results=[evaluate_case(document, case) for case in document.cases])


def validate_thermal(document: ThermalIR) -> list[Finding]:
    """Local validation remains separate from externally required source checks."""
    findings: list[Finding] = []
    for result in analyze_thermal(document).results:
        if result.status != "pass":
            findings.append(Finding(
                code=f"thermal_{result.status}", location=result.case_id,
                severity="error" if result.status == "fail" else "warning",
                message=result.message,
            ))
    return findings

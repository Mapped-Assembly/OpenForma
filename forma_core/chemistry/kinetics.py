"""Bounded first-order batch predictions from declared parameters, never inferred kinetics."""

from __future__ import annotations

from fractions import Fraction
import math
from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainModel, Finding, Text, fingerprint
from .models import ChemicalIR, Claim
from .stoichiometry import (
    ConservationResult, SpeciesAmountResult, Status, StoichiometryRequest, StoichiometryResult,
    _amounts_at_extent, _conservation_at_extent, evaluate_stoichiometry,
)


MODEL = "first-order-batch"
MODEL_VERSION = "1.0"
TIME_TO_SECONDS = {"s": Fraction(1), "min": Fraction(60), "h": Fraction(3600)}
RATE_TO_PER_SECOND = {"1/s": Fraction(1), "1/min": Fraction(1, 60), "1/h": Fraction(1, 3600)}
VOLUME_TO_LITRES = {"L": Fraction(1), "mL": Fraction(1, 1000), "m3": Fraction(1000)}


class Temperature(DomainModel):
    """A supplied temperature; degC may be negative, but absolute temperature may not."""

    value: float
    unit: Text
    claim: Claim = Field(default_factory=Claim)


class BatchVolume(DomainModel):
    """Positive, constant batch volume with explicit units and provenance."""

    value: float = Field(gt=0)
    unit: Text
    claim: Claim = Field(default_factory=Claim)


class EvaluationTime(DomainModel):
    """Elapsed time since the declared initial inventory (t = 0)."""

    value: float = Field(ge=0)
    unit: Text


class FirstOrderRateConstant(DomainModel):
    """The disappearance constant for A, applicable only at its supplied temperature."""

    value: float = Field(ge=0)
    unit: Text
    temperature: Temperature | None = None
    claim: Claim = Field(default_factory=Claim)


class KineticsRequest(DomainModel):
    """Explicit model, conditions and times on top of the pinned batch contract."""

    schema_version: Literal["1.0"] = "1.0"
    batch: StoichiometryRequest
    model: Text = MODEL
    model_version: Text = MODEL_VERSION
    rate_law: Text = "first_order_disappearance"
    rate_constant: FirstOrderRateConstant | None = None
    volume: BatchVolume | None = None
    temperature: Temperature | None = None
    isothermal: bool | None = None
    constant_volume: bool | None = None
    competing_reactions: bool | None = None
    times: list[EvaluationTime] = Field(min_length=1, max_length=10000)


class KineticSpeciesState(DomainModel):
    """Net inventory and concentration/rate for one species at one time."""

    amount: SpeciesAmountResult
    concentration_mol_per_l: str
    net_rate_mol_per_s: str
    net_concentration_rate_mol_per_l_s: str


class KineticSample(DomainModel):
    """One analytical solution sample; extent rates follow the written coefficient basis."""

    time_s: str
    extent_mol: str
    extent_rate_mol_per_s: str
    reaction_rate_mol_per_l_s: str
    disappearance_rate_mol_per_s: str
    disappearance_rate_mol_per_l_s: str
    species: list[KineticSpeciesState]
    conservation: ConservationResult


class KineticsResult(DomainModel):
    """Versioned prediction receipt retaining parameters, evidence and its theoretical bound."""

    schema_version: Literal["1.0"] = "1.0"
    model: Literal["first-order-batch"] = MODEL
    model_version: Literal["1.0"] = MODEL_VERSION
    status: Status
    input_digest: str
    request: KineticsRequest
    stoichiometric_bound: StoichiometryResult
    assumptions: list[str]
    findings: list[Finding] = Field(default_factory=list)
    consumed_species_id: str | None = None
    normalized_rate_constant_per_s: str | None = None
    normalized_volume_l: str | None = None
    normalized_temperature_k: str | None = None
    samples: list[KineticSample] = Field(default_factory=list)


def _input_digest(document: ChemicalIR, request: KineticsRequest) -> str:
    """Bind the model and all source/condition/parameter inputs to a reproducible receipt."""
    return fingerprint({"model": MODEL, "model_version": MODEL_VERSION,
                        "document": document.model_dump(mode="json"), "request": request.model_dump(mode="json")})


def kinetics_result_is_current(result: KineticsResult, document: ChemicalIR, request: KineticsRequest) -> bool:
    """Reject stale predictions after any parameter, condition, evidence or artifact change.

This is an input/version freshness check, not cryptographic verification of the
result itself. Re-evaluate when an input digest differs.
"""
    return (result.model == MODEL and result.model_version == MODEL_VERSION
            and result.input_digest == _input_digest(document, request))


def _kelvin(temperature: Temperature) -> Fraction:
    """Convert already unit-checked K/degC values without floating conversion drift."""
    return Fraction(str(temperature.value)) + (Fraction("273.15") if temperature.unit == "degC" else 0)


def _conversion(exponent: Fraction) -> tuple[Fraction, bool]:
    """Approximate exp once, then conserve species exactly using rational accounting.

expm1 preserves small conversion near t=0; exp preserves a small remaining
    inventory at large t. Tails beyond exp(-746) are outside binary64 resolution.
"""
    if exponent > 746:
        return Fraction(1), True
    value = float(exponent)
    if value <= 0.5:
        return Fraction(str(-math.expm1(-value))), exponent > 0 and value == 0
    remainder = math.exp(-value)
    return 1 - Fraction(str(remainder)), remainder == 0


def evaluate_kinetics(document: ChemicalIR, request: KineticsRequest) -> KineticsResult:
    """Evaluate -d[A]/dt = k[A] for one irreversible, isothermal constant-volume batch.

No rate order, constant, temperature correction or reaction feasibility is
inferred from stoichiometry. Schema errors use structured Pydantic diagnostics;
unsupported/missing scientific inputs return findings and no time series.
"""
    document = ChemicalIR.model_validate(document.model_dump(mode="json"))
    request = KineticsRequest.model_validate(request.model_dump(mode="json"))
    bound = evaluate_stoichiometry(document, request.batch)
    result = KineticsResult(
        status=bound.status, input_digest=_input_digest(document, request), request=request,
        stoichiometric_bound=bound, findings=list(bound.findings),
        assumptions=[
            "Declared disappearance law -d[A]/dt = k[A]; no kinetic parameter is inferred from a balanced reaction.",
            "One irreversible reaction in an isothermal, constant-volume batch without competing reactions.",
            "The supplied k applies only at its declared temperature; no Arrhenius or other temperature correction.",
            "Initial inventory is at t=0. Samples retain request order; no integration or interpolation is performed.",
            "The exponential uses binary64 math; subsequent inventory/conservation arithmetic is rational, not a claim of exact physical kinetics.",
            "Predictions are conditional on supplied model/parameters; no experimental validation or uncertainty interval is inferred.",
        ],
    )

    def stop(status: Status, code: str, location: str, message: str) -> KineticsResult:
        """Return a scoped finding without any partial numerical trajectory."""
        result.status = status
        result.samples = []
        result.findings.append(Finding(code=code, location=location, message=message,
                                       severity="error" if status == "fail" else "warning"))
        return result

    if bound.status != "pass":
        return result
    if (request.model, request.model_version, request.rate_law) != (MODEL, MODEL_VERSION, "first_order_disappearance"):
        return stop("unsupported", "kinetic_model", "model", "Only first-order-batch 1.0 with an explicitly declared first-order disappearance law is supported.")
    reaction = next(item for item in document.reactions if item.reaction_id == request.batch.reaction_id)
    if reaction.reversible or request.competing_reactions is True or request.isothermal is False or request.constant_volume is False:
        return stop("unsupported", "kinetic_regime", "conditions", "Reverse/competing reactions and changing temperature/volume require a different model.")
    initial = {item.species_id: Fraction(item.initial) for item in bound.amounts}
    coefficients = {item.species_id: Fraction(item.net_coefficient) for item in bound.amounts if Fraction(item.net_coefficient)}
    consumed = [key for key, coefficient in coefficients.items() if coefficient < 0]
    if len(consumed) != 1:
        return stop("unsupported", "kinetic_reactants", "reaction", "Exactly one net consumed species is supported; no pseudo-first-order assumption is inferred.")
    if any(value is None for value in (request.isothermal, request.constant_volume, request.competing_reactions)):
        return stop("unknown", "missing_batch_conditions", "conditions", "Explicit isothermal, constant-volume and competing-reaction declarations are required.")
    k, volume, temperature = request.rate_constant, request.volume, request.temperature
    if k is None or volume is None or temperature is None or k.temperature is None:
        return stop("unknown", "missing_kinetic_input", "parameters", "Rate constant, positive volume, operating temperature and rate-constant reference temperature are required.")
    if k.unit not in RATE_TO_PER_SECOND or volume.unit not in VOLUME_TO_LITRES:
        return stop("fail", "kinetic_units", "parameters", "Use inverse-time rate units and a supported volume unit; see kinetics schema discovery.")
    if any(item.unit not in TIME_TO_SECONDS for item in request.times):
        return stop("fail", "kinetic_time_unit", "times", "Evaluation times must use s, min or h.")
    if any(item.unit not in {"K", "degC"} for item in (temperature, k.temperature)):
        return stop("fail", "kinetic_temperature_unit", "temperature", "Temperature units must be K or degC.")
    operating_k, reference_k = _kelvin(temperature), _kelvin(k.temperature)
    if operating_k < 0 or reference_k < 0:
        return stop("fail", "kinetic_temperature", "temperature", "Temperature cannot be below absolute zero.")
    if operating_k != reference_k:
        return stop("unsupported", "kinetic_temperature_applicability", "temperature", "Operating temperature differs from the temperature supplied for k; no correction is inferred.")
    for condition in document.conditions:
        if condition.condition_id in reaction.condition_ids and condition.kind == "temperature":
            if condition.unit not in {"K", "degC"}:
                return stop("fail", "reaction_temperature_unit", condition.condition_id, "Referenced reaction temperature has unsupported units.")
            if _kelvin(Temperature(value=condition.value, unit=condition.unit)) != operating_k:
                return stop("unsupported", "reaction_temperature_applicability", condition.condition_id, "Requested temperature conflicts with the pinned reaction's declared condition.")
    sources = {item.source_id for item in bound.evidence}
    for name, claim in (("rate_constant", k.claim), ("volume", volume.claim),
                        ("temperature", temperature.claim), ("rate_constant.temperature", k.temperature.claim)):
        if set(claim.source_ids) - sources:
            return stop("fail", "unknown_kinetic_evidence", name, "A kinetic input references evidence absent from the chemical artifact and batch request.")
    if k.claim.basis == "unknown" or not (k.claim.source_ids or k.claim.rationale):
        return stop("unknown", "missing_rate_provenance", "rate_constant.claim", "A rate constant needs an explicit source or assumption; unknown provenance is not a validated parameter.")
    constant = Fraction(str(k.value)) * RATE_TO_PER_SECOND[k.unit]
    litres = Fraction(str(volume.value)) * VOLUME_TO_LITRES[volume.unit]
    reactant = consumed[0]
    consumption_coefficient = -coefficients[reactant]
    masses = {item.species_id: Fraction(item.molar_mass_g_per_mol) for item in bound.amounts if item.molar_mass_g_per_mol is not None}
    roles = {item.species_id: item.role for item in bound.amounts}
    assert bound.conservation is not None and bound.maximum_extent_mol is not None
    maximum_extent = Fraction(bound.maximum_extent_mol)
    result.consumed_species_id = reactant
    result.normalized_rate_constant_per_s = str(constant)
    result.normalized_volume_l = str(litres)
    result.normalized_temperature_k = str(operating_k)
    for index, time in enumerate(request.times):
        seconds = Fraction(str(time.value)) * TIME_TO_SECONDS[time.unit]
        conversion, underflow = _conversion(constant * seconds)
        extent = maximum_extent * conversion
        amounts = _amounts_at_extent(initial, coefficients, extent, masses, roles)
        remaining = initial[reactant] * (1 - conversion)
        disappearance_rate = constant * remaining
        extent_rate = disappearance_rate / consumption_coefficient
        conservation = _conservation_at_extent(
            document, bound.conservation.reaction_balance, coefficients, extent, masses,
            request.batch.mass_balance_tolerance_g,
        )
        result.samples.append(KineticSample(
            time_s=str(seconds), extent_mol=str(extent), extent_rate_mol_per_s=str(extent_rate),
            reaction_rate_mol_per_l_s=str(extent_rate / litres), disappearance_rate_mol_per_s=str(disappearance_rate),
            disappearance_rate_mol_per_l_s=str(disappearance_rate / litres), conservation=conservation,
            species=[KineticSpeciesState(amount=item, concentration_mol_per_l=str(Fraction(item.final) / litres),
                     net_rate_mol_per_s=str(coefficients.get(item.species_id, 0) * extent_rate),
                     net_concentration_rate_mol_per_l_s=str(coefficients.get(item.species_id, 0) * extent_rate / litres))
                     for item in amounts],
        ))
        if underflow:
            result.findings.append(Finding(code="kinetic_precision_limit", location=f"times.{index}", severity="warning",
                                           message="Exponential conversion or remaining fraction is below binary64 resolution; the corresponding fraction is represented as zero."))
    return result

"""Compatibility adapter for version-1 chemical experiment receipts."""
from __future__ import annotations
from fractions import Fraction
from forma_core.chemistry.stoichiometry import MASS_UNITS, MOLE_UNITS, evaluate_stoichiometry
from forma_core.domains.base import fingerprint
from forma_core.domains.providers import ArtifactReference
from forma_core.processes.validation import validate_process
from .models import Criterion, ExperimentPlan, ExperimentRequest, ExperimentRun, Reading

def _factor(unit: str, target: str) -> Fraction | None:
    """Convert compatible batch units only; never infer density or molecular weight."""
    for units in (MASS_UNITS, MOLE_UNITS):
        if unit in units and target in units:
            return units[unit] / units[target]
    return None


def _reference(plan: ExperimentPlan, criterion: Criterion) -> tuple[str, Fraction | None, str]:
    """Resolve formation versus inventory explicitly, preserving unsupported coverage."""
    if plan.prediction.status != "pass":
        return plan.prediction.status, None, "Prediction unavailable; inspect calculation findings."
    amount = next((a for a in plan.prediction.amounts if a.species_id == criterion.species_id), None)
    if amount is None:
        return "unknown", None, "Species is outside evaluated inventory."
    if criterion.unit in MOLE_UNITS:
        value = amount.final if criterion.basis == "total_inventory" else amount.formed
        reference = Fraction(value) / MOLE_UNITS[criterion.unit]
    elif criterion.unit in MASS_UNITS:
        value = amount.final_mass_g if criterion.basis == "total_inventory" else amount.formed_mass_g
        if value is None:
            return "unknown", None, "Mass prediction requires supplied molar mass."
        reference = Fraction(value) / MASS_UNITS[criterion.unit]
    else:
        return "unsupported", None, "Only compatible mass or amount quantities are supported."
    if criterion.basis == "isolated_product":
        if Fraction(amount.initial) != 0:
            return "unsupported", None, "Isolated product with initial product inventory needs a separation model."
        if criterion.recovery_fraction is None:
            return "unknown", None, "Isolated-product comparison requires an explicit recovery assumption."
        reference *= Fraction(str(criterion.recovery_fraction))
    return "pass", reference, "Conditional theoretical bound, not actual conversion."


def _reading(reading: Reading, unit: str) -> tuple[Fraction, Fraction | None, Fraction] | None:
    """Convert reading, uncertainty bound and resolution without rounding."""
    factor = _factor(reading.unit, unit)
    if factor is None:
        return None
    return (Fraction(str(reading.value)) * factor,
            None if reading.uncertainty is None else Fraction(str(reading.uncertainty)) * factor,
            Fraction(str(reading.resolution)) * factor)



def prepare_experiment(request: ExperimentRequest) -> ExperimentPlan:
    """Freeze supported predictions and topologically ordered native process steps."""
    request = ExperimentRequest.model_validate(request.model_dump(mode="json"))
    errors = [f for f in validate_process(request.process) if f.severity == "error"]
    if errors:
        raise ValueError(f"Invalid process protocol: {errors}")
    prediction = evaluate_stoichiometry(request.chemical, request.calculation)
    # A stale/invalid pin cannot become a protocol ready for observations.
    if prediction.status == "fail":
        raise ValueError(f"Invalid prediction inputs: {prediction.findings}")
    remaining = list(request.process.steps)
    ordered: list[str] = []
    while remaining:
        ready = [s for s in remaining if set(s.depends_on) <= set(ordered)]
        if not ready:
            raise ValueError("Protocol dependencies cannot be ordered.")
        ordered.extend(s.step_id for s in ready)
        remaining = [s for s in remaining if s not in ready]
    process_reference = ArtifactReference(artifact_id=request.process.artifact_id, domain="process",
        schema_version=request.process.schema_version, revision=request.process.revision, sha256=fingerprint(request.process))
    return ExperimentPlan(request=request, prediction=prediction, process_reference=process_reference, protocol_step_ids=ordered,
                          scientific_sha256=fingerprint({"request": request.model_dump(mode="json"), "prediction": prediction.model_dump(mode="json"),
                                                        "protocol_step_ids": ordered}))



def inventory_status(plan: ExperimentPlan, run: ExperimentRun) -> str:
    """Validate actual inventory for historical chemical-only declarations."""
    for feed in plan.request.calculation.inventory:
        actual = run.actual_inventory.get(feed.species_id)
        if actual is None:
            return "unknown"
        converted = _reading(actual, feed.unit)
        tolerance = Fraction(str(plan.request.inventory_tolerances.get(feed.species_id, 0)))
        if converted is None or abs(converted[0] - Fraction(str(feed.value))) > tolerance:
            return "unsupported"
    return "pass"

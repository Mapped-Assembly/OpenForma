"""Reproducible empirical comparison using existing exact stoichiometric arithmetic."""
from __future__ import annotations

from fractions import Fraction
from statistics import stdev
from typing import Any

from forma_core.chemistry.stoichiometry import MASS_UNITS, MOLE_UNITS, evaluate_stoichiometry
from forma_core.domains.base import fingerprint, unique
from forma_core.domains.providers import ArtifactReference
from forma_core.processes.validation import validate_process
from .models import Criterion, ExperimentPlan, ExperimentRequest, ExperimentRun, Reading

VERSION = "1.0"


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


def verify_plan(plan: ExperimentPlan) -> None:
    """Recompute receipts so edited predictions/criteria cannot masquerade as originals."""
    recomputed = prepare_experiment(plan.request)
    if recomputed != plan:
        raise ValueError("Plan integrity mismatch: create a new revision instead of editing a frozen plan.")


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


def compare_experiment(plan: ExperimentPlan, runs: list[ExperimentRun]) -> dict[str, Any]:
    """Compare trials against original criteria; never turn synthetic agreement into physical evidence."""
    plan = ExperimentPlan.model_validate(plan.model_dump(mode="json"))
    verify_plan(plan)
    runs = [ExperimentRun.model_validate(r.model_dump(mode="json")) for r in runs]
    unique([r.run_id for r in runs], "run ID")
    unique([r.sample_id for r in runs], "independent sample ID")
    criteria = {c.criterion_id: c for c in plan.request.criteria}
    for run in runs:
        if run.plan_sha256 != plan.scientific_sha256:
            raise ValueError("Run references a different plan revision/hash.")
        if any(o.criterion_id not in criteria for o in run.observations):
            raise ValueError("Observation references an unknown criterion.")
    results: list[dict[str, Any]] = []
    for run in runs:
        condition_status = "pass"
        for condition in plan.request.conditions:
            measured = run.conditions.get(condition.name)
            if measured is None:
                condition_status = "unknown"
                break
            if measured.unit != condition.unit:
                condition_status = "unsupported"
                break
            if not condition.minimum <= measured.value <= condition.maximum:
                condition_status = "unsupported"
                break
        for feed in plan.request.calculation.inventory:
            actual = run.actual_inventory.get(feed.species_id)
            if actual is None:
                condition_status = "unknown"
                break
            converted_feed = _reading(actual, feed.unit)
            tolerance = Fraction(str(plan.request.inventory_tolerances.get(feed.species_id, 0)))
            if converted_feed is None or abs(converted_feed[0] - Fraction(str(feed.value))) > tolerance:
                condition_status = "unsupported"
                break
        observations = {o.criterion_id: o for o in run.observations}
        for criterion in criteria.values():
            status, expected, message = _reference(plan, criterion)
            row: dict[str, Any] = {"run_id": run.run_id, "kind": run.kind,
                "criterion_id": criterion.criterion_id, "status": status, "message": message,
                "unit": criterion.unit, "reference": str(expected) if expected is not None else None,
                "observed": None, "signed_residual": None, "absolute_error": None,
                "relative_error": None, "observed_to_reference_ratio": None,
                "uncertainty_bound": None, "above_bound": False,
                "method": criterion.method, "basis": criterion.basis}
            observation = observations.get(criterion.criterion_id)
            if expected is None:
                pass
            elif observation is None:
                row.update(status="unknown", message="No observation supplied.")
            elif condition_status != "pass":
                row.update(status=condition_status, message="Missing or incompatible operating conditions or actual feed inventory.")
            elif observation.species_id != criterion.species_id or observation.basis != criterion.basis:
                row.update(status="unsupported", message="Species or quantity basis does not match.")
            elif criterion.assumptions and not observation.assumptions_confirmed:
                row.update(status="unknown", message="Measurement assumptions were not confirmed.")
            else:
                converted = [_reading(r, criterion.unit) for r in observation.readings]
                count = 2 if criterion.method == "difference_proxy" else 1
                if len(converted) != count or any(r is None for r in converted):
                    row.update(status="unsupported", message="Reading count or quantity units incompatible with method.")
                else:
                    first = converted[0]
                    assert first is not None
                    value, uncertainty, resolution = first
                    if count == 2:
                        second = converted[1]
                        assert second is not None
                        value -= second[0]
                        uncertainty = None if uncertainty is None or second[1] is None else uncertainty + second[1]
                        resolution += second[2]
                    residual = value - expected
                    tolerance = Fraction(str(criterion.absolute_tolerance))
                    # An interval wholly inside/outside the criterion gives a verdict;
                    # overlap or unknown uncertainty remains inconclusive.
                    if uncertainty is None or resolution > tolerance:
                        verdict = "unknown"
                    elif abs(residual) + uncertainty <= tolerance:
                        verdict = "pass"
                    elif abs(residual) - uncertainty > tolerance:
                        verdict = "fail"
                    else:
                        verdict = "unknown"
                    row.update(status=verdict, observed=str(value), signed_residual=str(residual),
                        absolute_error=str(abs(residual)),
                        relative_error=str(abs(residual) / abs(expected)) if expected else None,
                        observed_to_reference_ratio=str(value / expected) if expected else None,
                        uncertainty_bound=str(uncertainty) if uncertainty is not None else None,
                        above_bound=value > expected,
                        message="Scoped comparison only; uncertainty excludes unquantified model/proxy bias.")
            results.append(row)
    physical = [r for r in results if r["kind"] == "physical"]
    covered = {r["criterion_id"] for r in physical if r["status"] in {"pass", "fail"}}
    repeatability = {}
    for key in criteria:
        values = [float(Fraction(r["observed"])) for r in physical
                  if r["criterion_id"] == key and r["observed"] is not None]
        repeatability[key] = {"count": len(values), "sample_standard_deviation": stdev(values) if len(values) > 1 else None,
                              "unit": criteria[key].unit}
    return {"format": "forma.experiment-report", "schema_version": VERSION,
        "evaluator": "empirical-scalar-comparison", "evaluator_version": VERSION,
        "plan_sha256": plan.scientific_sha256, "run_sha256": [fingerprint(r) for r in runs],
        "input_digest": fingerprint({"plan": plan.model_dump(mode="json"), "runs": [r.model_dump(mode="json") for r in runs]}),
        "physical_status": "not_run" if not any(r.kind == "physical" for r in runs) else "observations_supplied",
        "results": results, "verification_coverage": len(covered) / len(criteria),
        "repeatability": repeatability,
        "metrics": {"material_balance_residual": {"status": "unknown", "reason": "Full measured inventory not supplied; a proxy mass difference is not a mass-balance closure."},
                    "energy_accounting": {"status": "unsupported", "reason": "No energy evaluator in this slice."},
                    "generalization": {"status": "unknown", "reason": "Requires independently designed held-out processes."},
                    "unsupported_criteria": [c.criterion_id for c in criteria.values() if _reference(plan, c)[0] == "unsupported"],
                    "trial_costs": [{"run_id": r.run_id, "cost": r.cost, "currency": r.currency,
                                     "elapsed_seconds": (r.completed_at-r.started_at).total_seconds()} for r in runs]},
        "scope": "Laboratory-scale conditional comparisons; no industrial qualification or authenticity guarantee.",
        "limitations": plan.request.limitations, "plan": plan.model_dump(mode="json"),
        "runs": [r.model_dump(mode="json") for r in runs]}

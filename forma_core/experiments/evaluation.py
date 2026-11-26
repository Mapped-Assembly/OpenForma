"""Declarative empirical comparison of named outputs from registered domain evaluators."""
from __future__ import annotations

from fractions import Fraction
from statistics import stdev
from typing import Any

from forma_core.domains.base import fingerprint, unique
from forma_core.domains.providers import ArtifactReference, DomainRegistry
from forma_core.domains.registry import get_registry
from forma_core.domains.quantities import convert
from forma_core.domains.evaluation import EvaluationOutput, PinnedArtifact
from forma_core.processes.validation import validate_process
from .models import Criterion, ExperimentPlan, ExperimentRequest, ExperimentRun, Reading, ResolvedInput

VERSION = "2.0"


def prepare_experiment(request: ExperimentRequest, *, registry: DomainRegistry | None = None) -> ExperimentPlan:
    """Freeze supported predictions and topologically ordered native process steps."""
    request = ExperimentRequest.model_validate(request.model_dump(mode="json"))
    errors = [f for f in validate_process(request.process) if f.severity == "error"]
    if errors:
        raise ValueError(f"Invalid process protocol: {errors}")
    if not request.evaluations:
        from .legacy import prepare_experiment as legacy_prepare
        return legacy_prepare(request)
    return _prepare_named(request, get_registry(registry))


def _prepare_named(request: ExperimentRequest, registry: DomainRegistry) -> ExperimentPlan:
    """Resolve declarations through the existing provider registry and freeze their receipts."""
    process_reference = ArtifactReference(artifact_id=request.process.artifact_id, domain="process",
        schema_version=request.process.schema_version, revision=request.process.revision, sha256=fingerprint(request.process))
    artifacts = {a.reference.artifact_id: a for a in request.artifacts}
    artifacts[request.process.artifact_id] = PinnedArtifact(reference=process_reference, artifact=request.process.model_dump(mode="json"))
    for artifact in artifacts.values():
        if fingerprint(artifact.artifact) != artifact.reference.sha256:
            raise ValueError("Artifact content digest mismatch.")
        provider = registry.provider(artifact.reference.domain)
        if provider is not None and not registry.reference_errors(artifact.reference):
            identity = provider.describe(artifact.artifact, artifact.reference)
            if any(getattr(identity, key) != getattr(artifact.reference, key) for key in
                   ("artifact_id", "domain", "schema_id", "schema_version", "revision")):
                raise ValueError("Artifact revision/schema identity mismatch.")
            errors = [f for f in registry.validate_artifact(artifact.artifact, artifact.reference) if f.severity == "error"]
            if errors:
                raise ValueError(f"Invalid native artifact: {errors}")
    predictions = [registry.evaluate_named(artifacts[e.artifact_id].artifact, artifacts[e.artifact_id].reference, e)
                   for e in request.evaluations]
    if any(p.status == "fail" for p in predictions):
        raise ValueError("Invalid prediction inputs: " + "; ".join(p.message for p in predictions if p.status == "fail"))
    inputs = []
    for requirement in request.required_inputs:
        quantity: Any = (artifacts[requirement.artifact_id].artifact if requirement.artifact_id is not None else
                         next(e.parameters for e in request.evaluations if e.evaluation_id == requirement.evaluation_id))
        try:
            for key in requirement.path:
                quantity = quantity[key]
            value = convert(Fraction(str(quantity["value"])), quantity["unit"], requirement.unit, requirement.dimension)
        except (KeyError, TypeError, IndexError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"Unresolved dimensioned input {requirement.input_id}.") from exc
        if value is None:
            raise ValueError(f"Incompatible units for input {requirement.input_id}.")
        inputs.append(ResolvedInput(**requirement.model_dump(mode="json"), value=str(value)))
    remaining = list(request.process.steps)
    ordered = []
    while remaining:
        ready = [step for step in remaining if set(step.depends_on) <= set(ordered)]
        if not ready:
            raise ValueError("Protocol dependencies cannot be ordered.")
        ordered.extend(step.step_id for step in ready)
        remaining = [step for step in remaining if step not in ready]
    plan = ExperimentPlan(schema_version="2.0", request=request, predictions=predictions,
        process_reference=process_reference, protocol_step_ids=ordered, input_requirements=inputs,
        scientific_sha256="0" * 64)
    plan.scientific_sha256 = fingerprint(plan.model_dump(mode="json", exclude={"scientific_sha256"}))
    return plan


def verify_plan(plan: ExperimentPlan, *, registry: DomainRegistry | None = None) -> ExperimentPlan:
    """Check history integrity and reevaluate; unavailable models remain explicit, never cached passes."""
    if plan.schema_version == "2.0" and fingerprint(plan.model_dump(mode="json", exclude={"scientific_sha256"})) != plan.scientific_sha256:
        raise ValueError("Plan integrity mismatch: create a new revision instead of editing a frozen plan.")
    recomputed = prepare_experiment(plan.request, registry=registry)
    if recomputed != plan:
        if plan.schema_version == "2.0" and any(p.status in {"unknown", "unsupported"} for p in recomputed.predictions):
            # Still compare reproducible independent evaluations; block only unavailable ones.
            originals = {p.request.evaluation_id: p for p in plan.predictions}
            if (plan.model_dump(mode="json", exclude={"predictions", "scientific_sha256"}) ==
                    recomputed.model_dump(mode="json", exclude={"predictions", "scientific_sha256"}) and
                    all(p.status in {"unknown", "unsupported"} or p == originals.get(p.request.evaluation_id) for p in recomputed.predictions)):
                return recomputed
        raise ValueError("Plan integrity mismatch: create a new revision instead of editing a frozen plan.")
    return recomputed


def _output(plan: ExperimentPlan, criterion: Criterion) -> EvaluationOutput | None:
    """Resolve a declared output by stable invocation/output IDs, never by list position."""
    if criterion.target is None:
        return None
    receipt = next(p for p in plan.predictions if p.request.evaluation_id == criterion.target.evaluation_id)
    return next((o for o in receipt.outputs if o.output_id == criterion.target.output_id), None)


def _reference(plan: ExperimentPlan, criterion: Criterion) -> tuple[str, Fraction | None, str]:
    """Resolve any declared dimensioned output; old species-based receipts use an isolated adapter."""
    if criterion.target is None:
        from .legacy import _reference as legacy_reference
        return legacy_reference(plan, criterion)
    receipt = next(p for p in plan.predictions if p.request.evaluation_id == criterion.target.evaluation_id)
    if receipt.status != "pass":
        return receipt.status, None, receipt.message
    output = _output(plan, criterion)
    if output is None:
        return "unsupported", None, "Named output is not supplied by this evaluator."
    if output.status != "pass":
        return output.status, None, output.message
    if output.basis != criterion.basis:
        return "unsupported", None, "Output quantity basis does not match criterion."
    if output.dimension == "temperature" and criterion.method != "direct":
        return "unsupported", None, "An absolute temperature cannot be compared with a difference of readings."
    value = convert(Fraction(output.value), output.unit, criterion.unit, output.dimension)
    return ("unsupported", None, "Incompatible output units/dimension.") if value is None else ("pass", value, output.message)


def _reading(reading: Reading, unit: str, dimension: str | None = None) -> tuple[Fraction, Fraction | None, Fraction] | None:
    """Convert values and interval bounds separately; affine offsets never affect uncertainty."""
    if dimension is None:
        from .legacy import _reading as legacy_reading
        return legacy_reading(reading, unit)
    value = convert(Fraction(str(reading.value)), reading.unit, unit, dimension)
    resolution = convert(Fraction(str(reading.resolution)), reading.unit, unit, dimension, interval=True)
    uncertainty = None if reading.uncertainty is None else convert(Fraction(str(reading.uncertainty)), reading.unit, unit, dimension, interval=True)
    if value is None or resolution is None:
        return None
    return value, uncertainty, resolution


def compare_experiment(plan: ExperimentPlan, runs: list[ExperimentRun], *, registry: DomainRegistry | None = None) -> dict[str, Any]:
    """Compare trials against original criteria; never turn synthetic agreement into physical evidence."""
    plan = ExperimentPlan.model_validate(plan.model_dump(mode="json"))
    evaluated = verify_plan(plan, registry=registry)
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
        if plan.schema_version == "1.0":
            from .legacy import inventory_status
            legacy_status = inventory_status(plan, run)
            if legacy_status != "pass":
                condition_status = legacy_status
        for requirement in plan.input_requirements:
            actual = run.inputs.get(requirement.input_id)
            converted = None if actual is None else _reading(actual, requirement.unit, requirement.dimension)
            if actual is None:
                condition_status = "unknown"
                break
            if converted is None or abs(converted[0] - Fraction(requirement.value)) > Fraction(str(requirement.absolute_tolerance)):
                condition_status = "unsupported"
                break
        observations = {o.criterion_id: o for o in run.observations}
        for criterion in criteria.values():
            status, expected, message = _reference(evaluated, criterion)
            output = _output(evaluated, criterion)
            dimension = output.dimension if output is not None else None
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
                row.update(status=condition_status, message="Missing or incompatible operating conditions or declared actual inputs.")
            elif observation.target != criterion.target or observation.species_id != criterion.species_id or observation.basis != criterion.basis:
                row.update(status="unsupported", message="Observation target or quantity basis does not match.")
            elif criterion.assumptions and not observation.assumptions_confirmed:
                row.update(status="unknown", message="Measurement assumptions were not confirmed.")
            else:
                converted = [_reading(r, criterion.unit, dimension) for r in observation.readings]
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
                        relative_error=str(abs(residual) / abs(expected)) if expected and dimension != "temperature" else None,
                        observed_to_reference_ratio=str(value / expected) if expected and dimension != "temperature" else None,
                        uncertainty_bound=str(uncertainty) if uncertainty is not None else None,
                        above_bound=value > expected and (output is None or output.kind == "upper_bound"),
                        message="Scoped comparison only; uncertainty excludes unquantified model/proxy bias. Absolute-temperature ratios are not reported." if dimension == "temperature" else "Scoped comparison only; uncertainty excludes unquantified model/proxy bias.")
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
                    "energy_accounting": {"status": "unknown" if any(o.kind == "conservation_residual" and o.dimension in {"energy", "heat_rate"} for p in plan.predictions for o in p.outputs) else "unsupported",
                        "reason": "Modeled conservation is not measured energy closure.",
                        "modeled_residuals": [o.model_dump(mode="json") for p in plan.predictions for o in p.outputs if o.kind == "conservation_residual" and o.dimension in {"energy", "heat_rate"}]},
                    "generalization": {"status": "unknown", "reason": "Requires independently designed held-out processes."},
                    "unsupported_criteria": [c.criterion_id for c in criteria.values() if _reference(evaluated, c)[0] == "unsupported"],
                    "trial_costs": [{"run_id": r.run_id, "cost": r.cost, "currency": r.currency,
                                     "elapsed_seconds": (r.completed_at-r.started_at).total_seconds()} for r in runs]},
        "evaluation_statuses": {p.request.evaluation_id: p.status for p in evaluated.predictions},
        "scope": "Laboratory-scale conditional comparisons; no industrial qualification or authenticity guarantee.",
        "limitations": plan.request.limitations, "plan": plan.model_dump(mode="json"),
        "runs": [r.model_dump(mode="json") for r in runs]}

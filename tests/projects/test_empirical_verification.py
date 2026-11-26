"""Synthetic software fixtures: none of these are physical experimental observations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import subprocess
import sys
from typing import Any

import pytest

from forma_core.domains.base import fingerprint
from forma_core.domains.tools import call_domain_tool
from forma_core.experiments import ExperimentPlan, ExperimentRequest, ExperimentRun, compare_experiment, prepare_experiment
from forma_core.experiments.tools import experiment_tool


def fixture() -> ExperimentRequest:
    """Load the packaged, manually executable benchmark."""
    return ExperimentRequest.model_validate(experiment_tool({'action': 'benchmark'}))


def trial(plan: ExperimentPlan, *, value: float = 1.04776, kind: str = 'synthetic') -> ExperimentRun:
    """Construct clearly marked synthetic readings for software assertions only."""
    now = datetime.now(timezone.utc)
    return ExperimentRun.model_validate(dict(run_id='run-1', sample_id='sample-1', kind=kind,
        plan_sha256=plan.scientific_sha256, started_at=now-timedelta(seconds=10), completed_at=now,
        operator='software fixture', actual_inventory={f.species_id: dict(value=f.value,unit=f.unit,instrument='synthetic feed measurement',resolution=0.01,uncertainty=0.01) for f in plan.request.calculation.inventory}, conditions={'ambient_temperature': dict(value=22,unit='degC',instrument='synthetic',resolution=0.1,uncertainty=0.2)},
        observations=[dict(observation_id='obs',criterion_id='co2-mass-proxy',species_id='co2',basis='net_formation',
            readings=[dict(value=100,unit='g',instrument='synthetic scale',resolution=0.01,uncertainty=0.01),
                      dict(value=100-value,unit='g',instrument='synthetic scale',resolution=0.01,uncertainty=0.01)],
            evidence_ids=['raw'],assumptions_confirmed=True)],
        evidence=[dict(source_id='raw',basis='illustrative' if kind=='synthetic' else 'measured',title='Synthetic test data',
                       rationale='Unit test, never physically measured.',publisher='test',locator='fixture',published_at=now.isoformat(),retrieved_at=now.isoformat())]))


def result(plan: ExperimentPlan, run: ExperimentRun) -> dict[str, Any]:
    """Return the single comparison under test."""
    return compare_experiment(plan, [run])['results'][0]


def test_benchmark_prediction_and_empty_report() -> None:
    """Independently check mass arithmetic and refuse a physical claim before observations."""
    plan = prepare_experiment(fixture())
    co2 = next(a for a in plan.prediction.amounts if a.species_id == 'co2')
    assert Fraction(co2.formed_mass_g) == Fraction('2') / Fraction('84.0066') * Fraction('44.0095')
    assert plan.prediction.limiting_species_ids == ['bicarbonate']
    report = compare_experiment(plan, [])
    assert report['physical_status'] == 'not_run'
    assert report['verification_coverage'] == 0
    assert report['metrics']['material_balance_residual']['status'] == 'unknown'
    assert report['metrics']['energy_accounting']['status'] == 'unsupported'
    assert plan.protocol_step_ids == ['prepare','inputs','before','mix','after','record']


@pytest.mark.parametrize('value,status,above', [(1.04776,'pass',False),(0.3,'fail',False),(2,'fail',True)])
def test_agreement_shortfall_and_above_bound(value: float, status: str, above: bool) -> None:
    """Preserve discrepancies and above-bound ratios, including synthetic labeling."""
    plan = prepare_experiment(fixture())
    report = compare_experiment(plan, [trial(plan, value=value)])
    row = report['results'][0]
    assert row['status'] == status
    assert row['above_bound'] == above
    assert report['physical_status'] == 'not_run'
    assert report['verification_coverage'] == 0
    if above:
        assert Fraction(row['observed_to_reference_ratio']) > 1


@pytest.mark.parametrize('change,status', [('uncertainty','unknown'),('precision','unknown'),('species','unsupported'),('unit','unsupported'),('condition','unknown'),('temperature','unsupported'),('basis','unsupported'),('assumption','unknown')])
def test_incompatible_or_missing_inputs(change: str, status: str) -> None:
    """Missing or incompatible science must not become a pass."""
    plan = prepare_experiment(fixture()); run = trial(plan)
    observation = run.observations[0]
    if change == 'uncertainty': observation.readings[0].uncertainty = None
    if change == 'precision': observation.readings[0].resolution = 1
    if change == 'species': observation.species_id = 'water'
    if change == 'unit': observation.readings[0].unit = 'mL'
    if change == 'condition': run.conditions = {}
    if change == 'temperature': run.conditions['ambient_temperature'].value = 50
    if change == 'basis': observation.basis = 'total_inventory'
    if change == 'assumption': observation.assumptions_confirmed = False
    assert result(plan, run)['status'] == status


def test_unit_conversion_and_unknown_not_zero() -> None:
    """Equivalent mg readings preserve discrepancy; unknown uncertainty stays absent."""
    plan = prepare_experiment(fixture()); run = trial(plan)
    original = result(plan, run)
    for reading in run.observations[0].readings:
        reading.value *= 1000; reading.resolution *= 1000; reading.uncertainty *= 1000; reading.unit='mg'
    assert abs(float(Fraction(result(plan, run)['signed_residual'])-Fraction(original['signed_residual']))) < 1e-12
    run.observations[0].readings[0].uncertainty=None
    assert result(plan, run)['uncertainty_bound'] is None


def test_integrity_and_refinement() -> None:
    """Reject receipt edits, stale run pins, and preserve historical predictions on refinement."""
    original = prepare_experiment(fixture()); run = trial(original)
    edited = deepcopy(original); edited.request.criteria[0].absolute_tolerance=10
    with pytest.raises(ValueError, match='integrity'): compare_experiment(edited,[run])
    edited=deepcopy(original); edited.prediction.amounts[0].formed='100'
    with pytest.raises(ValueError, match='integrity'): compare_experiment(edited,[run])
    request=fixture(); request.revision=2; request.parent_plan_sha256=original.scientific_sha256
    request.calculation.inventory[0].value=1
    revised=prepare_experiment(request)
    with pytest.raises(ValueError, match='different plan'): compare_experiment(revised,[run])
    assert original == prepare_experiment(fixture())
    assert original.scientific_sha256 != revised.scientific_sha256


def test_zero_reference_and_initial_inventory() -> None:
    """Zero denominators remain undefined; total inventory retains initial product."""
    request=fixture(); request.calculation.inventory[0].value=0
    plan=prepare_experiment(request); row=result(plan,trial(plan,value=0))
    assert row['relative_error'] is None and row['observed_to_reference_ratio'] is None
    request=fixture()
    from forma_core.chemistry.stoichiometry import FeedAmount
    request.calculation.inventory.append(FeedAmount(species_id='co2',value=1,unit='g'))
    formation=prepare_experiment(request)
    request.criteria[0].basis='total_inventory'; total=prepare_experiment(request)
    run=trial(total); run.observations[0].basis='total_inventory'
    assert Fraction(result(total,run)['reference'])-Fraction(result(formation,trial(formation))['reference']) == 1
    request.criteria[0].basis='isolated_product'; request.criteria[0].recovery_fraction=0.9
    isolated=prepare_experiment(request); run=trial(isolated); run.observations[0].basis='isolated_product'
    assert result(isolated,run)['status']=='unsupported'


def test_nonfinite_duplicates_synthetic_mislabel_and_repeatability() -> None:
    """Validate mutable models at the boundary and count independent physical trials only."""
    plan=prepare_experiment(fixture()); run=trial(plan)
    with pytest.raises(ValueError,match='run ID'): compare_experiment(plan,[run,run])
    run.observations[0].readings[0].value=float('nan')
    with pytest.raises(ValueError): compare_experiment(plan,[run])
    run=trial(plan); run.kind='physical'
    with pytest.raises(ValueError,match='basis'): compare_experiment(plan,[run])
    one=trial(plan,kind='physical'); two=trial(plan,value=1.04,kind='physical'); two.run_id='run-2'; two.sample_id='sample-2'
    report=compare_experiment(plan,[one,two])
    assert report['repeatability']['co2-mass-proxy']['count']==2
    assert report['repeatability']['co2-mass-proxy']['sample_standard_deviation'] > 0
    assert report['verification_coverage']==1


def test_generalization_does_not_depend_on_benchmark_species_or_ids() -> None:
    """Rename the species and protocol identities without changing evaluator behavior."""
    request=fixture(); original=prepare_experiment(request)
    raw=json.dumps(request.model_dump(mode='json')).replace('co2','unseen-product').replace('co2-mass-proxy','new-criterion')
    request=ExperimentRequest.model_validate_json(raw)
    request.calculation.chemical.sha256=fingerprint(request.chemical)
    plan=prepare_experiment(request); run=trial(plan)
    run.observations[0].criterion_id=request.criteria[0].criterion_id; run.observations[0].species_id='unseen-product'
    assert result(plan,run)['reference']==result(original,trial(original))['reference']


def test_sdk_cli_and_mcp_wire_equivalence() -> None:
    """All transports call the same pure operation; JSON round trips preserve hashes."""
    payload={'action':'prepare','request':fixture().model_dump(mode='json')}
    expected=call_domain_tool('experiment',payload)
    completed=subprocess.run([sys.executable,'-m','forma_core','domains','experiment','-'],input=json.dumps(payload),text=True,capture_output=True)
    assert completed.returncode==0, completed.stderr
    assert json.loads(completed.stdout)==expected
    from forma_core.opencode.models import DomainToolArguments
    arguments=DomainToolArguments(operation='experiment',payload=json.dumps(payload))
    assert call_domain_tool(arguments.operation,json.loads(arguments.payload))==expected
    plan=ExperimentPlan.model_validate_json(json.dumps(expected))
    assert compare_experiment(plan,[])['plan_sha256']==plan.scientific_sha256


def test_missing_changed_feeds_and_missing_observation() -> None:
    """Actual feed mismatch cannot silently compare against another scenario."""
    plan=prepare_experiment(fixture());run=trial(plan)
    run.actual_inventory={}
    assert result(plan,run)['status']=='unknown'
    run=trial(plan);run.actual_inventory['bicarbonate'].value=3
    assert result(plan,run)['status']=='unsupported'
    run=trial(plan);run.observations=[]
    assert result(plan,run)['status']=='unknown'


def test_unsupported_model_missing_mass_and_isolated_recovery() -> None:
    """Unsupported modes and unknown properties stay explicit; recovery is never inferred."""
    request=fixture();request.calculation.mode='equilibrium'
    plan=prepare_experiment(request)
    assert result(plan,trial(plan))['status']=='unsupported'
    request=fixture();request.calculation.molar_masses=[m for m in request.calculation.molar_masses if m.species_id!='co2']
    plan=prepare_experiment(request)
    assert result(plan,trial(plan))['status']=='unknown'
    request=fixture();request.criteria[0].basis='isolated_product'
    plan=prepare_experiment(request);run=trial(plan);run.observations[0].basis='isolated_product'
    assert result(plan,run)['status']=='unknown'
    request.criteria[0].recovery_fraction=0.5
    recovered=prepare_experiment(request);run=trial(recovered);run.observations[0].basis='isolated_product'
    assert Fraction(result(recovered,run)['reference']) == Fraction(440095,420033)/2


def test_actual_mcp_dispatch_matches_sdk() -> None:
    """Exercise the existing MCP handler rather than only its argument schema."""
    import asyncio
    from apps.api.opencode_mcp import _call_tool
    from forma_core.opencode.models import DomainToolArguments
    payload={'action':'prepare','request':fixture().model_dump(mode='json')}
    arguments=DomainToolArguments(operation='experiment',payload=json.dumps(payload))
    actual=asyncio.run(_call_tool('forma.opencode.domains',arguments,None))
    assert actual==experiment_tool(payload)

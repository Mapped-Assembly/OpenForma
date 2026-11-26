"""Cross-domain benchmarks use synthetic evidence only; no physical success is asserted."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
from typing import Any

import pytest

from forma_core.domains.base import fingerprint
from forma_core.domains.registry import create_registry
from forma_core.domains.tools import call_domain_tool
from forma_core.experiments import ExperimentRequest, ExperimentRun, prepare_experiment, compare_experiment
from forma_core.experiments.models import ExperimentPlan
from forma_core.experiments.tools import experiment_tool


def request() -> ExperimentRequest:
    """Load a purely declarative thermal/process case, without chemical placeholders."""
    return ExperimentRequest.model_validate(experiment_tool({'action':'benchmark','benchmark_id':'water-mixing'}))


def repin(req: ExperimentRequest) -> ExperimentRequest:
    """Pin a deliberately revised test artifact before preparing a new prediction."""
    for artifact in req.artifacts:
        artifact.reference.sha256 = fingerprint(artifact.artifact)
    return req


def reading(value: float, unit: str, uncertainty: float | None = 0.1) -> dict[str, Any]:
    """Declare synthetic instrument data, including unknown uncertainty when requested."""
    return dict(value=value,unit=unit,instrument='synthetic test instrument',resolution=0.1,uncertainty=uncertainty)


def trial(plan: ExperimentPlan, value: float = 29.77, unit: str = 'degC') -> ExperimentRun:
    """Create an explicitly synthetic trial with all declared inputs and provenance."""
    now=datetime.now(timezone.utc)
    return ExperimentRun.model_validate(dict(run_id='synthetic-1',sample_id='synthetic-sample',kind='synthetic',
        started_at=now-timedelta(minutes=2),completed_at=now,operator='software benchmark',
        plan_sha256=plan.scientific_sha256,
        inputs={i.input_id:reading(float(Fraction(i.value)),i.unit) for i in plan.input_requirements},
        conditions={'measurement_delay':reading(60,'s')},
        observations=[dict(observation_id='endpoint',criterion_id='mixed-temperature',
            target={'evaluation_id':'mix-equilibrium','output_id':'final_temperature'},basis='equilibrium_temperature',
            readings=[reading(value,unit)],evidence_ids=['synthetic'],assumptions_confirmed=True)],
        evidence=[dict(source_id='synthetic',basis='illustrative',title='Synthetic thermal comparison',rationale='Software test values; never physically measured.')]))


def row(plan: ExperimentPlan, run: ExperimentRun) -> dict[str, Any]:
    """Return the one comparison in the default thermal fixture."""
    return compare_experiment(plan,[run])['results'][0]


@pytest.mark.parametrize('warm_mass,cool_mass,cup_capacity,expected', [
    (100,100,20,Fraction(25480,856)),
    (200,100,20,Fraction(42200,1274)),
    (50,150,20,Fraction(21300,856)),
    (100,100,100,Fraction(27080,936)),
])
def test_mass_changes_drive_temperature_without_case_code(warm_mass: int, cool_mass: int, cup_capacity: int, expected: Fraction) -> None:
    """Independent energy arithmetic checks unseen mass/capacity declarations."""
    req=request(); nodes=req.artifacts[0].artifact['nodes']
    nodes[0]['mass']['value']=warm_mass;nodes[1]['mass']['value']=cool_mass;nodes[2]['capacity']['value']=cup_capacity
    plan=prepare_experiment(repin(req))
    raw=plan.predictions[0].raw
    assert Fraction(raw['final_temperature_kelvin'])-Fraction('273.15')==expected
    assert Fraction(raw['energy_residual_j'])==0
    assert sum(Fraction(v) for v in raw['energy_changes_j'].values())==0
    assert 'chemical' not in plan.request.model_dump(mode='json')
    assert 'calculation' not in plan.request.model_dump(mode='json')
    assert raw['capacities_j_per_k']['warm']==str(Fraction(warm_mass)*Fraction('4.18'))
    assert compare_experiment(plan,[])['physical_status']=='not_run'


def test_temperature_affine_conversion_and_no_false_bound_claim() -> None:
    """Kelvin offsets affect readings but not uncertainty or temperature discrepancies."""
    plan=prepare_experiment(request())
    celsius=row(plan,trial(plan));kelvin=row(plan,trial(plan,302.92,'K'))
    assert celsius['status']==kelvin['status']=='pass'
    assert celsius['signed_residual']==kelvin['signed_residual']
    assert kelvin['uncertainty_bound']=='1/10'
    assert kelvin['relative_error'] is None
    assert kelvin['observed_to_reference_ratio'] is None
    assert row(plan,trial(plan,40))['status']=='fail'
    assert row(plan,trial(plan,40))['above_bound'] is False
    report=compare_experiment(plan,[trial(plan)])
    assert report['verification_coverage']==0
    assert report['metrics']['energy_accounting']['status']=='unknown'
    assert report['metrics']['energy_accounting']['modeled_residuals'][0]['value']=='0'


@pytest.mark.parametrize('change,status', [('mass','unsupported'),('input','unknown'),('output','unsupported'),('unit','unsupported'),('basis','unsupported'),('uncertainty','unknown'),('target','unsupported')])
def test_incompatible_declarations_or_measurements(change: str, status: str) -> None:
    """Missing, incompatible and out-of-envelope evidence cannot become a pass."""
    req=request()
    if change=='output': req.criteria[0].target.output_id='not-a-model-output'
    if change=='unit': req.criteria[0].unit='mol'
    if change=='basis': req.criteria[0].basis='isolated_product'
    plan=prepare_experiment(req);run=trial(plan)
    if change=='mass': run.inputs['warm-mass'].value=200
    if change=='input': run.inputs.pop('warm-mass')
    if change=='uncertainty': run.observations[0].readings[0].uncertainty=None
    if change=='target': run.observations[0].target.output_id='energy_residual'
    assert row(plan,run)['status']==status


@pytest.mark.parametrize('change,status', [('capacity','unknown'),('assumption','unknown'),('phase_change','unsupported'),('version','unsupported')])
def test_unsupported_or_unknown_science(change: str, status: str) -> None:
    """No fallback equation is substituted for unsupported or underspecified physics."""
    req=request()
    if change=='capacity': req.artifacts[0].artifact['nodes'][0]['specific_heat']=None
    if change=='assumption': req.evaluations[0].parameters['closed_adiabatic']=None
    if change=='phase_change': req.evaluations[0].parameters['no_phase_change']=False
    if change=='version': req.evaluations[0].evaluator_version='999'
    plan=prepare_experiment(repin(req))
    assert plan.predictions[0].status==status
    assert row(plan,trial(plan))['status']==status


def test_registered_provider_unavailable_blocks_only_its_outputs() -> None:
    """An old successful receipt is not reused as a currently available evaluator."""
    plan=prepare_experiment(request());registry=create_registry()
    metadata=registry.metadata('thermal');metadata.availability='unavailable'
    registry.register(metadata,replace=True)
    report=compare_experiment(plan,[trial(plan)],registry=registry)
    assert report['results'][0]['status']=='unknown'
    assert report['plan']['predictions'][0]['status']=='pass'
    assert report['evaluation_statuses']['mix-equilibrium']=='unknown'


def test_tampering_stale_pins_and_refinement() -> None:
    """Every native artifact, declared input and original result is pinned."""
    req=request();req.artifacts[0].artifact['nodes'][0]['mass']['value']=200
    with pytest.raises(ValueError,match='digest'): prepare_experiment(req)
    plan=prepare_experiment(request());tampered=deepcopy(plan)
    tampered.predictions[0].outputs[0].value='999'
    with pytest.raises(ValueError,match='integrity'): compare_experiment(tampered,[])
    refined=request();refined.revision=2;refined.parent_plan_sha256=plan.scientific_sha256
    refined.artifacts[0].artifact['nodes'][0]['mass']['value']=200
    next_plan=prepare_experiment(repin(refined))
    with pytest.raises(ValueError,match='different plan'): compare_experiment(next_plan,[trial(plan)])
    assert plan==prepare_experiment(request())


def test_renamed_and_reordered_nodes_are_data_not_dispatch_keys() -> None:
    """The same model works with unrelated IDs, order and equivalent units."""
    req=request();raw=json.dumps(req.model_dump(mode='json')).replace('warm','alpha').replace('cool','beta').replace('cup','vessel')
    req=ExperimentRequest.model_validate_json(raw)
    nodes=req.artifacts[0].artifact['nodes']
    nodes[0]['mass'].update(value=0.1,unit='kg');nodes[0]['specific_heat'].update(value=4180,unit='J/(kg*K)')
    nodes[0]['initial_temperature'].update(value=313.15,unit='K')
    req.evaluations[0].parameters['node_ids'].reverse()
    plan=prepare_experiment(repin(req))
    baseline=prepare_experiment(request())
    assert plan.predictions[0].raw['final_temperature_kelvin']==baseline.predictions[0].raw['final_temperature_kelvin']
    assert plan.input_requirements[0].value=='100'


def test_catalog_transport_roundtrip_and_legacy_hash_compatibility() -> None:
    """Discovery drives authoring; historical v1 requests keep their original serialization."""
    catalog=experiment_tool({'action':'catalog'})
    assert any(s['evaluator_id']=='adiabatic-equilibrium' for s in catalog['evaluators']['thermal'])
    assert {'water-mixing','vinegar-bicarbonate-declarative'} <= {b['benchmark_id'] for b in catalog['benchmarks']}
    payload={'action':'prepare','request':request().model_dump(mode='json')}
    plan=call_domain_tool('experiment',payload)
    report=call_domain_tool('experiment',{'action':'compare','comparison':{'plan':plan,'runs':[]}})
    assert report['plan_sha256']==plan['scientific_sha256']
    with pytest.raises(ValueError): experiment_tool({'action':'benchmark','benchmark_id':'../private'})


def test_named_chemistry_uses_same_generic_comparison() -> None:
    """The original case also works through named outputs, without legacy request fields."""
    req=ExperimentRequest.model_validate(experiment_tool({'action':'benchmark','benchmark_id':'vinegar-bicarbonate-declarative'}))
    plan=prepare_experiment(req)
    assert plan.prediction is None
    assert next(o for o in plan.predictions[0].outputs if o.output_id=='co2.formed_mass').value=='440095/420033'
    assert [i.value for i in plan.input_requirements]==['2','5/2']


def test_external_registered_evaluator_requires_no_experiment_code_changes() -> None:
    """An unrelated domain with viscosity output traverses the same scalar workflow."""
    from forma_core.domains.providers import ProviderMetadata, ArtifactReference, ArtifactIdentity
    from forma_core.domains.evaluation import EvaluationSpec, EvaluationRequest, PinnedArtifact
    class Provider:
        """Minimal explicit external adapter, installed only in this isolated registry."""
        metadata=ProviderMetadata(domain='rheology',ir_type='RheologyDesign',owner='test',provider_id='test.rheology',
            provider_version='1',schemas=[{'schema_id':'test.rheology','versions':['1']}],validation=True,evaluation=True)
        def describe(self, payload: dict[str, Any], reference: ArtifactReference) -> ArtifactIdentity:
            """Expose external artifact identity without assuming native schemas."""
            return ArtifactIdentity(artifact_id=payload['artifact_id'],domain='rheology',schema_id='test.rheology',schema_version='1',revision=payload['revision'])
        def validate(self, payload: dict[str, Any], reference: ArtifactReference) -> list:
            """Accept the small declared test payload."""
            return []
        def evaluation_specs(self) -> list[dict[str, Any]]:
            """Declare an independently owned model and output contract."""
            return [EvaluationSpec(evaluator_id='property-reference',version='1',description='Synthetic property adapter',parameter_schema={'type':'object'},outputs={'viscosity':'mPa*s'},limitations=['Software extensibility test']).model_dump(mode='json')]
        def evaluate_named(self, payload: dict[str, Any], reference: ArtifactReference, evaluation: EvaluationRequest) -> dict[str, Any]:
            """Return an unrelated named physical quantity through the common boundary."""
            return {'status':'pass','outputs':[{'output_id':'viscosity','value':str(payload['viscosity']),
                'dimension':'dynamic_viscosity','unit':'mPa*s','basis':'apparent_viscosity','status':'pass'}]}
    registry=create_registry();provider=Provider();registry.register(provider.metadata,provider)
    req=request();doc={'artifact_id':'unrelated-property','revision':'a','viscosity':12}
    pin=ArtifactReference(artifact_id='unrelated-property',domain='rheology',schema_id='test.rheology',schema_version='1',
                         provider_id='test.rheology',provider_version='1',revision='a',sha256=fingerprint(doc))
    req.artifacts=[PinnedArtifact(reference=pin,artifact=doc)]
    req.evaluations=[EvaluationRequest(evaluation_id='mix-equilibrium',artifact_id='unrelated-property',evaluator_id='property-reference',evaluator_version='1')]
    req.required_inputs=[]
    req.criteria[0].target.output_id='viscosity';req.criteria[0].basis='apparent_viscosity';req.criteria[0].unit='Pa*s';req.criteria[0].absolute_tolerance=0.002
    plan=prepare_experiment(req,registry=registry);run=trial(plan,0.012,'Pa*s')
    run.observations[0].target.output_id='viscosity';run.observations[0].basis='apparent_viscosity'
    run.observations[0].readings[0].resolution=0.0001;run.observations[0].readings[0].uncertainty=0.0001
    row=compare_experiment(plan,[run],registry=registry)['results'][0]
    assert row['reference']=='3/250' and row['status']=='pass'


def test_generic_saved_api_and_mcp_roundtrip(tmp_path: Any) -> None:
    """Thermal-only plans survive the same REST storage and MCP tools as chemistry."""
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apps.api.auth import UserContext,require_user_context
    from apps.api.experiments_api import router,artifact_storage
    from forma_core.persistence.project_artifacts import ProjectArtifactStorage
    from apps.api.opencode_mcp import _call_tool
    from forma_core.opencode.models import DomainToolArguments
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[require_user_context]=lambda: UserContext('test','owner','owner',True,False)
    app.dependency_overrides[artifact_storage]=lambda: ProjectArtifactStorage(storage_config={'enabled':True,'backend':'local','directory':str(tmp_path),'max_bytes':1000000})
    payload={'action':'prepare','request':request().model_dump(mode='json')}
    mcp=asyncio.run(_call_tool('forma.opencode.domains',DomainToolArguments(operation='experiment',payload=json.dumps(payload)),None))
    with TestClient(app) as client:
        saved=client.post('/experiments/plans',json={'request':payload['request']})
        assert saved.status_code==200,saved.text
        assert saved.json()['record']['plan']==mcp
        plan=ExperimentPlan.model_validate(mcp)
        response=client.post('/experiments/reports',json={'plan_record_sha256':saved.json()['sha256'],'runs':[trial(plan).model_dump(mode='json')]})
        assert response.status_code==200,response.text
        assert response.json()['record']['report']['results'][0]['status']=='pass'
        assert response.json()['record']['report']['physical_status']=='not_run'


def test_benchmark_suite_is_reproducible_and_not_physical() -> None:
    """The published six-case report is generated by the same public workflow."""
    from pathlib import Path
    from examples.experiments.run_benchmark import run_suite
    suite=json.loads(Path('examples/experiments/water-mixing-suite.json').read_text())
    report=run_suite(suite)
    assert report['passed']==report['total']==6
    assert report['physical_experiments_performed']==0
    assert report==json.loads(Path('examples/experiments/water-mixing-benchmark-report.json').read_text())


def test_preexisting_v1_plan_remains_byte_equivalent_after_parse() -> None:
    """Snapshot was frozen by c4ae002 before generalization, not regenerated by this implementation."""
    from pathlib import Path
    from forma_core.experiments.evaluation import verify_plan
    original=json.loads(Path('tests/projects/fixtures/empirical-v1-plan.json').read_text())
    parsed=ExperimentPlan.model_validate(original)
    assert parsed.model_dump(mode='json')==original
    assert verify_plan(parsed)==parsed


def test_unavailable_provider_does_not_disable_structural_integrity_checks() -> None:
    """Even with a recomputed hash, a falsely resolved input cannot pass as a valid plan."""
    plan=prepare_experiment(request());registry=create_registry()
    metadata=registry.metadata('thermal');metadata.availability='unavailable';registry.register(metadata,replace=True)
    plan.input_requirements[0].value='999'
    plan.scientific_sha256=fingerprint(plan.model_dump(mode='json',exclude={'scientific_sha256'}))
    with pytest.raises(ValueError,match='integrity'): compare_experiment(plan,[],registry=registry)

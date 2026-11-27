"""Causal movement and atomic interaction regression tests, independent of papermaking."""
from __future__ import annotations

from copy import deepcopy
import numpy as np
import pytest

from forma_core.processes.execution.interactions import InteractionSpec, InteractionRuntime, InteractionCommand
from forma_core.processes.execution.workcell import VirtualWorkcell
from .test_workcell_execution import small_case


def pose(x: float = 0., y: float = 0., z: float = 0.) -> list[list[float]]:
    """Construct a rigid fixture transform in millimetres."""
    matrix = np.eye(4); matrix[:3, 3] = [x, y, z]
    return matrix.tolist()


def fixture() -> InteractionSpec:
    """Create a hand, a portable object, and a finite bench patch."""
    return InteractionSpec.model_validate({'entities': [
        {'body_id': 'bench', 'kind': 'fixed', 'initial_pose_mm': pose()},
        {'body_id': 'hand', 'kind': 'actor', 'actions': ['move','grasp','carry','release','use','actuate'], 'initial_pose_mm': pose(0, 0, 10)},
        {'body_id': 'part', 'kind': 'movable', 'actions': ['grasp', 'carry', 'release'], 'initial_pose_mm': pose(0, 0, 10),
         'contact_mm': [[0, 0, -10]], 'initial_support': 'top'}],
        'surfaces': [{'surface_id': 'top', 'body_id': 'bench', 'bounds_xy_mm': [-50, -50, 50, 50], 'height_mm': 0}]})


def command(action: str, **kwargs: str) -> InteractionCommand:
    """Create a fixture interaction with explicit actor and target."""
    return InteractionCommand(action=action, entity='part', actor='hand', **kwargs)


def test_hand_motion_does_not_move_ungrasped_object() -> None:
    """Independent actor movement cannot drag a supported object."""
    rt = InteractionRuntime(fixture()); rt.update({'hand': pose(25, 0, 40)}, {})
    assert np.allclose(rt.poses['part'], pose(0, 0, 10))
    with pytest.raises(ValueError, match='Contact missing'):
        rt.commands([command('grasp')], 'remote-grasp')
    assert not rt.holders


def test_grasp_carry_place_release_and_idempotent_retry() -> None:
    """Carried poses derive from the hand, and release transfers support atomically."""
    rt = InteractionRuntime(fixture()); rt.commands([command('grasp')], 'pick')
    rt.commands([command('grasp')], 'pick')
    rt.update({'hand': pose(25, 0, 40)}, {})
    assert np.allclose(rt.poses['part'], pose(25, 0, 40))
    before = rt.snapshot()
    with pytest.raises(ValueError, match='Unsupported placement'):
        rt.commands([command('release', surface='top')], 'bad', after=True)
    assert rt.snapshot() == before
    rt.update({'hand': pose(25, 0, 10)}, {})
    rt.commands([command('release', surface='top')], 'place', after=True)
    rt.update({'hand': pose(-25, 0, 40)}, {})
    assert np.allclose(rt.poses['part'], pose(25, 0, 10))


def test_support_outside_patch_and_duplicate_hand_rejected() -> None:
    """A plane alone does not establish finite support or permit two unrelated grasps."""
    spec = fixture(); extra = spec.entities[-1].model_copy(deep=True); extra.body_id = 'second'
    spec.entities.append(extra); rt = InteractionRuntime(spec)
    rt.commands([command('grasp')], 'pick')
    with pytest.raises(ValueError, match='occupied hand'):
        rt.commands([InteractionCommand(action='grasp', entity='second', actor='hand')], 'second')
    rt.update({'hand': pose(100, 0, 10)}, {})
    with pytest.raises(ValueError, match='Unsupported placement'):
        rt.commands([command('release', surface='top')], 'outside', after=True)


def test_fixed_and_movable_axes_are_rejected_before_dispatch() -> None:
    """An independent body pose axis cannot bypass the strict interaction graph."""
    data = small_case(); body = next(a['joint']['body_ids'][0] for a in data['workcell']['machine']['definition']['axes'] if a.get('joint'))
    spec = fixture().model_dump(mode='json'); spec['entities'][0]['body_id'] = body; spec['surfaces'][0]['body_id'] = body
    data['workcell']['interactions'] = spec
    with pytest.raises(ValueError, match='Nonactor poses'):
        VirtualWorkcell.model_validate(data['workcell'])


def test_mechanism_requires_contact_and_actuation() -> None:
    """A declared drive axis cannot move a mechanism without its actor action."""
    spec = fixture(); spec.entities[-1].kind = 'mechanism'; spec.entities[-1].initial_support = None
    spec.entities[-1].actions = ['actuate']; data = spec.model_dump(mode='json')
    data['entities'].append({'body_id':'control','kind':'fixed','initial_pose_mm':pose(0,0,10)})
    data['mechanisms'] = [{'body_id': 'part', 'input_axis': 'crank', 'ratio_mm_per_unit': 1., 'direction': [0, 0, 1], 'travel_mm': [0, 30], 'control_body': 'control'}]
    rt = InteractionRuntime(InteractionSpec.model_validate(data))
    with pytest.raises(ValueError, match='without actuation'):
        rt.update({'hand': pose(0, 0, 10)}, {'crank': 10})
    rt.commands([command('actuate')], 'turn')
    rt.update({'hand': pose(0, 0, 10)}, {'crank': 10})
    assert rt.poses['part'][2, 3] == 20
    rt.commands([], 'next')
    with pytest.raises(ValueError, match='without actuation'):
        rt.update({'hand': pose(0, 0, 10)}, {'crank': 11})


def test_capability_and_cycle_validation() -> None:
    """Invalid capabilities and cyclic support declarations fail schema validation."""
    data = fixture().model_dump(mode='json'); data['entities'][0]['actions'] = ['grasp']
    with pytest.raises(ValueError, match='Fixed entities'):
        InteractionSpec.model_validate(data)
    data = fixture().model_dump(mode='json'); data['surfaces'][0]['body_id'] = 'part'
    with pytest.raises(ValueError, match='Cyclic support'):
        InteractionSpec.model_validate(data)


def test_process_completion_is_blocked_by_failed_placement() -> None:
    """A successful machine move cannot complete a process with an invalid release."""
    from forma_core.processes.execution.tools import process_runtime_tool
    from forma_core.processes.execution.workcell import IdealMachineHost
    from forma_core.processes.execution.kinematics import body_transforms
    data = small_case(); spec = VirtualWorkcell.model_validate(data['workcell'])
    positions = {a.axis_id: a.neutral for a in spec.machine.definition.axes}
    transforms = body_transforms(spec, positions)
    actor = next(iter(transforms)); location = transforms[actor][:3, 3]
    entities = [{'body_id': body, 'kind': 'actor', 'actions': ['move','grasp','carry','release','use','actuate'], 'initial_pose_mm': value.tolist()} for body, value in transforms.items()]
    entities.extend([
        {'body_id':'table','kind':'fixed','initial_pose_mm':pose(*location)},
        {'body_id':'payload','kind':'movable','actions':['grasp','carry','release'],'initial_pose_mm':transforms[actor].tolist(),'contact_mm':[[0,0,0]],'initial_support':'tabletop'}])
    data['workcell']['interactions'] = {'entities':entities,'surfaces':[
        {'surface_id':'tabletop','body_id':'table','bounds_xy_mm':[-100,-100,100,100],'height_mm':0},
        {'surface_id':'unreachable','body_id':'table','bounds_xy_mm':[-100,-100,100,100],'height_mm':1000}]}
    first=data['workcell']['phases'][0]
    first['before']=[{'action':'grasp','entity':'payload','actor':actor}]
    first['after']=[{'action':'release','entity':'payload','actor':actor,'surface':'unreachable'}]
    result=process_runtime_tool(data)
    # Tool uses the same runtime entry point as the portable CLI.
    assert result['runtime']['status'] != 'completed'
    assert not result['phase_receipts']
    assert 'Unsupported placement' in str(result)


def test_inverting_ordinary_support_is_rejected_but_retention_is_explicit() -> None:
    """Support cannot silently behave as glue; retained fixtures need a declaration."""
    data=fixture().model_dump(mode='json')
    data['entities'].append({'body_id':'load','kind':'movable','actions':['grasp','carry','release'],'initial_pose_mm':pose(0,0,10),'contact_mm':[[0,0,0]],'initial_support':'carrier'})
    data['surfaces'].append({'surface_id':'carrier','body_id':'part','bounds_xy_mm':[-20,-20,20,20],'height_mm':0})
    rt=InteractionRuntime(InteractionSpec.model_validate(data));rt.commands([command('grasp')],'pick')
    inverted=np.diag([1.,-1.,-1.,1.]);inverted[:3,3]=[0,0,30]
    with pytest.raises(ValueError,match='upward-facing'):
        rt.update({'hand':inverted}, {})
    data['entities'][-1]['initial_retention']=True
    rt=InteractionRuntime(InteractionSpec.model_validate(data));rt.commands([command('grasp')],'pick')
    rt.update({'hand':inverted}, {})
    assert rt.snapshot()['relationships']['load']['kind']=='retained'


def test_fixed_entities_cannot_inherit_motion() -> None:
    """A fixed object cannot bypass immobility by naming a moving support."""
    data=fixture().model_dump(mode='json');data['entities'][0]['initial_support']='top'
    with pytest.raises(ValueError,match='Only movable'):
        InteractionSpec.model_validate(data)


def test_compact_polling_preserves_full_event_history() -> None:
    """Lightweight polling retains the same state and event-chain head without data loss."""
    from forma_core.processes.execution.models import ProcessExecutionPlan
    from forma_core.processes.execution.runtime import ProcessRuntime
    data=small_case();runtime=ProcessRuntime(ProcessExecutionPlan.model_validate(data['plan']),'compact')
    runtime.start(runtime.ready_steps()[0]);full=runtime.snapshot();compact=runtime.snapshot(include_events=False)
    assert compact['events']==[] and compact['event_count']==len(full['events'])
    assert compact['last_event_sha256']==full['events'][-1]['sha256']
    assert {k:v for k,v in full.items() if k!='events'}=={k:v for k,v in compact.items() if k!='events'}
    assert runtime.snapshot()==full

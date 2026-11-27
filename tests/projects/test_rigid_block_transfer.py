"""Independent, data-only human block-transfer regression over native runtimes."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from forma_core.processes.execution.tools import process_runtime_tool

ROOT=Path(__file__).resolve().parents[2]


def request() -> dict:
    """Load the independently authored four-step, seven-action fixture."""
    return json.loads((ROOT/'examples/process_runtime/rigid-block-transfer/request.json').read_text())


def test_block_transfer_keeps_grasp_and_inventory_coherent() -> None:
    """A carried body follows its hand, then stays on its tray after withdrawal."""
    result=process_runtime_tool(request())
    assert result['runtime']['status']=='completed'
    assert len(result['phase_receipts'])==7
    offset=None
    for frame in result['frames']:
        state=frame['interactions'];relation=state['relationships']['block'];poses=state['poses_mm']
        if relation['kind']=='grasp':
            assert relation['parent']=='right-hand'
            current=np.linalg.inv(poses['right-hand'])@np.array(poses['block'])
            if offset is None:offset=current
            assert np.allclose(current,offset,atol=1e-8)
        assert sum(c['mass_g'] for c in frame['materials']['containers'].values())==300
    assert result['materials']['containers']['destination']['mass_g']==300
    assert result['materials']['containers']['in-hand']['mass_g']==0
    assert result['materials']['residual_g']==0
    last=result['frames'][-1]['interactions']
    assert last['relationships']['block']=={'parent':'tray-b','kind':'support'}
    assert np.allclose(np.asarray(last['poses_mm']['block'])[:3,3],[180,-80,916.5])


def test_remote_grasp_cannot_move_material() -> None:
    """Skipping reach fails at grasp, with the block still in its source inventory."""
    data=request();data['workcell']['phases']=[p for p in data['workcell']['phases'] if p['phase_id']!='reach']
    result=process_runtime_tool(data)
    assert result['runtime']['status']=='failed'
    assert 'Contact missing' in str(result)
    assert result['materials']['containers']['source']['mass_g']==300
    assert not result['phase_receipts']


def test_release_in_midair_cannot_commit_destination_inventory() -> None:
    """A raised endpoint cannot release a block or complete the placement step."""
    data=request();lower=next(a for a in data['workcell']['machine']['actions'] if a['action_id']=='lower')
    lower['targets']['right-hand__z']+=80
    result=process_runtime_tool(data)
    assert result['runtime']['status']=='failed'
    assert 'Unsupported placement' in str(result)
    assert result['materials']['containers']['destination']['mass_g']==0
    assert result['materials']['containers']['in-hand']['mass_g']==300
    assert result['frames'][-1]['interactions']['relationships']['block']=={'parent':'right-hand','kind':'grasp'}


def test_omitting_interactions_fails_closed() -> None:
    """Removing both declarations and hooks cannot silently select legacy execution."""
    import pytest
    data = request()
    data['workcell'].pop('interactions')
    for phase in data['workcell']['phases']:
        phase.pop('before', None)
        phase.pop('after', None)
    with pytest.raises(ValueError, match='Strict workcells require'):
        process_runtime_tool(data)
    data['workcell']['verification_mode'] = 'legacy-unverified'
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'completed'
    assert result['interaction_verification'] == 'unverified'
    assert result['physical_verification'] == 'not_run'


def test_transfer_requires_entity_and_matching_action() -> None:
    """Dropping transfer identity or interaction hooks fails before host dispatch."""
    import pytest
    for field in ['entity', 'before']:
        data = request()
        phase = next(p for p in data['workcell']['phases'] if p['phase_id'] == 'grasp')
        if field == 'entity':
            phase['transfer'].pop('entity')
        else:
            phase.pop('before')
        with pytest.raises(ValueError, match='Strict transfers|matching grasp'):
            process_runtime_tool(data)


def test_transfer_cannot_debit_unrelated_container() -> None:
    """Sufficient inventory in a different tray is not evidence of source ownership."""
    data = request()
    containers = data['workcell']['containers']
    containers[0]['contents_g'] = {}
    containers[2]['contents_g'] = {'wood': 300}
    phase = next(p for p in data['workcell']['phases'] if p['phase_id'] == 'grasp')
    phase['transfer']['source'] = 'destination'
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'failed'
    assert 'Transfer source' in result['stop_reason']
    assert result['materials']['containers']['destination']['mass_g'] == 300
    assert result['materials']['containers']['in-hand']['mass_g'] == 0


def test_undeclared_motion_body_and_wrong_target_rejected() -> None:
    """Neither missing motion entities nor unrelated transfer destinations are allowed."""
    import pytest
    data = request()
    data['workcell']['interactions']['entities'] = [e for e in data['workcell']['interactions']['entities'] if e['body_id'] != 'head']
    with pytest.raises(ValueError, match='Every motion body'):
        process_runtime_tool(data)
    data = request()
    phase = next(p for p in data['workcell']['phases'] if p['phase_id'] == 'release')
    phase['transfer']['target'] = 'source'
    with pytest.raises(ValueError, match='matching grasp'):
        process_runtime_tool(data)

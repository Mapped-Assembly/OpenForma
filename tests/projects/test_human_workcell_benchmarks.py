"""Independent round-trip and retained-web declarations over the native runtime."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import numpy as np
import pytest
from forma_core.processes.execution.tools import process_runtime_tool

ROOT = Path(__file__).resolve().parents[2] / 'examples/process_runtime'


def request(name: str) -> dict[str, Any]:
    """Load a portable benchmark without renderer or authoring dependencies."""
    return json.loads((ROOT / name / 'request.json').read_text())


@pytest.mark.parametrize('name,count,payload,parent,mass', [
    ('human-round-trip', 14, 'block', 'tray-a', 300.),
    ('mould-to-felt', 11, 'sheet-01', 'felt-stack', 4.51612),
])
def test_valid_handling_completes(name: str, count: int, payload: str, parent: str, mass: float) -> None:
    """Success requires all phases, conserved mass and persistent final support."""
    result = process_runtime_tool(request(name))
    assert result['runtime']['status'] == 'completed'
    assert len(result['phase_receipts']) == count
    assert result['verification_mode'] == 'strict'
    for frame in result['frames']:
        assert sum(c['mass_g'] for c in frame['materials']['containers'].values()) == pytest.approx(mass)
    last = result['frames'][-1]['interactions']
    assert last['relationships'][payload] == {'parent': parent, 'kind': 'support'}
    if name == 'mould-to-felt':
        placed = [f['interactions']['poses_mm'][payload] for f in result['frames']
                  if f['interactions']['relationships'][payload]['parent'] == parent]
        assert placed and all(np.allclose(p, placed[0]) for p in placed)
    assert result['physical_verification'] == 'not_run'


@pytest.mark.parametrize('mutation,reason', [
    ('no-retention', 'upward-facing'),
    ('midair', 'Unsupported placement'),
])
def test_invalid_couching_preserves_inventory(mutation: str, reason: str) -> None:
    """A detached or unsupported web cannot be credited to the felt."""
    data = request('mould-to-felt')
    if mutation == 'no-retention':
        next(e for e in data['workcell']['interactions']['entities'] if e['body_id'] == 'sheet-01')['initial_retention'] = False
    else:
        next(a for a in data['workcell']['machine']['actions'] if a['action_id'] == 'action-0070')['targets']['right-hand__z'] += 50
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'failed'
    assert reason in result['stop_reason']
    assert result['materials']['containers']['on-felt']['mass_g'] == 0
    assert result['materials']['containers']['on-mould']['mass_g'] == pytest.approx(4.51612)


def test_couching_without_release_is_rejected_before_execution() -> None:
    """Accounting alone cannot stand in for a release action."""
    data = request('mould-to-felt')
    data['workcell']['phases'][4]['after'] = []
    with pytest.raises(ValueError, match='matching grasp or supported release'):
        process_runtime_tool(data)

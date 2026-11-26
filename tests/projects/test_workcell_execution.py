"""Regression tests for generic feedback-driven workcell execution and inventory."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from forma_core.domains.base import fingerprint
from forma_core.processes.execution.models import ProcessExecutionPlan, StepSubmission
from forma_core.processes.execution.tools import process_runtime_tool
from forma_core.processes.execution.workcell import (
    VirtualWorkcell, MaterialLedger, Phase, IdealMachineHost, WorkcellAdapter,
)
from forma_core.processes.execution.runtime import ProcessRuntime

ROOT = Path(__file__).resolve().parents[2]


def example() -> dict:
    """Read the same portable declarations used to produce the user-facing run."""
    return json.loads((ROOT/'examples/process_runtime/workcell/request.json').read_text())


def small_case() -> dict:
    """Reduce animation duration without replacing either runtime or action feedback."""
    data = example()
    phases = data['workcell']['phases']
    keep = []
    for step in data['plan']['process']['steps']:
        choices = [p for p in phases if p['step_id'] == step['step_id']]
        keep.append(next((p for p in choices if p.get('transfer')), choices[0]))
    data['workcell']['phases'] = keep
    for action in data['workcell']['machine']['actions']:
        action['completion']['dwell_s'] = .04
        action['targets'] = {k: 0. for k in action['targets']}
    return data


def test_real_machine_feedback_gates_transfers_and_process() -> None:
    """Neither dispatch nor merely starting a step is sufficient to move material."""
    data = small_case()
    plan = ProcessExecutionPlan.model_validate(data['plan'])
    spec = VirtualWorkcell.model_validate(data['workcell'])
    host = IdealMachineHost(spec.machine)
    adapter = WorkcellAdapter(spec, host)
    rt = ProcessRuntime(plan, 'feedback-test', adapter=adapter)
    for sid in ['prepare', 'inputs']:
        rt.start(sid)
        submission = StepSubmission.model_validate(data['submissions'][sid])
        submission.submission_id = sid+'-dispatch'
        assert rt.submit(sid, submission)['status'] == 'unknown'
        assert adapter.ledger.snapshot()['containers']['reaction-bowl']['mass_g'] == 0
        host.advance(.2)
        submission.submission_id = sid+'-poll'
        assert rt.submit(sid, submission)['status'] == 'completed'
    assert adapter.ledger.snapshot()['containers']['reaction-bowl']['mass_g'] == 50
    assert all(p['receipt']['evidence']['feedback_sequence'] > 0 for p in adapter.completed)


def test_interrupted_action_does_not_transfer_or_unlock_downstream() -> None:
    """A stopped machine produces a failed process step, not an animated success."""
    data = small_case()
    plan = ProcessExecutionPlan.model_validate(data['plan'])
    spec = VirtualWorkcell.model_validate(data['workcell'])
    host = IdealMachineHost(spec.machine)
    adapter = WorkcellAdapter(spec, host)
    rt = ProcessRuntime(plan, 'interrupted', adapter=adapter)
    rt.start('prepare')
    s = StepSubmission.model_validate(data['submissions']['prepare'])
    rt.submit('prepare', s)
    host.command('hold')
    s.submission_id = 'after-hold'
    assert rt.submit('prepare', s)['status'] == 'failed'
    assert not rt.ready_steps()
    assert adapter.completed == []
    assert adapter.ledger.snapshot()['containers']['reaction-bowl']['mass_g'] == 0


def test_closed_inventory_conservation_and_idempotency() -> None:
    """Transfers preserve each constituent across partial transfers and retries."""
    spec = VirtualWorkcell.model_validate(example()['workcell'])
    ledger = MaterialLedger(spec.containers)
    original = ledger.totals()
    phase = next(p for p in spec.phases if p.transfer)
    phase.transfer.mass_g = 12.345
    ledger.apply(phase)
    snapshot = ledger.snapshot()
    ledger.apply(phase)
    assert ledger.snapshot() == snapshot
    assert ledger.totals() == original
    assert snapshot['residual_g'] == 0
    assert snapshot['containers']['reaction-bowl']['mass_g'] == 12.345


@pytest.mark.parametrize('mass,match', [(51, 'source'), (50, 'capacity')])
def test_inventory_rejections_are_atomic(mass: float, match: str) -> None:
    """Overdraw/overflow leave all inventory and effect identities unchanged."""
    spec = VirtualWorkcell.model_validate(example()['workcell'])
    if match == 'capacity':
        spec.containers[-1].capacity_g = 49
    ledger = MaterialLedger(spec.containers)
    before = ledger.snapshot()
    phase = next(p for p in spec.phases if p.transfer)
    phase.transfer.mass_g = mass
    with pytest.raises(ValueError, match=match):
        ledger.apply(phase)
    assert ledger.snapshot() == before
    assert not ledger.applied


def test_reuse_with_unseen_material_names_and_quantities() -> None:
    """The same engine accepts a different conservative process without name branches."""
    data = small_case()
    data['plan']['experiment'] = None
    for b in data['plan']['bindings']:
        b['evaluations'] = []; b['required_criteria'] = []
    for s in data['submissions'].values():
        s['observations'] = []
    containers = data['workcell']['containers']
    containers[0]['contents_g'] = {'water': 17., 'salt': 3.}
    containers[1]['contents_g'] = {'pigment': 1.}
    for p in data['workcell']['phases']:
        if p.get('transfer'):
            p['transfer']['mass_g'] = 10. if p['transfer']['source'] == 'vinegar-dose' else 1.
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'completed'
    assert result['materials']['containers']['reaction-bowl']['contents_g'] == {'water': 8.5, 'salt': 1.5, 'pigment': 1.}
    assert result['materials']['residual_g'] == 0
    assert result['physical_verification'] == 'not_run'


def test_render_uses_feedback_not_commanded_positions() -> None:
    """Parent translations compose with rotation about the declared rest origin."""
    np = pytest.importorskip('numpy')
    from forma_core.processes.execution.render import body_transforms
    spec = VirtualWorkcell.model_validate(example()['workcell'])
    positions = {a.axis_id: 0. for a in spec.machine.definition.axes}
    positions.update(cup_x=-95., cup_y=-35., cup_z=140., cup_tilt=90.)
    matrix = body_transforms(spec, positions)['cup']
    assert np.allclose(matrix @ np.array([65., 80., 68., 1.]), [-30., 45., 208., 1.])
    positions['cup_z'] = None
    with pytest.raises(ValueError, match='feedback'):
        body_transforms(spec, positions)


def test_portable_simulation_never_allows_hardware() -> None:
    """A JSON caller cannot use this simulation entry point to dispatch physical hardware."""
    data = small_case()
    data['workcell']['machine']['definition']['backend'] = 'physical'
    with pytest.raises(ValueError):
        process_runtime_tool(data)


def test_unresolved_phase_and_stream_rejected_before_host() -> None:
    """Misspelled step/stream bindings cannot silently become visual-only actions."""
    for target in ['step', 'stream']:
        data = small_case()
        phase = next(p for p in data['workcell']['phases'] if p.get('transfer'))
        if target == 'step':
            phase['step_id'] = 'absent'
        else:
            phase['transfer']['stream_id'] = 'absent'
        with pytest.raises(ValueError):
            process_runtime_tool(data)


def test_missing_condition_prevents_motion() -> None:
    """Runtime condition checks remain authoritative over the new adapter."""
    data = small_case()
    data['plan']['bindings'][0]['conditions'] = [dict(name='missing',dimension='mass',unit='g',minimum=0.,maximum=1.)]
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'cancelled'
    assert not result['phase_receipts']
    assert result['frames'][0]['machine_action'] is None


def test_trace_and_geometry_tamper_rejected(tmp_path: Path) -> None:
    """Rendering refuses changed evidence or unpinned CAD geometry."""
    pytest.importorskip('numpy'); pytest.importorskip('PIL')
    from forma_core.processes.execution.render import render_workcell
    data = small_case()
    trace = process_runtime_tool(data)
    scene = json.loads((ROOT/'examples/process_runtime/workcell/scene.json').read_text())
    changed = deepcopy(trace); changed['frames'][0]['positions']['cup_x'] = 99.
    with pytest.raises(ValueError, match='hash'):
        render_workcell(data, changed, scene, tmp_path/'bad.gif')
    scene['bodies'][0]['color'] = [1., 0., 0.]
    with pytest.raises(ValueError, match='Geometry'):
        render_workcell(data, trace, scene, tmp_path/'bad.gif')


def test_full_example_end_to_end() -> None:
    """Run all 27 phases, preserving the original scientific plan and synthetic provenance."""
    data = example()
    result = process_runtime_tool(data)
    assert result['runtime']['status'] == 'completed'
    assert len(result['phase_receipts']) == 27
    assert len({r['receipt']['run_id'] for r in result['phase_receipts']}) == 27
    assert result['materials']['containers']['reaction-bowl']['mass_g'] == 52
    assert result['materials']['containers']['reaction-bowl']['agitation_performed']
    assert result['comparison']['physical_status'] == 'not_run'
    assert result['materials']['reaction_state'] == 'unsupported'
    assert result['materials']['mixing_homogeneity'] == 'unknown'
    assert fingerprint({k:v for k,v in result.items() if k != 'trace_sha256'}) == result['trace_sha256']
    assert any(f['positions']['cup_z'] > 100 for f in result['frames'])
    assert all(r['receipt']['status'] == 'completed' for r in result['phase_receipts'])


def test_depth_buffer_resolves_crossing_triangles() -> None:
    """Per-pixel depth avoids average-depth sorting artifacts in the native CAD meshes."""
    np = pytest.importorskip('numpy')
    from forma_core.processes.execution.render import rasterize
    a = np.array([[0., 150., 10.], [20., 150., -10.], [0., 170., 10.]])
    b = np.array([[0., 150., 0.], [20., 150., 0.], [0., 170., 0.]])
    pixels = rasterize([(0., a, (255, 0, 0)), (0., b, (0, 255, 0))], 30, 180)
    assert pixels[151, 1].tolist() == [255, 0, 0]
    assert pixels[151, 15].tolist() == [0, 255, 0]

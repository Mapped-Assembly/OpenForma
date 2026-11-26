"""Process execution tests: provenance, dependencies, batches and real machine delegation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json

import pytest

from forma_core.domains.base import fingerprint
from forma_core.domains.tools import call_domain_tool
from forma_core.processes.execution import (ProcessExecutionPlan, ProcessRuntime, StepSubmission,
    VirtualProcessAdapter, MachineProcessAdapter)
from forma_core.processes.execution.tools import process_runtime_tool

HERE = Path(__file__).parents[2] / 'examples/process_runtime'


def example(name: str = 'vinegar-bicarbonate-declarative') -> dict:
    """Read authored data; no benchmark-specific runtime is imported."""
    return json.loads((HERE/(name+'.json')).read_text())


def minimal() -> ProcessExecutionPlan:
    """A process with two dependent steps and one explicitly bounded observation."""
    return ProcessExecutionPlan.model_validate({'process': {'artifact_id': 'unseen-process',
        'operations': [{'operation_id': 'op', 'operation_type': 'unseen-operation'}],
        'steps': [{'step_id': 'a', 'operation_id': 'op', 'preconditions': ['ready']},
                  {'step_id': 'b', 'operation_id': 'op', 'depends_on': ['a']}]},
        'bindings': [{'step_id': 'a', 'readings': [{'name': 'temperature', 'dimension': 'temperature',
                     'unit': 'degC', 'minimum': 19, 'maximum': 21}]}, {'step_id': 'b'}]})


def submission(sid: str = 'one', *, measured: bool = False) -> StepSubmission:
    """Explicit test-only records, never physical benchmark evidence."""
    source = {'source_id': sid, 'basis': 'measured' if measured else 'illustrative', 'title': 'Unit test fixture',
              'publisher': 'test harness', 'locator': 'test-only', 'retrieved_at': '2026-01-01T00:00:00Z',
              'undated_reason': 'Synthetic test data exercising provenance checks.', 'rationale': 'Software fixture only.'}
    return StepSubmission.model_validate({'submission_id': sid, 'actor': 'test fixture', 'acknowledged': True,
        'confirmed_preconditions': ['ready'], 'readings': {'temperature': {'value': 293.15, 'unit': 'K',
        'instrument': 'test-only', 'resolution': .01, 'uncertainty': .1}},
        'reading_evidence': {'temperature': [sid]}, 'evidence': [source]})


@pytest.mark.parametrize('name', ['vinegar-bicarbonate-declarative', 'water-mixing'])
def test_end_to_end_json(name: str) -> None:
    """Two independent domains share execution, comparison and synthetic evidence rules."""
    result = call_domain_tool('process-runtime', example(name))
    assert result['runtime']['status'] == 'completed'
    assert result['comparison']['physical_status'] == 'not_run'
    assert result['comparison']['verification_coverage'] == 0
    previous = None
    for event in result['runtime']['events']:
        assert event['previous_sha256'] == previous
        assert event['sha256'] == fingerprint({k: v for k, v in event.items() if k != 'sha256'})
        previous = event['sha256']


def test_dependencies_unknown_conditions_and_idempotency() -> None:
    """Missing evidence blocks dependents; duplicate submission IDs cannot execute twice."""
    rt = ProcessRuntime(minimal(), 'run', adapter=VirtualProcessAdapter())
    with pytest.raises(ValueError): rt.start('b')
    rt.start('a')
    missing = submission(); missing.confirmed_preconditions = []
    assert rt.submit('a', missing)['status'] == 'unknown'
    assert rt.ready_steps() == []
    valid = submission('two')
    result = rt.submit('a', valid)
    assert result['status'] == 'completed'
    count = len(rt.snapshot()['events'])
    assert rt.submit('a', valid) == result
    assert len(rt.snapshot()['events']) == count
    valid.actor = 'edited'
    with pytest.raises(ValueError, match='different content'): rt.submit('a', valid)
    assert rt.ready_steps() == ['b']


@pytest.mark.parametrize('change', ['uncertainty', 'units', 'bounds'])
def test_reading_gates(change: str) -> None:
    """Unknown precision, incompatible dimensions and out-of-range readings do not pass."""
    rt = ProcessRuntime(minimal(), 'run', adapter=VirtualProcessAdapter()); rt.start('a')
    s = submission()
    if change == 'uncertainty': s.readings['temperature'].uncertainty = None
    if change == 'units': s.readings['temperature'].unit = 'g'
    if change == 'bounds': s.readings['temperature'].value = 330
    assert rt.submit('a', s)['status'] == 'unknown'


def test_manual_requires_real_provenance_and_separate_verification() -> None:
    """A manual acknowledgement is workflow evidence, not physical verification."""
    rt = ProcessRuntime(minimal(), 'manual'); rt.start('a')
    with pytest.raises(ValueError, match='measured'): rt.submit('a', submission())
    assert rt.submit('a', submission(measured=True))['status'] == 'completed'
    assert rt.snapshot()['physical_verification'] == 'not_evaluated'


def test_cancel_and_snapshot_isolation() -> None:
    """Cancellation blocks execution; returned mutable snapshots cannot change runtime state."""
    plan = minimal(); rt = ProcessRuntime(plan, 'run', adapter=VirtualProcessAdapter())
    plan.process.steps.clear()
    rt.start('a'); snapshot = rt.snapshot(); snapshot['steps']['a'] = 'completed'
    assert rt.snapshot()['steps']['a'] == 'running'
    rt.cancel()
    assert rt.ready_steps() == []
    with pytest.raises(ValueError): rt.submit('a', submission())


def test_native_revision_mismatch_and_bad_bindings() -> None:
    """Reject drift from frozen native inputs and dangling completion obligations."""
    data = example()['plan']; data['process']['revision'] += 1
    with pytest.raises(ValueError): ProcessExecutionPlan.model_validate(data)
    data = minimal().model_dump(mode='json'); data['bindings'][0]['required_batch_streams'] = ['absent']
    with pytest.raises(ValueError): ProcessExecutionPlan.model_validate(data)


def test_unknown_virtual_evaluator_stays_blocked() -> None:
    """An unavailable model cannot be substituted with a previously cached successful value."""
    from forma_core.domains.providers import DomainRegistry
    data = example()
    result = process_runtime_tool(data, registry=DomainRegistry())
    assert result['runtime']['status'] != 'completed'
    assert result['submissions'][-1]['status'] in {'unknown', 'unsupported'}


def test_batch_dimension_lineage_and_evidence() -> None:
    """Material observations stay typed and cannot invent source lots or evidence."""
    data = example(); plan = ProcessExecutionPlan.model_validate(data['plan'])
    for mutation in ['unit', 'parent', 'source']:
        rt = ProcessRuntime(plan, mutation, adapter=VirtualProcessAdapter())
        command = data['commands'][1]; rt.start(command['step_id'])
        s = StepSubmission.model_validate(command['submission'])
        assert s.batches
        if mutation == 'unit': s.batches[0].quantity.unit = 'degC'
        if mutation == 'parent': s.batches[0].parent_batch_ids = ['not-recorded']
        if mutation == 'source': s.batches[0].evidence_ids = ['absent']
        with pytest.raises(ValueError): rt.submit(command['step_id'], s)


class LocalMachineClient:
    """Exercise the actual MachineRuntime command/lease/feedback path without HTTP."""

    def __init__(self) -> None:
        """The host explicitly claims and arms an ideal machine for this test."""
        from tests.control.test_package import package_fixture
        from forma_core.control import MachineRuntime
        self.package = package_fixture(); self.time = 0.; self.seq = 0; self.dispatches = 0
        self.runtime = MachineRuntime(self.package.definition, package=self.package, clock=lambda: self.time)
        self.session = self.runtime.claim('process test')['session_id']
        self.command('arm')

    def capabilities(self) -> dict:
        """Return the existing control service contract."""
        return {'machine_package': self.package.model_dump(mode='json')}

    def command(self, op: str, **parameters: object) -> dict:
        """Forward commands with fresh leases and strictly increasing sequences."""
        if op == 'action': self.dispatches += 1
        lease = self.runtime.lease(self.session)
        packet = {'protocol_version': 2, 'session_id': self.session, 'lease_id': lease['lease_id'],
                  'seq': self.seq, 'op': op, **parameters}
        self.seq += 1
        return self.runtime.command(packet)

    def action_result(self, run_id: str) -> dict:
        """Read evidence produced by the real action engine."""
        return self.runtime.action_execution.snapshot(run_id)

    def advance(self) -> None:
        """Advance the ideal machine while the host maintains command traffic."""
        for _ in range(30):
            self.time += .02; self.command('heartbeat'); self.runtime.tick()


def machine_case() -> tuple[ProcessRuntime, LocalMachineClient]:
    """Bind a generic process step to a pinned existing machine action."""
    client = LocalMachineClient(); plan = minimal()
    from forma_core.processes.execution import MachineBinding
    plan.bindings[0].machine = MachineBinding.model_validate({'machine_id': 'fixture', 'package_sha256': client.package.sha256,
                                'action_id': 'position', 'parameters': {'distance': 1.}})
    plan = ProcessExecutionPlan.model_validate(plan.model_dump(mode='json'))
    return ProcessRuntime(plan, 'machine', adapter=MachineProcessAdapter(client, 'fixture')), client


def test_machine_feedback_once_and_provenance() -> None:
    """Actual machine feedback completes the process step; polling never redispatches."""
    rt, client = machine_case(); rt.start('a')
    assert rt.submit('a', submission())['status'] == 'unknown'
    assert client.dispatches == 1
    client.advance()
    result = rt.submit('a', submission('poll'))
    assert result['status'] == 'completed', result
    assert result['adapter']['provenance'] == 'simulated'
    assert client.dispatches == 1


def test_machine_wrong_provenance_never_dispatches() -> None:
    """Do not move a machine for inputs whose asserted provenance is invalid."""
    rt, client = machine_case(); rt.start('a')
    with pytest.raises(ValueError): rt.submit('a', submission(measured=True))
    assert client.dispatches == 0


def test_machine_ambiguous_dispatch_never_retried() -> None:
    """An ambiguous transport failure remains unknown even with a new submission ID."""
    rt, client = machine_case(); rt.start('a')
    original = client.command
    def failed(op: str, **parameters: object) -> dict:
        """Simulate a server accepting an action before the connection is lost."""
        original(op, **parameters)
        raise ConnectionError('lost reply')
    client.command = failed
    assert rt.submit('a', submission())['status'] == 'unknown'
    client.command = original
    assert rt.submit('a', submission('retry'))['status'] == 'unknown'
    assert client.dispatches == 1


def test_machine_cancel_requests_hold() -> None:
    """Process cancellation propagates Hold rather than merely changing UI state."""
    rt, client = machine_case(); rt.start('a'); rt.submit('a', submission())
    rt.cancel()
    assert client.runtime.state == 'HOLD'
    assert rt.snapshot()['status'] == 'cancelled'


def test_tool_rejects_implicit_machine_execution() -> None:
    """Portable transcript calls cannot construct arbitrary network/physical adapters."""
    data = example(); data['mode'] = 'machine'
    with pytest.raises(ValueError): process_runtime_tool(data)


def test_mcp_dispatch_uses_process_runtime() -> None:
    """The existing authenticated MCP handler recognizes the new domain operation."""
    import asyncio
    from apps.api.opencode_mcp import _call_tool, opencode_mcp_tools
    from forma_core.opencode.models import DomainToolArguments
    payload = example('water-mixing')
    result = asyncio.run(_call_tool('forma.opencode.domains', DomainToolArguments(
        operation='process-runtime', payload=json.dumps(payload)), None))
    assert result['runtime']['status'] == 'completed'
    assert result['comparison']['physical_status'] == 'not_run'
    tool = next(t for t in opencode_mcp_tools() if t['name'] == 'forma.opencode.domains')
    assert 'process-runtime' in tool['inputSchema']['properties']['operation']['enum']


def test_invalid_preaction_condition_does_not_dispatch_machine() -> None:
    """Conditions gate side effects, rather than checking only after motion finishes."""
    client = LocalMachineClient(); plan = minimal()
    from forma_core.processes.execution import MachineBinding
    plan.bindings[0].conditions = plan.bindings[0].readings
    plan.bindings[0].machine = MachineBinding(machine_id='fixture', package_sha256=client.package.sha256,
                                              action_id='position', parameters={'distance': 1.})
    rt = ProcessRuntime(plan, 'gated', adapter=MachineProcessAdapter(client, 'fixture')); rt.start('a')
    s = submission(); s.readings['temperature'].value = 400
    assert rt.submit('a', s)['status'] == 'unknown'
    assert client.dispatches == 0


def test_machine_adapter_cannot_leak_old_action_to_new_run() -> None:
    """An adapter holding action IDs belongs to one run only."""
    client = LocalMachineClient(); adapter = MachineProcessAdapter(client, 'fixture')
    ProcessRuntime(minimal(), 'first', adapter=adapter)
    with pytest.raises(ValueError, match='shared'):
        ProcessRuntime(minimal(), 'second', adapter=adapter)

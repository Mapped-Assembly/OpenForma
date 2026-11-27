"""Process orchestration, independent of CAD, robots, input devices and renderers."""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Callable

from forma_core.domains.base import fingerprint, unique
from forma_core.domains.quantities import convert, UNITS
from forma_core.experiments.models import ExperimentRun
from forma_core.processes.validation import validate_process
from .adapters import ProcessAdapter, ManualAdapter
from .models import ProcessExecutionPlan, ProcessEvent, StepSubmission, ReadingRequirement


class ProcessRuntime:
    """Serialized single-owner process run with append-only evidence and event snapshots.

    Hosts serialize calls and persist snapshots. Hashes detect accidental edits;
    they do not authenticate an operator or replace durable server-side storage.
    """

    def __init__(self, plan: ProcessExecutionPlan, run_id: str, *, adapter: ProcessAdapter | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        """Pin a validated native process and its preregistered experiment, if supplied."""
        self._plan = ProcessExecutionPlan.model_validate(plan.model_dump(mode='json'))
        if not run_id.strip():
            raise ValueError('run_id is required.')
        errors = [f for f in validate_process(self._plan.process) if f.severity == 'error']
        if errors:
            raise ValueError(f'Invalid ProcessIR: {errors}')
        self.run_id = run_id
        self.plan_sha256 = fingerprint(self._plan)
        self.adapter = adapter or ManualAdapter()
        from .adapters import MachineProcessAdapter
        if isinstance(self.adapter, MachineProcessAdapter):
            if self.adapter.owner_run is not None:
                raise ValueError("Machine adapter instances cannot be shared between process runs.")
            self.adapter.owner_run = run_id
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._started = self._now()
        self._steps = {s.step_id: s for s in self._plan.process.steps}
        self._bindings = {s.step_id: s for s in self._plan.bindings}
        self._states = {sid: 'pending' for sid in self._steps}
        self._events: list[ProcessEvent] = []
        self._accepted: dict[str, StepSubmission] = {}
        self._submissions: dict[str, tuple[str, dict[str, Any]]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._cancelled = False
        self._provenances: set[str] = set()

    def _now(self) -> datetime:
        """Use timezone-aware monotonic-in-history event timestamps."""
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError('Runtime timestamps require a timezone.')
        if getattr(self, '_events', None) and now < self._events[-1].timestamp:
            raise ValueError('Runtime clock moved backwards.')
        return now

    def _event(self, kind: str, step_id: str | None, payload: dict[str, Any]) -> None:
        """Append an independent JSON copy of one causally ordered event."""
        body = dict(sequence=len(self._events), run_id=self.run_id, plan_sha256=self.plan_sha256,
                    timestamp=self._now().isoformat(), step_id=step_id, kind=kind, payload=payload,
                    previous_sha256=self._events[-1].sha256 if self._events else None)
        event = ProcessEvent.model_validate({**body, 'sha256': '0'*64})
        event.sha256 = fingerprint(event.model_dump(mode='json', exclude={'sha256'}))
        self._events.append(event)

    def ready_steps(self) -> list[str]:
        """Return dependency-ready steps without silently scheduling any execution."""
        if self._cancelled or self._active is not None:
            return []
        return [sid for sid, step in self._steps.items() if self._states[sid] == 'pending'
                and all(self._states[parent] == 'completed' for parent in step.depends_on)]

    def start(self, step_id: str) -> dict[str, Any]:
        """Start one ready step; starting alone never invokes a device."""
        if step_id not in self.ready_steps():
            raise ValueError('Step is not dependency-ready or another step is active.')
        self._now()
        self._active = step_id
        self._states[step_id] = 'running'
        self._event('started', step_id, {'operation_id': self._steps[step_id].operation_id})
        return self.snapshot()

    def _requirements(self, requirements: list[ReadingRequirement], submission: StepSubmission) -> list[str]:
        """Check dimensioned uncertainty intervals without discarding affine unit semantics."""
        findings = []
        for requirement in requirements:
            reading = submission.readings.get(requirement.name)
            if reading is None:
                findings.append(f'{requirement.name}: unknown reading')
                continue
            value = convert(Fraction(str(reading.value)), reading.unit, requirement.unit, requirement.dimension)
            uncertainty = None if reading.uncertainty is None else convert(
                Fraction(str(reading.uncertainty)), reading.unit, requirement.unit, requirement.dimension, interval=True)
            if value is None or (reading.uncertainty is not None and uncertainty is None):
                findings.append(f'{requirement.name}: unsupported units')
            elif requirement.require_uncertainty and uncertainty is None:
                findings.append(f'{requirement.name}: unknown uncertainty')
            elif value-(uncertainty or 0) < Fraction(str(requirement.minimum)) or value+(uncertainty or 0) > Fraction(str(requirement.maximum)):
                findings.append(f'{requirement.name}: interval outside completion bounds')
        return findings

    def _validate_submission(self, step_id: str, submission: StepSubmission) -> None:
        """Reject dangling provenance, lot lineage and observations before dispatch."""
        unique([e.source_id for e in submission.evidence], 'evidence ID')
        unique([b.batch_id for b in submission.batches], 'batch ID')
        unique([o.criterion_id for o in submission.observations], 'criterion observation')
        sources = {e.source_id for e in submission.evidence}
        if set(submission.readings) != set(submission.reading_evidence):
            raise ValueError('Every reading needs explicit source references.')
        references = [*submission.reading_evidence.values(),
                      *[b.evidence_ids for b in submission.batches], *[o.evidence_ids for o in submission.observations]]
        if not submission.evidence or any(not ids or set(ids)-sources for ids in references):
            raise ValueError('Submission requires resolvable evidence, including acknowledgements.')
        if any(e.source_id in {old.source_id for accepted in self._accepted.values() for old in accepted.evidence}
               and any(old.source_id == e.source_id and old != e for accepted in self._accepted.values() for old in accepted.evidence)
               for e in submission.evidence):
            raise ValueError('Evidence identity cannot be overwritten.')
        streams = {s.stream_id: s for s in self._plan.process.streams if s.kind == 'material'}
        operation = self._steps[step_id].operation_id
        for batch in submission.batches:
            stream = streams.get(batch.stream_id)
            if stream is None or operation not in {stream.source.operation_id, stream.target.operation_id}:
                raise ValueError('Batch must reference an incident material stream.')
            if batch.batch_id in self._batches or set(batch.parent_batch_ids)-self._batches.keys():
                raise ValueError('Duplicate batch or unresolved prior lot ancestry.')
            if UNITS.get(batch.quantity.unit, ('',))[0] not in {'mass', 'amount', 'volume'}:
                raise ValueError('Batch requires a supported material quantity unit.')
            if stream.batch_quantity is not None:
                expected = {'mass': 'mass', 'molar_amount': 'amount', 'amount': 'amount', 'volume': 'volume'}.get(stream.batch_quantity.kind)
                if expected and UNITS[batch.quantity.unit][0] != expected:
                    raise ValueError('Observed batch dimension differs from the material stream.')
        criteria = {c.criterion_id: c for c in self._plan.experiment.request.criteria} if self._plan.experiment else {}
        existing = {o.criterion_id for s in self._accepted.values() for o in s.observations}
        for observation in submission.observations:
            criterion = criteria.get(observation.criterion_id)
            if criterion is None or observation.criterion_id in existing or observation.target != criterion.target or observation.species_id != criterion.species_id or observation.basis != criterion.basis:
                raise ValueError('Observation must match a unique frozen scientific criterion.')

    def submit(self, step_id: str, submission: StepSubmission) -> dict[str, Any]:
        """Acknowledge/perform one step and advance only after all completion gates pass."""
        submission = StepSubmission.model_validate(submission.model_dump(mode='json'))
        digest = fingerprint({'step_id': step_id, 'submission': submission.model_dump(mode='json')})
        prior = self._submissions.get(submission.submission_id)
        if prior:
            if prior[0] != digest:
                raise ValueError('Submission ID already belongs to different content.')
            import copy
            return copy.deepcopy(prior[1])
        if self._active != step_id or self._cancelled:
            raise ValueError('Submission requires the active step.')
        self._now()
        self._validate_submission(step_id, submission)
        binding = self._bindings[step_id]
        findings = self._requirements(binding.conditions, submission)
        findings += [f'Unconfirmed precondition: {p}' for p in self._steps[step_id].preconditions if p not in submission.confirmed_preconditions]
        if not submission.acknowledged:
            findings.append('Explicit acknowledgement required before adapter dispatch.')
        expected_basis = self.adapter.evidence_basis(self._plan.model_copy(deep=True), binding.model_copy(deep=True))
        if any(e.basis != expected_basis for e in submission.evidence):
            raise ValueError(f'Adapter requires {expected_basis} evidence before dispatch.')
        result = None
        if not findings:
            from .models import AdapterResult
            try:
                result = self.adapter.execute(self._plan.model_copy(deep=True), binding.model_copy(deep=True), submission.model_copy(deep=True))
            except Exception as exc:
                result = AdapterResult(status='unknown', provenance='simulated' if expected_basis == 'illustrative' else 'machine-measured',
                    message=f'Adapter outcome unknown: {type(exc).__name__}. Reconcile before retrying.')
            expected = 'illustrative' if result.provenance == 'simulated' else 'measured'
            if any(e.basis != expected for e in submission.evidence):
                raise ValueError(f'{result.provenance} submissions require {expected} evidence.')
            if result.status != 'ready':
                findings.append(result.message)
        findings += self._requirements(binding.readings, submission)
        if set(binding.required_batch_streams)-{b.stream_id for b in submission.batches}:
            findings.append('Required material batch observations are missing.')
        if set(binding.required_criteria)-{o.criterion_id for o in submission.observations}:
            findings.append('Required scientific observations are missing.')
        status = 'completed' if not findings else (result.status if result and result.status != 'ready' else 'unknown')
        response = {'step_id': step_id, 'status': status, 'findings': findings,
                    'adapter': result.model_dump(mode='json') if result else None}
        self._event('submitted', step_id, {'submission': submission.model_dump(mode='json'), 'result': response})
        if status == 'completed':
            assert result is not None
            self._accepted[step_id] = submission
            self._provenances.add(result.provenance)
            for batch in submission.batches:
                self._batches[batch.batch_id] = {**batch.model_dump(mode='json'), 'step_id': step_id,
                    'operation_id': self._steps[step_id].operation_id, 'provenance': result.provenance}
            self._states[step_id] = 'completed'
            self._active = None
            self._event('completed', step_id, {'provenance': result.provenance, 'physical_verification': 'not_evaluated'})
        elif status == 'failed':
            self._states[step_id] = 'failed'
            self._active = None
        import copy
        self._submissions[submission.submission_id] = (digest, copy.deepcopy(response))
        return response

    def cancel(self) -> dict[str, Any]:
        """Stop process progression; for a machine adapter request hold before recording cancellation."""
        if self._cancelled:
            return self.snapshot()
        from .adapters import MachineProcessAdapter
        if isinstance(self.adapter, MachineProcessAdapter) and self._active is not None:
            self.adapter.client.command('hold')
        self._cancelled = True
        if self._active:
            self._states[self._active] = 'cancelled'
        self._event('cancelled', self._active, {})
        self._active = None
        return self.snapshot()

    def snapshot(self, *, include_events: bool = True) -> dict[str, Any]:
        """Expose copied state, optionally omitting history during high-rate polling."""
        import copy
        operations = {}
        for operation in self._plan.process.operations:
            states = [self._states[sid] for sid, step in self._steps.items() if step.operation_id == operation.operation_id]
            operations[operation.operation_id] = 'completed' if states and all(s == 'completed' for s in states) else 'not_completed'
        return copy.deepcopy({'format': 'forma.process-run', 'run_id': self.run_id,
            'plan_sha256': self.plan_sha256, 'process_sha256': fingerprint(self._plan.process),
            'status': 'cancelled' if self._cancelled else 'completed' if all(s == 'completed' for s in self._states.values()) else 'failed' if 'failed' in self._states.values() else 'running',
            'steps': self._states, 'operations': operations, 'active_step': self._active,
            'ready_steps': self.ready_steps(), 'batches': self._batches,
            'events': [e.model_dump(mode='json') for e in self._events] if include_events else [],
            'event_count': len(self._events),
            'last_event_sha256': self._events[-1].sha256 if self._events else None,
            'physical_verification': 'not_evaluated', 'material_balance': 'unknown'})

    def experiment_run(self, operator: str) -> ExperimentRun:
        """Export accepted evidence to the existing empirical comparator; never auto-pass it."""
        if self.snapshot()['status'] != 'completed' or self._plan.experiment is None:
            raise ValueError('A completed run with a frozen experiment is required.')
        if 'simulated' in self._provenances and len(self._provenances) > 1:
            raise ValueError('Mixed physical/simulated evidence requires separate empirical trials.')
        readings = {k: v for s in self._accepted.values() for k, v in s.readings.items()}
        sources = {e.source_id: e for s in self._accepted.values() for e in s.evidence}
        request = self._plan.experiment.request
        return ExperimentRun(run_id=self.run_id, sample_id=self.run_id,
            plan_sha256=self._plan.experiment.scientific_sha256,
            kind='synthetic' if 'simulated' in self._provenances else 'physical',
            started_at=self._started, completed_at=self._events[-1].timestamp, operator=operator,
            inputs={i.input_id: readings[i.input_id] for i in self._plan.experiment.input_requirements if i.input_id in readings},
            conditions={c.name: readings[c.name] for c in request.conditions if c.name in readings},
            observations=[o for s in self._accepted.values() for o in s.observations], evidence=list(sources.values()))

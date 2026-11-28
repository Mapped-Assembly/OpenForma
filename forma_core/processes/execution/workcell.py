"""Declarative, ideal workcell execution over the existing process and machine runtimes.

This is an offline virtual host, not a hardware dispatcher or fluid solver. Material
updates are conservative discrete transactions after observed motion completion.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, model_validator

from forma_core.control import MachineRuntime, VirtualAdapter
from forma_core.control.package import MachinePackage
from forma_core.domains.base import DomainModel, Text, fingerprint, unique
from .adapters import MachineProcessAdapter, VirtualProcessAdapter
from .models import AdapterResult, MachineBinding, ProcessExecutionPlan, StepBinding, StepSubmission
from .runtime import ProcessRuntime
from .interactions import InteractionCommand, InteractionSpec, InteractionRuntime


class Container(DomainModel):
    """A virtual inventory location; mass capacity is a declared scenario constraint."""

    container_id: Text
    body_id: Text
    label: Text
    capacity_g: float = Field(gt=0)
    contents_g: dict[Text, float] = Field(default_factory=dict)
    center_mm: list[float] = Field(min_length=3, max_length=3)
    radius_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    color: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode='after')
    def valid(self) -> Container:
        """Reject impossible initial inventories and out-of-range display colors."""
        if any(v < 0 for v in self.contents_g.values()) or sum(self.contents_g.values()) > self.capacity_g:
            raise ValueError('Invalid initial container inventory.')
        if any(not 0 <= c <= 1 for c in self.color):
            raise ValueError('Colors must lie in [0, 1].')
        return self


class Transfer(DomainModel):
    """Conservative ideal batch transfer, explicitly linked to a native material stream."""

    source: Text
    target: Text
    stream_id: Text
    mass_g: float = Field(gt=0)
    entity: Text | None = None


class Phase(DomainModel):
    """One feedback-gated machine action and optional discrete inventory effect."""

    phase_id: Text
    step_id: Text
    label: Text
    action_id: Text
    parameters: dict[str, float] = Field(default_factory=dict)
    transfer: Transfer | None = None
    agitate: Text | None = None
    before: list[InteractionCommand] = Field(default_factory=list)
    after: list[InteractionCommand] = Field(default_factory=list)


class VirtualWorkcell(DomainModel):
    """Execution sidecar composing existing contracts; not an engineering IR."""

    machine: MachinePackage
    verification_mode: Literal['strict', 'legacy-unverified'] = 'strict'
    interactions: InteractionSpec | None = None
    geometry_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    containers: list[Container] = Field(min_length=1, max_length=100)
    phases: list[Phase] = Field(min_length=1, max_length=1000)
    frame_interval_s: float = Field(default=.1, ge=.02, le=.5)
    maximum_duration_s: float = Field(default=180, gt=0, le=600)

    @model_validator(mode='after')
    def references(self) -> VirtualWorkcell:
        """Only allow the installed ideal adapter, known actions and resolvable effects."""
        if self.machine.definition.backend != 'virtual' or self.machine.adapter != VirtualAdapter(self.machine.definition).describe():
            raise ValueError('Offline workcells require the installed forma.ideal adapter.')
        unique([c.container_id for c in self.containers], 'container')
        unique([c.body_id for c in self.containers], 'container body')
        unique([p.phase_id for p in self.phases], 'phase')
        containers = {c.container_id for c in self.containers}
        actions = {a.action_id: a for a in self.machine.actions}
        for phase in self.phases:
            action = actions.get(phase.action_id)
            if action is None or set(phase.parameters) != set(action.parameters):
                raise ValueError('Phase requires a known action and its exact parameters.')
            if any(not action.parameters[k].minimum <= v <= action.parameters[k].maximum for k, v in phase.parameters.items()):
                raise ValueError('Phase parameter exceeds action bounds.')
            if phase.transfer and (phase.transfer.source == phase.transfer.target or
                    {phase.transfer.source, phase.transfer.target} - containers):
                raise ValueError('Transfer needs two distinct known containers.')
            if phase.agitate and phase.agitate not in containers:
                raise ValueError('Agitation references an unknown container.')
        if self.verification_mode == 'strict' and self.interactions is None:
            raise ValueError('Strict workcells require interaction declarations; legacy playback must explicitly select legacy-unverified.')
        if self.verification_mode == 'legacy-unverified' and self.interactions is not None:
            raise ValueError('Interaction declarations require strict verification mode.')
        if self.interactions:
            entities = {e.body_id: e for e in self.interactions.entities}
            axes = {a.axis_id: a for a in self.machine.definition.axes}
            for axis in axes.values():
                if axis.joint:
                    for body in axis.joint.body_ids:
                        if body in entities and entities[body].kind != 'actor':
                            raise ValueError('Nonactor poses must derive from relationships, not independent axes.')
            bodies = {b for a in axes.values() if a.joint for b in a.joint.body_ids}
            if bodies - entities.keys():
                raise ValueError('Every motion body requires an interaction entity.')
            if {c.body_id for c in self.containers} - entities.keys():
                raise ValueError('Every inventory container requires an interaction entity.')
            if {e.body_id for e in entities.values() if e.kind == 'actor'} - bodies:
                raise ValueError('Every actor requires a feedback body binding.')
            for mechanism in self.interactions.mechanisms:
                if mechanism.input_axis not in axes:
                    raise ValueError('Unknown mechanism input axis.')
            surfaces = {surface.surface_id: surface for surface in self.interactions.surfaces}
            container_bodies = {c.container_id: c.body_id for c in self.containers}
            for phase in self.phases:
                used_axes = set(actions[phase.action_id].axes)
                drives = {m.input_axis: m.body_id for m in self.interactions.mechanisms}
                for axis_id in used_axes:
                    if axes[axis_id].joint is None and axis_id not in drives:
                        raise ValueError('Every commanded axis requires a body or transmission binding.')
                    if axis_id in drives and not any(cmd.action == 'actuate' and cmd.entity == drives[axis_id] for cmd in phase.before):
                        raise ValueError('A transmission action requires phase-scoped actuation before dispatch.')
                if phase.transfer:
                    transfer = phase.transfer
                    if transfer.entity not in entities or entities[transfer.entity].kind != 'movable':
                        raise ValueError('Strict transfers require a movable entity.')
                    target = container_bodies[transfer.target]
                    matches = [cmd for cmd in [*phase.before, *phase.after]
                               if cmd.entity == transfer.entity and (
                                   (cmd.action == 'grasp' and cmd.actor == target) or
                                   (cmd.action == 'release' and cmd.surface in surfaces and surfaces[cmd.surface].body_id == target))]
                    if len(matches) != 1:
                        raise ValueError('Transfer requires exactly one matching grasp or supported release.')
                for command in [*phase.before, *phase.after]:
                    if command.entity not in entities or command.actor not in entities:
                        raise ValueError('Unknown interaction action target.')
                    if command.surface is not None and command.surface not in surfaces:
                        raise ValueError('Unknown interaction support surface.')
                    if command.action not in entities[command.actor].actions:
                        raise ValueError('Actor does not provide the requested action.')
                    if command.action not in entities[command.entity].actions:
                        raise ValueError('Entity does not provide the requested action.')
        elif any(p.before or p.after for p in self.phases):
            raise ValueError('Interaction actions require entity declarations.')
        return self

    def validate_plan(self, plan: ProcessExecutionPlan) -> None:
        """Bind every phase to the exact native step and incident material stream."""
        steps = {s.step_id: s for s in plan.process.steps}
        streams = {s.stream_id: s for s in plan.process.streams if s.kind == 'material'}
        if {p.step_id for p in self.phases} != set(steps):
            raise ValueError('Workcell phases must cover every process step.')
        if any(b.machine is not None for b in plan.bindings):
            raise ValueError('Choose phase bindings or a single machine binding, not both.')
        for phase in self.phases:
            if phase.transfer:
                stream = streams.get(phase.transfer.stream_id)
                if stream is None or steps[phase.step_id].operation_id not in {stream.source.operation_id, stream.target.operation_id}:
                    raise ValueError('Transfer stream must be incident to its process operation.')


class MaterialLedger:
    """Exact rational inventory accounting, separate from measurements and chemistry."""

    def __init__(self, containers: list[Container]) -> None:
        """Copy declared masses; callers cannot mutate an in-flight scenario."""
        self.capacity = {c.container_id: Fraction(str(c.capacity_g)) for c in containers}
        self.contents = {c.container_id: {s: Fraction(str(m)) for s, m in c.contents_g.items()} for c in containers}
        self.initial = self.totals()
        self.agitated: set[str] = set()
        self.applied: set[str] = set()

    def totals(self) -> dict[str, Fraction]:
        """Return component masses across the closed virtual accounting boundary."""
        result: dict[str, Fraction] = {}
        for contents in self.contents.values():
            for species, mass in contents.items():
                result[species] = result.get(species, Fraction(0)) + mass
        return result

    def validate(self, phase: Phase) -> None:
        """Reject overdraw and overflow before dispatching the associated motion."""
        transfer = phase.transfer
        if transfer:
            mass = Fraction(str(transfer.mass_g))
            if sum(self.contents[transfer.source].values()) < mass:
                raise ValueError('Transfer exceeds source inventory.')
            if sum(self.contents[transfer.target].values()) + mass > self.capacity[transfer.target]:
                raise ValueError('Transfer exceeds target capacity.')
        if phase.agitate and not sum(self.contents[phase.agitate].values()):
            raise ValueError('Cannot agitate an empty container.')

    def apply(self, phase: Phase) -> None:
        """Commit each effect once, only after the caller has verified motion feedback."""
        if phase.phase_id in self.applied:
            return
        self.validate(phase)
        if phase.transfer:
            t = phase.transfer
            source, target = self.contents[t.source], self.contents[t.target]
            fraction = Fraction(str(t.mass_g)) / sum(source.values())
            for species, amount in list(source.items()):
                moved = amount * fraction
                source[species] -= moved
                target[species] = target.get(species, Fraction(0)) + moved
            self.agitated.discard(t.target)
        if phase.agitate:
            self.agitated.add(phase.agitate)
        self.applied.add(phase.phase_id)
        assert self.totals() == self.initial

    def snapshot(self) -> dict[str, Any]:
        """Expose nominal virtual quantities, never instrument readings."""
        return {'containers': {key: {'contents_g': {s: float(m) for s, m in values.items()},
                    'mass_g': float(sum(values.values())), 'agitation_performed': key in self.agitated}
                    for key, values in self.contents.items()},
                'residual_g': float(sum(self.totals().values()) - sum(self.initial.values())),
                'basis': 'ideal-discrete-inventory', 'reaction_state': 'unsupported',
                'mixing_homogeneity': 'unknown', 'physical_verification': 'not_run'}


class IdealMachineHost:
    """Offline host for the real MachineRuntime, with an explicit virtual clock."""

    def __init__(self, package: MachinePackage) -> None:
        """Reject hardware packages before claiming and arming an in-memory machine."""
        if package.definition.backend != 'virtual' or package.adapter != VirtualAdapter(package.definition).describe():
            raise ValueError('Only forma.ideal packages are permitted here.')
        self.package = package.model_copy(deep=True)
        self.time = 0.0
        self.seq = 0
        self.runtime = MachineRuntime(self.package.definition, package=self.package, clock=lambda: self.time)
        self.session = self.runtime.claim('offline virtual workcell')['session_id']
        self.command('arm')

    def capabilities(self) -> dict[str, Any]:
        """Advertise the exact package consumed by the process execution adapter."""
        return {'machine_package': self.package}

    def command(self, op: str, **parameters: Any) -> dict[str, Any]:
        """Use existing lease, sequence, watchdog, action and feedback validation."""
        lease = self.runtime.lease(self.session)
        packet = dict(protocol_version=2, session_id=self.session, lease_id=lease['lease_id'],
                      seq=self.seq, op=op, **parameters)
        self.seq += 1
        return self.runtime.command(packet)

    def action_result(self, run_id: str) -> dict[str, Any]:
        """Read authoritative action evidence rather than inferring it from poses."""
        return self.runtime.action_execution.snapshot(run_id)

    def advance(self, duration: float) -> None:
        """Maintain heartbeats while ticking at the machine's declared update interval."""
        target = self.time + duration
        dt = self.package.definition.timing.update_ms / 1000
        while self.time < target - 1e-10:
            self.time = min(target, self.time + dt)
            self.command('heartbeat')
            self.runtime.tick()


class WorkcellAdapter(MachineProcessAdapter):
    """Compose feedback-gated phases with the existing model evaluator adapter."""

    def __init__(self, spec: VirtualWorkcell, host: IdealMachineHost) -> None:
        """Use a separate action key per phase; retain every completed action receipt."""
        super().__init__(host, spec.machine.definition.machine_id)
        self.spec = spec.model_copy(deep=True)
        self.host = host
        self.package_sha256 = self.spec.machine.sha256
        self.ledger = MaterialLedger(spec.containers)
        self.machine = MachineProcessAdapter(host, self.machine_id)
        self.virtual = VirtualProcessAdapter()
        self.completed: list[dict[str, Any]] = []
        self.current: Phase | None = None
        self.index: dict[str, int] = {}
        self.failure: str | None = None
        self.transfer_sources_checked: set[str] = set()
        self.interactions = InteractionRuntime(spec.interactions) if spec.interactions else None

    def evidence_basis(self, plan: ProcessExecutionPlan, binding: StepBinding) -> str:
        """All inputs and results of this offline adapter remain illustrative."""
        return 'illustrative'

    def execute(self, plan: ProcessExecutionPlan, binding: StepBinding, submission: StepSubmission) -> AdapterResult:
        """Advance only from machine receipts; never from renderer progress or elapsed GIF time."""
        if self.failure:
            return AdapterResult(status='failed', provenance='simulated', message=self.failure)
        model = self.virtual.execute(plan, binding, submission)
        if model.status != 'ready':
            return model
        phases = [p for p in self.spec.phases if p.step_id == binding.step_id]
        index = self.index.get(binding.step_id, 0)
        if index >= len(phases):
            return model
        phase = self.current = phases[index]
        try:
            self.ledger.validate(phase)
            if self.interactions:
                from .kinematics import body_transforms
                positions = {key: value['simulated'] for key, value in self.host.runtime.snapshot()['axes'].items()}
                self.interactions.update(body_transforms(self.spec, positions), positions)
                if phase.transfer and phase.phase_id not in self.transfer_sources_checked:
                    source = next(c.body_id for c in self.spec.containers if c.container_id == phase.transfer.source)
                    relation = self.interactions.parent.get(phase.transfer.entity)
                    if relation is None or relation[0] != source:
                        raise ValueError('Transfer source does not own or support the declared entity.')
                    self.transfer_sources_checked.add(phase.phase_id)
                self.interactions.commands(phase.before, phase.phase_id)
        except ValueError as exc:
            self.failure = str(exc)
            return AdapterResult(status='failed', provenance='simulated', message=self.failure)
        machine_binding = StepBinding(step_id=phase.phase_id, machine=MachineBinding(
            machine_id=self.machine_id, package_sha256=self.package_sha256,
            action_id=phase.action_id, parameters=phase.parameters))
        result = self.machine.execute(plan, machine_binding, submission)
        if result.status == 'failed':
            self.failure = result.message
        if result.status != 'ready':
            return result
        try:
            if self.interactions:
                trial = deepcopy(self.interactions)
                trial.commands(phase.after, phase.phase_id, after=True)
                if phase.transfer:
                    target = next(c.body_id for c in self.spec.containers if c.container_id == phase.transfer.target)
                    relation = trial.parent.get(phase.transfer.entity)
                    events = [e for e in trial.events if e['phase_id'] == phase.phase_id and e['entity'] == phase.transfer.entity and e['action'] in {'grasp', 'release'}]
                    if relation is None or relation[0] != target or relation[2] not in {'grasp', 'support'} or not events:
                        raise ValueError('Transfer requires observed grasp or supported release to its target.')
                self.interactions = trial
        except ValueError as exc:
            self.failure = str(exc)
            return AdapterResult(status='failed', provenance='simulated', message=self.failure)
        self.ledger.apply(phase)
        self.completed.append({'phase_id': phase.phase_id, 'step_id': phase.step_id,
                               'receipt': result.machine_receipt, 'materials': self.ledger.snapshot(),
                               'interactions': self.interactions.snapshot() if self.interactions else None})
        self.index[binding.step_id] = index + 1
        if index + 1 < len(phases):
            return AdapterResult(status='unknown', provenance='simulated', message='Next declared phase pending.',
                                 machine_receipt=result.machine_receipt)
        return AdapterResult(status='ready', provenance='simulated', message='All declared phases have machine feedback.',
                             evaluations=model.evaluations, machine_receipt=result.machine_receipt)


def simulate_workcell(plan: ProcessExecutionPlan, spec: VirtualWorkcell, run_id: str,
                      submissions: dict[str, StepSubmission]) -> dict[str, Any]:
    """Run data-only choreography through both runtimes and retain a bounded pose trace."""
    spec = VirtualWorkcell.model_validate(spec.model_dump(mode='json'))
    spec.validate_plan(plan)
    if set(submissions) != {s.step_id for s in plan.process.steps}:
        raise ValueError('Each process step needs exactly one explicit scenario submission.')
    host = IdealMachineHost(spec.machine)
    adapter = WorkcellAdapter(spec, host)
    runtime = ProcessRuntime(plan, run_id, adapter=adapter)
    frames: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    count = 0
    reason = None
    try:
        while host.time <= spec.maximum_duration_s:
            state = runtime.snapshot(include_events=False)
            if state['status'] in {'completed', 'failed', 'cancelled'}:
                break
            step = state['active_step']
            if step is None:
                ready = runtime.ready_steps()
                if not ready:
                    reason = 'No dependency-ready step.'
                    break
                step = ready[0]
                runtime.start(step)
            submission = submissions[step].model_copy(deep=True)
            submission.submission_id = f'{run_id}:{count}'
            count += 1
            response = runtime.submit(step, submission)
            results.append(response)
            state = runtime.snapshot(include_events=False)
            machine = host.runtime.snapshot()
            frame = {'time_s': round(host.time, 6), 'step_id': step,
                     'phase_id': adapter.current.phase_id if adapter.current else None,
                     'phase_label': adapter.current.label if adapter.current else response['status'],
                     'process_status': state['status'], 'steps': state['steps'],
                     'positions': {key: value['simulated'] for key, value in machine['axes'].items()},
                     'machine_action': machine['action'], 'materials': adapter.ledger.snapshot(),
                     'interactions': adapter.interactions.snapshot() if adapter.interactions else None,
                     'process_event_sha256': state['last_event_sha256']}
            frames.append(frame)
            # Preconditions, unsupported science and absent readings must not be papered over.
            if response['adapter'] is None or response['status'] in {'failed', 'unsupported'}:
                reason = '; '.join(response['findings'])
                break
            if response['status'] == 'unknown' and response['adapter']['status'] == 'ready':
                reason = '; '.join(response['findings'])
                break
            if response['status'] == 'unknown' and response['adapter']['machine_receipt'] is None:
                reason = '; '.join(response['findings'])
                break
            host.advance(spec.frame_interval_s)
        else:
            reason = 'Virtual host duration limit reached.'
        if reason and runtime.snapshot()['status'] == 'running':
            runtime.cancel()
        if frames:
            frames[-1]['process_status'] = runtime.snapshot()['status']
            frames[-1]['steps'] = runtime.snapshot()['steps']
        result: dict[str, Any] = {'runtime': runtime.snapshot(), 'frames': frames,
            'phase_receipts': adapter.completed, 'materials': adapter.ledger.snapshot(),
            'submissions': results, 'stop_reason': reason, 'workcell_sha256': fingerprint(spec),
            'geometry_sha256': spec.geometry_sha256, 'machine_package_sha256': spec.machine.sha256,
            'fidelity': 'ideal-kinematics + conservative discrete transfers; no fluid, contact or reaction dynamics',
            'physical_verification': 'not_run',
            'interaction_verification': 'kinematic-anchors-only' if spec.interactions else 'unverified',
            'verification_mode': spec.verification_mode}
        if result['runtime']['status'] == 'completed' and plan.experiment:
            from forma_core.experiments.evaluation import compare_experiment
            trial = runtime.experiment_run('declarative virtual workcell')
            result['experiment_run'] = trial.model_dump(mode='json')
            result['comparison'] = compare_experiment(plan.experiment, [trial])
        result['trace_sha256'] = fingerprint(result)
        return result
    finally:
        host.command('hold')
        host.command('disarm')

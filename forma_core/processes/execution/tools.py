"""Portable transcript replay and declarative ideal workcells; no physical dispatch."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import Field, model_validator
from forma_core.domains.base import DomainModel, Text
from forma_core.domains.providers import DomainRegistry
from .models import ProcessExecutionPlan, StepSubmission
from .runtime import ProcessRuntime
from .adapters import ManualAdapter, VirtualProcessAdapter
from .workcell import VirtualWorkcell, simulate_workcell


class Start(DomainModel):
    """Explicitly start a dependency-ready step."""
    op: Literal['start']
    step_id: Text


class Submit(DomainModel):
    """Submit attributed evidence for the active step."""
    op: Literal['submit']
    step_id: Text
    submission: StepSubmission


class Cancel(DomainModel):
    """Cancel transcript progression."""
    op: Literal['cancel']


class ProcessToolInput(DomainModel):
    """Data-only transcript replay or ideal workcell execution; never a hardware URL."""
    action: Literal['schema', 'run', 'simulate']
    plan: ProcessExecutionPlan | None = None
    run_id: Text | None = None
    mode: Literal['manual', 'virtual'] = 'manual'
    commands: list[Annotated[Start | Submit | Cancel, Field(discriminator='op')]] = Field(default_factory=list)
    workcell: VirtualWorkcell | None = None
    submissions: dict[str, StepSubmission] = Field(default_factory=dict)

    @model_validator(mode='after')
    def arguments(self) -> ProcessToolInput:
        """Reject ignored or incomplete requests."""
        if self.action == 'schema' and (self.plan or self.run_id or self.commands or self.mode != 'manual' or self.workcell or self.submissions):
            raise ValueError('Schema accepts only action.')
        if self.action in {'run', 'simulate'} and (self.plan is None or self.run_id is None):
            raise ValueError('Run requires an execution plan and run ID.')
        if self.action == 'simulate' and (self.workcell is None or self.mode != 'virtual' or self.commands):
            raise ValueError('Simulate requires a virtual workcell and submissions, not transcript commands.')
        if self.action != 'simulate' and (self.workcell is not None or self.submissions):
            raise ValueError('Workcell declarations are only accepted by simulate.')
        return self


def process_runtime_tool(payload: dict[str, Any], *, registry: DomainRegistry | None = None) -> dict[str, Any]:
    """Expose the same process scheduler to existing CLI/MCP domain dispatch."""
    request = ProcessToolInput.model_validate(payload)
    if request.action == 'schema':
        return {'request': ProcessToolInput.model_json_schema(),
                'authority': 'OpenForma owns process state and evidence; OpenCAD supplies geometry only.',
                'machine_execution': 'Inject MachineProcessAdapter into ProcessRuntime in an authorized host. Never replay physical commands from a transcript.'}
    assert request.plan is not None and request.run_id is not None
    if request.action == 'simulate':
        assert request.workcell is not None
        if registry is not None:
            raise ValueError('Virtual workcell simulation uses the installed evaluator registry.')
        return simulate_workcell(request.plan, request.workcell, request.run_id, request.submissions)
    runtime = ProcessRuntime(request.plan, request.run_id,
        adapter=ManualAdapter() if request.mode == 'manual' else VirtualProcessAdapter(registry))
    results = []
    for command in request.commands:
        if command.op == 'start':
            runtime.start(command.step_id)
        elif command.op == 'submit':
            results.append(runtime.submit(command.step_id, command.submission))
        else:
            runtime.cancel()
    result = {'runtime': runtime.snapshot(), 'submissions': results}
    if runtime.snapshot()['status'] == 'completed' and request.plan.experiment:
        from forma_core.experiments.evaluation import compare_experiment
        trial = runtime.experiment_run('process-runtime transcript')
        result['experiment_run'] = trial.model_dump(mode='json')
        result['comparison'] = compare_experiment(request.plan.experiment, [trial], registry=registry)
    return result

"""Execution sidecars for native ProcessIR; not another engineering IR."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from forma_core.chemistry.models import EvidenceRecord
from forma_core.domains.base import DomainModel, Text, unique
from forma_core.domains.evaluation import EvaluationReceipt
from forma_core.domains.providers import Digest
from forma_core.experiments.models import ExperimentPlan, Observation, Reading
from forma_core.processes.models import ProcessIR


class ReadingRequirement(DomainModel):
    """A bounded observation required for step completion, with explicit units."""

    name: Text
    dimension: Text
    unit: Text
    minimum: float
    maximum: float
    require_uncertainty: bool = True

    @model_validator(mode='after')
    def ordered(self) -> ReadingRequirement:
        """Reject inverted acceptance intervals."""
        if self.minimum > self.maximum:
            raise ValueError('Reading interval is inverted.')
        return self


class MachineBinding(DomainModel):
    """Bind a step to one exact existing machine package and declared action."""

    machine_id: Text
    package_sha256: Digest
    action_id: Text
    parameters: dict[str, float] = Field(default_factory=dict)


class StepBinding(DomainModel):
    """Completion obligations and optional execution capabilities for a ProcessStep."""

    step_id: Text
    conditions: list[ReadingRequirement] = Field(default_factory=list)
    readings: list[ReadingRequirement] = Field(default_factory=list)
    required_batch_streams: list[Text] = Field(default_factory=list)
    required_criteria: list[Text] = Field(default_factory=list)
    evaluations: list[Text] = Field(default_factory=list)
    machine: MachineBinding | None = None

    @model_validator(mode='after')
    def unique_requirements(self) -> StepBinding:
        """Avoid ambiguous repeated completion obligations."""
        for values in ([r.name for r in self.conditions], [r.name for r in self.readings], self.required_batch_streams,
                       self.required_criteria, self.evaluations):
            unique(values, 'step requirement')
        return self


class ProcessExecutionPlan(DomainModel):
    """Freeze a process, execution bindings and optional original scientific plan."""

    format: Literal['forma.process-execution'] = 'forma.process-execution'
    version: Literal[1] = 1
    process: ProcessIR
    bindings: list[StepBinding]
    experiment: ExperimentPlan | None = None

    @model_validator(mode='after')
    def references(self) -> ProcessExecutionPlan:
        """Require explicit step coverage and resolvable native references."""
        unique([b.step_id for b in self.bindings], 'step binding')
        steps = {s.step_id: s for s in self.process.steps}
        if not steps or set(steps) != {b.step_id for b in self.bindings}:
            raise ValueError('Bindings must cover every native ProcessStep exactly once.')
        streams = {s.stream_id: s for s in self.process.streams if s.kind == 'material'}
        evaluations = {e.evaluation_id for e in self.experiment.request.evaluations} if self.experiment else set()
        criteria = {c.criterion_id for c in self.experiment.request.criteria} if self.experiment else set()
        for binding in self.bindings:
            if set(binding.evaluations) - evaluations or set(binding.required_criteria) - criteria:
                raise ValueError('Unresolved scientific evaluation or criterion.')
            operation = steps[binding.step_id].operation_id
            for sid in binding.required_batch_streams:
                if sid not in streams or operation not in {streams[sid].source.operation_id, streams[sid].target.operation_id}:
                    raise ValueError('Batch obligation must reference an incident material stream.')
        if self.experiment:
            from forma_core.domains.base import fingerprint
            from forma_core.experiments.evaluation import verify_plan
            verify_plan(self.experiment)
            if fingerprint(self.process) != fingerprint(self.experiment.request.process):
                raise ValueError('Process differs from the frozen scientific plan.')
        return self


class MaterialBatch(DomainModel):
    """An append-only lot observation; never an inferred material balance."""

    batch_id: Text
    stream_id: Text
    quantity: Reading
    parent_batch_ids: list[Text] = Field(default_factory=list)
    evidence_ids: list[Text] = Field(min_length=1)

    @model_validator(mode='after')
    def positive(self) -> MaterialBatch:
        """A measured lot amount cannot be negative or its own ancestor."""
        if self.quantity.value < 0 or self.batch_id in self.parent_batch_ids:
            raise ValueError('Invalid batch quantity or ancestry.')
        unique(self.parent_batch_ids, 'parent batch')
        return self


class StepSubmission(DomainModel):
    """Operator/adapter input, separate from frozen predictions and runtime events."""

    submission_id: Text
    actor: Text
    acknowledged: bool = False
    confirmed_preconditions: list[Text] = Field(default_factory=list)
    readings: dict[str, Reading] = Field(default_factory=dict)
    reading_evidence: dict[str, list[Text]] = Field(default_factory=dict)
    batches: list[MaterialBatch] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)


class AdapterResult(DomainModel):
    """Execution evidence. Completion here is not scientific verification."""

    status: Literal['ready', 'unknown', 'unsupported', 'failed']
    provenance: Literal['manual', 'simulated', 'machine-measured']
    message: Text
    evaluations: list[EvaluationReceipt] = Field(default_factory=list)
    machine_receipt: dict[str, Any] | None = None


class ProcessEvent(DomainModel):
    """Hash-linked event for a precisely pinned execution plan."""

    sequence: int
    run_id: Text
    plan_sha256: Digest
    timestamp: datetime
    step_id: Text | None
    kind: Literal['started', 'submitted', 'completed', 'cancelled']
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_sha256: Digest | None
    sha256: Digest

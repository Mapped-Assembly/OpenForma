"""Named, versioned numerical evaluation contracts shared by domain providers.

These are request/receipt envelopes, not another engineering IR. Scientific
models remain owned by their domains; the registry never evaluates expressions.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import DomainModel, Text, unique
from .providers import ArtifactReference, Digest, ArtifactInput as PinnedArtifact

Status = Literal['pass', 'fail', 'unknown', 'unsupported']


class EvaluationSpec(DomainModel):
    """Discoverable provider capability with explicit parameters and output semantics."""

    evaluator_id: Text
    version: Text
    description: Text
    parameter_schema: dict[str, Any]
    outputs: dict[str, Any]
    limitations: list[str]


class EvaluationRequest(DomainModel):
    """Select a registered model and one pinned input artifact declaratively."""

    evaluation_id: Text
    artifact_id: Text
    evaluator_id: Text
    evaluator_version: Text
    parameters: dict[str, Any] = Field(default_factory=dict)


class EvaluationOutput(DomainModel):
    """A named scalar with dimension, scientific basis and scoped evaluation status."""

    output_id: Text
    value: str | None = None
    dimension: Text
    unit: Text
    basis: Text
    kind: Literal['prediction', 'upper_bound', 'conservation_residual'] = 'prediction'
    status: Status
    message: str = ''

    @model_validator(mode='after')
    def numerical_status(self) -> EvaluationOutput:
        """Only successful numerical outputs can supply a comparison reference."""
        if self.value is not None:
            Fraction(self.value)
        if self.status == 'pass' and self.value is None:
            raise ValueError('Passing output requires a numerical value.')
        return self


class EvaluationReceipt(DomainModel):
    """Complete reproducible invocation and provider-owned raw scientific receipt."""

    request: EvaluationRequest
    artifact: ArtifactReference
    input_digest: Digest
    status: Status
    outputs: list[EvaluationOutput] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    message: str = ''

    @model_validator(mode='after')
    def distinct_outputs(self) -> EvaluationReceipt:
        """Never resolve an ambiguous output name by list order."""
        unique([o.output_id for o in self.outputs], 'evaluation output ID')
        return self


"""Portable experimental records, not a new engineering IR or execution runtime."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from forma_core.chemistry.models import ChemicalIR, EvidenceRecord
from forma_core.chemistry.stoichiometry import StoichiometryRequest, StoichiometryResult
from forma_core.domains.base import DomainModel, Text, unique
from forma_core.domains.providers import ArtifactReference, Digest
from forma_core.processes.models import ProcessIR


class Condition(DomainModel):
    """An explicit applicability interval; absence of a measured value is unknown."""

    name: Text
    unit: Text
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> Condition:
        """Reject empty operating envelopes."""
        if self.minimum > self.maximum:
            raise ValueError("Condition minimum exceeds maximum.")
        return self


class Reading(DomainModel):
    """One instrument reading; uncertainty is an absolute bound, not a confidence interval."""

    value: float
    unit: Text
    instrument: Text
    resolution: float = Field(gt=0)
    uncertainty: float | None = Field(None, ge=0)
    calibration: Text | None = None


class Criterion(DomainModel):
    """A preregistered scalar comparison with an existing stoichiometric output."""

    criterion_id: Text
    species_id: Text
    basis: Literal["net_formation", "total_inventory", "isolated_product"] = "net_formation"
    unit: Text
    absolute_tolerance: float = Field(ge=0)
    method: Literal["direct", "difference_proxy"] = "direct"
    assumptions: list[Text] = Field(default_factory=list)
    recovery_fraction: float | None = Field(None, gt=0, le=1)

    @model_validator(mode="after")
    def explicit_proxy(self) -> Criterion:
        """Do not silently identify a change in total mass with a species measurement."""
        if self.method == "difference_proxy" and not self.assumptions:
            raise ValueError("A difference proxy requires explicit measurement assumptions.")
        return self


class ExperimentRequest(DomainModel):
    """Author a protocol and criteria before recording observations."""

    experiment_id: Text
    revision: int = Field(1, ge=1)
    parent_plan_sha256: Digest | None = None
    objective: Text
    chemical: ChemicalIR
    process: ProcessIR
    calculation: StoichiometryRequest
    inventory_tolerances: dict[str, float] = Field(default_factory=dict)
    criteria: list[Criterion] = Field(min_length=1)
    conditions: list[Condition] = Field(min_length=1)
    assumptions: list[Text] = Field(min_length=1)
    limitations: list[Text] = Field(min_length=1)

    @model_validator(mode="after")
    def identities(self) -> ExperimentRequest:
        """Reject ambiguous criteria, missing protocols and dangling species."""
        unique([c.criterion_id for c in self.criteria], "criterion ID")
        unique([c.name for c in self.conditions], "condition name")
        if not self.process.steps:
            raise ValueError("A protocol requires ProcessIR steps.")
        feeds = {f.species_id for f in self.calculation.inventory}
        if set(self.inventory_tolerances) - feeds or any(v < 0 for v in self.inventory_tolerances.values()):
            raise ValueError("Inventory tolerances must be nonnegative and reference supplied feeds.")
        species = {s.species_id for s in self.chemical.species}
        if any(c.species_id not in species for c in self.criteria):
            raise ValueError("Criterion species must exist in ChemicalIR.")
        if self.revision > 1 and self.parent_plan_sha256 is None:
            raise ValueError("Refinement must reference the historical plan.")
        return self


class ExperimentPlan(DomainModel):
    """Frozen, self-contained prediction/protocol receipt; integrity is checked on use."""

    format: Literal["forma.experiment-plan"] = "forma.experiment-plan"
    schema_version: Literal["1.0"] = "1.0"
    request: ExperimentRequest
    prediction: StoichiometryResult
    process_reference: ArtifactReference
    protocol_step_ids: list[Text]
    scientific_sha256: Digest


class Attachment(DomainModel):
    """Content-addressed evidence reference; transport verifies bytes separately."""

    sha256: Digest
    media_type: Literal["image/jpeg", "image/png", "image/webp", "text/plain", "application/json"]
    source_id: Text


class Observation(DomainModel):
    """An imported observation or explicitly synthetic test input."""

    observation_id: Text
    criterion_id: Text
    species_id: Text
    basis: Literal["net_formation", "total_inventory", "isolated_product"]
    readings: list[Reading] = Field(min_length=1, max_length=2)
    evidence_ids: list[Text] = Field(min_length=1)
    assumptions_confirmed: bool = False


class ExperimentRun(DomainModel):
    """One independent trial, bound to the original plan, not a mutable current model."""

    run_id: Text
    sample_id: Text
    plan_sha256: Digest
    kind: Literal["physical", "synthetic"]
    started_at: datetime
    completed_at: datetime
    operator: Text
    actual_inventory: dict[str, Reading] = Field(default_factory=dict)
    conditions: dict[str, Reading] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    cost: float | None = Field(None, ge=0)
    currency: Text | None = None

    @model_validator(mode="after")
    def provenance(self) -> ExperimentRun:
        """Require timestamps, unique observations and resolvable evidence references."""
        if self.started_at.utcoffset() is None or self.completed_at.utcoffset() is None:
            raise ValueError("Run timestamps require a timezone.")
        if self.completed_at < self.started_at:
            raise ValueError("Run completion precedes its start.")
        if (self.cost is None) != (self.currency is None):
            raise ValueError("Cost and currency must be supplied together.")
        unique([o.observation_id for o in self.observations], "observation ID")
        unique([o.criterion_id for o in self.observations], "observed criterion per run")
        unique([e.source_id for e in self.evidence], "evidence ID")
        sources = {e.source_id: e for e in self.evidence}
        for observation in self.observations:
            if set(observation.evidence_ids) - sources.keys():
                raise ValueError("Observation evidence is unresolved.")
            expected = "measured" if self.kind == "physical" else "illustrative"
            if any(sources[key].basis != expected for key in observation.evidence_ids):
                raise ValueError("Evidence basis must match physical/synthetic run kind.")
        if any(a.source_id not in sources for a in self.attachments):
            raise ValueError("Attachment source is unresolved.")
        return self

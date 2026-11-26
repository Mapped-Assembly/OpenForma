"""Declarative human representation, not a physiological or behavior simulator."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from forma_core.domains.base import DomainDocument, DomainModel, Text, unique


class HumanContext(DomainModel):
    """Authoring scope and evidence inputs for the native human domain."""

    scope: Text
    participant_requirements: list[Text] = Field(min_length=1)
    evidence_requirements: list[Text] = Field(min_length=1)


class ProfileSource(DomainModel):
    """Distinguish synthetic or assumed inputs from referenced data."""

    kind: Literal["synthetic", "assumed", "reference"]
    description: Text
    reference: Text | None = None

    @model_validator(mode="after")
    def check_reference(self) -> ProfileSource:
        """Require traceability when a profile claims a reference."""
        if self.kind == "reference" and self.reference is None:
            raise ValueError("Referenced profiles require a source reference.")
        return self


class BodySegment(DomainModel):
    """Named body region; absent dimensions or mass are unknown."""

    segment_id: Text
    label: Text
    length_m: float | None = Field(default=None, gt=0)
    mass_kg: float | None = Field(default=None, gt=0)


class Joint(DomainModel):
    """An articulated relation between two profile-local body segments."""

    joint_id: Text
    parent_segment_id: Text
    child_segment_id: Text
    lower_limit_deg: float | None = None
    upper_limit_deg: float | None = None

    @model_validator(mode="after")
    def check_limits(self) -> Joint:
        """Reject self-joints and reversed declared limits."""
        if self.parent_segment_id == self.child_segment_id:
            raise ValueError("A joint must connect different segments.")
        if (self.lower_limit_deg is not None and self.upper_limit_deg is not None
                and self.lower_limit_deg > self.upper_limit_deg):
            raise ValueError("Joint lower limit exceeds upper limit.")
        return self


class Capability(DomainModel):
    """Declared capability, not evidence of successful execution."""

    capability_id: Text
    action: Text
    required_tools: list[Text] = Field(default_factory=list)
    estimated_duration_s: float | None = Field(default=None, gt=0)
    reach_m: float | None = Field(default=None, gt=0)
    force_limit_n: float | None = Field(default=None, ge=0)
    conditions: list[Text] = Field(default_factory=list)


class HumanProfile(DomainModel):
    """Reusable versioned anthropometry, articulation and capability assumptions."""

    profile_id: Text
    revision: int = Field(default=1, ge=1)
    source: ProfileSource
    stature_m: float | None = Field(default=None, gt=0)
    mass_kg: float | None = Field(default=None, gt=0)
    segments: list[BodySegment] = Field(default_factory=list)
    joints: list[Joint] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    assumptions: list[Text] = Field(default_factory=list)
    unknowns: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_topology(self) -> HumanProfile:
        """Reject duplicates, dangling joint references, multiple parents and cycles."""
        unique([s.segment_id for s in self.segments], "body segment IDs")
        unique([j.joint_id for j in self.joints], "joint IDs")
        unique([c.capability_id for c in self.capabilities], "capability IDs")
        segments = {s.segment_id for s in self.segments}
        parents: dict[str, str] = {}
        for joint in self.joints:
            parent, child = joint.parent_segment_id, joint.child_segment_id
            if parent not in segments or child not in segments:
                raise ValueError("Joint references an unknown body segment.")
            if child in parents:
                raise ValueError("A body segment cannot have multiple parent joints.")
            parents[child] = parent
        for segment in parents:
            seen: set[str] = set()
            current = segment
            while current in parents:
                if current in seen:
                    raise ValueError("Body articulation contains a cycle.")
                seen.add(current)
                current = parents[current]
        return self


class HumanInterface(DomainModel):
    """Typed participant port for later cross-domain coupling."""

    port_id: Text
    kind: Literal["mechanical_contact", "thermal_exchange", "control", "task"]
    segment_id: Text | None = None
    description: Text
    conditions: list[Text] = Field(default_factory=list)


class HumanConstraint(DomainModel):
    """Declared design constraint, with explicit units and no safety certification."""

    constraint_id: Text
    quantity: Literal["skin_temperature", "contact_force", "reach", "exposure_duration"]
    segment_id: Text | None = None
    lower: float | None = None
    upper: float | None = None
    basis: Text | None = None

    @property
    def unit(self) -> str:
        """Return the canonical unit fixed by the quantity."""
        return {"skin_temperature": "degC", "contact_force": "N",
                "reach": "m", "exposure_duration": "s"}[self.quantity]

    @model_validator(mode="after")
    def check_bounds(self) -> HumanConstraint:
        """Check the declared range; do not infer missing bounds or their safety."""
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Constraint lower bound exceeds upper bound.")
        if self.quantity != "skin_temperature" and any(
            bound is not None and bound < 0 for bound in (self.lower, self.upper)
        ):
            raise ValueError("Force, reach and duration bounds cannot be negative.")
        return self


class HumanParticipant(DomainModel):
    """An individual system participant, distinct from reusable profile and task."""

    participant_id: Text
    profile_id: Text
    profile_revision: int = Field(ge=1)
    roles: list[Literal["wearer", "operator", "assembly_worker", "collaborator",
                        "consumer", "supervisor", "physical_participant"]] = Field(min_length=1)
    interfaces: list[HumanInterface] = Field(default_factory=list)
    constraints: list[HumanConstraint] = Field(default_factory=list)
    unknowns: list[Text] = Field(default_factory=list)


class HumanIR(DomainDocument):
    """Native versioned human domain; geometry and execution are external concerns."""

    domain: Literal["human"] = "human"
    schema_version: Literal["1.0"] = "1.0"
    profiles: list[HumanProfile] = Field(min_length=1)
    participants: list[HumanParticipant] = Field(min_length=1)

    @model_validator(mode="after")
    def check_references(self) -> HumanIR:
        """Enforce revision-pinned profiles and profile-local segment references."""
        unique([p.profile_id for p in self.profiles] +
               [p.participant_id for p in self.participants], "human entity IDs")
        profiles = {p.profile_id: p for p in self.profiles}
        for participant in self.participants:
            profile = profiles.get(participant.profile_id)
            if profile is None or profile.revision != participant.profile_revision:
                raise ValueError("Participant must reference an existing profile revision.")
            segments = {s.segment_id for s in profile.segments}
            unique(participant.roles, "participant roles")
            unique([p.port_id for p in participant.interfaces], "participant interface IDs")
            unique([c.constraint_id for c in participant.constraints], "participant constraint IDs")
            for item in [*participant.interfaces, *participant.constraints]:
                if item.segment_id is not None and item.segment_id not in segments:
                    raise ValueError("Participant item references an unknown body segment.")
        return self

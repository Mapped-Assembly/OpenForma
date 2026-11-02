"""Mechanical authoring independent of electrical or biological context."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from forma_core.domains.base import ComponentView, DomainDocument, DomainModel, Text, unique
from .primitives import MechanicalNotes


class MechanicalContext(DomainModel):
    """Explicit inputs required by a mechanical author; unknowns block this domain."""

    scope: Text
    geometric_requirements: list[Text] = Field(min_length=1)
    loads: list[Text] = Field(min_length=1)
    manufacturing_constraints: list[Text] = Field(min_length=1)


class MechanicalPart(DomainModel):
    """Mechanical facts for a shared part identity; no electrical pin authority."""

    part_definition_id: Text
    dimensions_mm: dict[str, float] = Field(default_factory=dict)
    material: Text | None = None
    geometry_refs: list[Text] = Field(default_factory=list)


class MechanicalIR(DomainDocument):
    """Versioned mechanical parts, placements, CAD and motion intent.

    This preserves existing MechanicalNotes semantics. It does not introduce a
    stress solver, tolerance solver or new kinematic simulation.
    """

    domain: Literal["mechanical"] = "mechanical"
    schema_version: Literal["1.0"] = "1.0"
    parts: list[MechanicalPart] = Field(default_factory=list)
    components: list[ComponentView] = Field(default_factory=list)
    mechanical: MechanicalNotes | None = None
    cad_model: Any | None = None

    @model_validator(mode="after")
    def validate_references(self) -> MechanicalIR:
        """Validate local identities, placements, spatial links and motion targets."""
        unique([p.part_definition_id for p in self.parts], "mechanical part ID")
        unique([c.object_id for c in self.components], "mechanical object ID")
        unique([c.ref_des for c in self.components], "mechanical ref_des")
        parts = {p.part_definition_id for p in self.parts}
        refs = {c.ref_des for c in self.components}
        for component in self.components:
            if component.part_definition_id not in parts:
                raise ValueError(f"Unknown mechanical part '{component.part_definition_id}'.")
        if self.mechanical is not None:
            placements = self.mechanical.component_placements
            unique([p.ref_des for p in placements], "placement ref_des")
            targets = [p.ref_des for p in placements]
            for relation in self.mechanical.spatial_relationships:
                targets.extend([relation.source_ref_des, relation.target_ref_des])
            for motion in self.mechanical.motion_intents:
                targets.append(motion.target_ref)
                if motion.parent_ref is not None:
                    targets.append(motion.parent_ref)
                for lo, hi in [(motion.min_deg, motion.max_deg), (motion.min_mm, motion.max_mm)]:
                    if lo is not None and hi is not None and lo > hi:
                        raise ValueError("Motion minimum exceeds maximum.")
            for target in targets:
                if target not in refs:
                    raise ValueError(f"Unknown mechanical component '{target}'.")
        return self

"""Reference-only inventory projections: specifications remain in ElectricalIR."""
from __future__ import annotations

from pydantic import Field

from forma_core.domains.base import DomainModel, Text
from .models import ElectricalIR


class EngineeringBOMRow(DomainModel):
    """Procurement grouping by shared definition, with no copied engineering facts."""

    part_definition_id: Text
    object_ids: list[Text] = Field(min_length=1)

    @property
    def quantity(self) -> int:
        """Derive quantity from independently addressable physical instances."""
        return len(self.object_ids)


def engineering_bom(ir: ElectricalIR) -> list[EngineeringBOMRow]:
    """Project deterministic instance groups; resolve MPN/ratings from ir.parts."""
    grouped: dict[str, list[str]] = {}
    for component in ir.components:
        grouped.setdefault(component.part_definition_id, []).append(component.object_id)
    return [EngineeringBOMRow(part_definition_id=key, object_ids=sorted(values))
            for key, values in sorted(grouped.items())]

"""Electrical domain using the existing pin, net, bus and power contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from forma_core.domains.base import ComponentView, DomainDocument, DomainModel, Text, unique
from .primitives import BusConnection, ConnectionNet, PinDefinition, PinMappingEntry, PowerRail


class ElectricalContext(DomainModel):
    """Electrical inputs, deliberately excluding geometry and biological context."""

    scope: Text
    supply_requirements: list[Text] = Field(min_length=1)
    load_requirements: list[Text] = Field(min_length=1)
    interface_requirements: list[Text] = Field(min_length=1)


class ElectricalPart(DomainModel):
    """Electrical facts for a shared part identity."""

    part_definition_id: Text
    name: str = ""
    part_number: str = ""
    category: str = "Component"
    pins: list[PinDefinition] = Field(default_factory=list)
    electrical_specs: dict[str, Any] = Field(default_factory=dict)


class ElectricalIR(DomainDocument):
    """Standalone logical electrical design; foundation for issue #17.

    No PCB geometry, harness routing or certification is implied by validity.
    Stable object IDs are separate from legacy display reference designators.
    """

    domain: Literal["electrical"] = "electrical"
    schema_version: Literal["1.0"] = "1.0"
    parts: list[ElectricalPart] = Field(default_factory=list)
    components: list[ComponentView] = Field(default_factory=list)
    nets: list[ConnectionNet] = Field(default_factory=list)
    buses: list[BusConnection] = Field(default_factory=list)
    pin_mappings: list[PinMappingEntry] = Field(default_factory=list)
    power_rails: list[PowerRail] = Field(default_factory=list)
    estimated_current_draw_ma: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_references(self) -> ElectricalIR:
        """Reject ambiguous identities, dangling endpoints, buses and rail sources."""
        for values, label in [
            ([p.part_definition_id for p in self.parts], "electrical part ID"),
            ([c.object_id for c in self.components], "electrical object ID"),
            ([c.ref_des for c in self.components], "electrical ref_des"),
            ([n.net_id for n in self.nets], "net ID"),
            ([b.bus_id for b in self.buses], "bus ID"),
            ([r.rail_id for r in self.power_rails], "rail ID"),
        ]:
            unique(values, label)
        parts = {p.part_definition_id: p for p in self.parts}
        components = {c.ref_des: c for c in self.components}
        for part in self.parts:
            unique([pin.pin_id for pin in part.pins], "pin ID")
        for component in self.components:
            if component.part_definition_id not in parts:
                raise ValueError(f"Unknown electrical part '{component.part_definition_id}'.")
        for net in self.nets:
            unique([f"{pin.ref_des}:{pin.pin_id}" for pin in net.pins], "net endpoint")
            for pin in net.pins:
                component = components.get(pin.ref_des)
                if component is None:
                    raise ValueError(f"Unknown electrical component '{pin.ref_des}'.")
                if pin.pin_id not in {p.pin_id for p in parts[component.part_definition_id].pins}:
                    raise ValueError(f"Unknown pin '{pin.ref_des}.{pin.pin_id}'.")
        nets = {n.net_id for n in self.nets}
        for bus in self.buses:
            if any(net not in nets for net in bus.nets):
                raise ValueError(f"Bus '{bus.bus_id}' references an unknown net.")
        for rail in self.power_rails:
            if rail.source_component not in components:
                raise ValueError(f"Rail '{rail.rail_id}' references an unknown source.")
        return self

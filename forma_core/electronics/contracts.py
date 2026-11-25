"""Optional engineering contracts; missing measurements are None, never zero."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from forma_core.assets.models import InspectRequest
from forma_core.domains.base import DomainModel, Text

Nonnegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Efficiency = Annotated[float, Field(gt=0, le=1)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Discipline = Literal["geometry", "dynamics", "electrical", "thermal", "safety"]


class SourceReference(DomainModel):
    """Immutable supporting document identity; URI is not fetched by validation."""

    uri: Text
    sha256: Digest
    revision: Text | None = None


class VoltageRange(DomainModel):
    """Inclusive DC voltage envelope in volts."""

    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> VoltageRange:
        """Reject inverted envelopes."""
        if self.minimum > self.maximum:
            raise ValueError("Voltage minimum exceeds maximum.")
        return self


class PinLimits(DomainModel):
    """Typed supplement to existing pin direction, interface and power_role."""

    voltage_range: VoltageRange | None = None
    current_limit_a: Nonnegative | None = None
    connector_id: Text | None = None


class Connector(DomainModel):
    """Part-local connector groups existing pin IDs, without copying pin facts."""

    connector_id: Text
    pin_ids: list[Text] = Field(min_length=1)
    mating_part_number: Text | None = None


class MotorConstants(DomainModel):
    """Effective brushed-DC constants in SI; not a BLDC phase-current model."""

    source: SourceReference | None = None
    torque_constant_nm_per_a: Positive | None = None
    back_emf_v_per_rad_s: Positive | None = None
    resistance_ohm: Nonnegative | None = None
    no_load_current_a: Nonnegative | None = None


class ControllerRatings(DomainModel):
    """DC controller limits; motor current and bus current are distinct."""

    source: SourceReference | None = None
    efficiency: Efficiency | None = None
    motor_current_limit_a: Positive | None = None
    bus_current_limit_a: Positive | None = None
    bus_voltage: VoltageRange | None = None


class PartEngineering(DomainModel):
    """Facts owned once by ElectricalPart; instances and exports reference it."""

    manufacturer: Text | None = None
    revision: Text | None = None
    datasheet: SourceReference | None = None
    mass_kg: Nonnegative | None = None
    cad: InspectRequest | None = None
    connectors: list[Connector] = Field(default_factory=list)
    motor: MotorConstants | None = None
    controller: ControllerRatings | None = None


class Endpoint(DomainModel):
    """Stable physical component identity plus existing part pin ID."""

    object_id: Text
    pin_id: Text


class Wire(DomainModel):
    """Physical conductor on an existing logical net; route is a CAD reference."""

    wire_id: Text
    net_id: Text
    start: Endpoint
    end: Endpoint
    length_m: Positive | None = None
    cross_section_mm2: Positive | None = None
    current_limit_a: Positive | None = None
    route_ref: Text | None = None
    protection_object_id: Text | None = None


class Harness(DomainModel):
    """Wire grouping with explicit mating connector instances in the shared BOM."""

    harness_id: Text
    wires: list[Wire] = Field(min_length=1)
    mating_connector_object_ids: list[Text] = Field(default_factory=list)


class PowerLoad(DomainModel):
    """On/off load envelope; all listed loads may operate simultaneously."""

    object_id: Text
    on_power_w: Nonnegative | None = None
    idle_power_w: Nonnegative | None = None
    duty_cycle: Annotated[float, Field(ge=0, le=1)] | None = None


class PowerDomain(DomainModel):
    """Source and loads on a net; converter output optionally names its input domain."""

    domain_id: Text
    net_id: Text
    source_object_id: Text
    voltage_v: Positive | None = None
    current_limit_a: Positive | None = None
    upstream_domain_id: Text | None = None
    conversion_efficiency: Efficiency | None = None
    quiescent_power_w: Nonnegative | None = None
    loads: list[PowerLoad] = Field(default_factory=list)


class RuntimeBinding(DomainModel):
    """Reference existing MachineDefinition commands, units, limits and timing."""

    object_id: Text
    machine_id: Text
    machine_sha256: Digest
    axis_id: Text
    feedback: Literal["simulated", "measured", "unknown"] = "unknown"
    startup_state: Literal["disabled", "unknown"] = "unknown"
    fault_response: Literal["disable", "hold", "unknown"] = "unknown"


class VerificationEvidence(DomainModel):
    """Scoped claims pinned to a design hash and exact component revisions."""

    evidence_id: Text
    discipline: Discipline
    status: Literal["pass", "fail", "unknown"] = "unknown"
    design_sha256: Digest
    component_revisions: dict[Text, Text | None]
    model_sha256: dict[Text, Digest] = Field(default_factory=dict)
    source: SourceReference
    method: Text
    scope: Text
    limitations: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_pass(self) -> VerificationEvidence:
        """Unknown part revisions cannot support a passing claim."""
        if self.status == "pass" and (not self.component_revisions or None in self.component_revisions.values()):
            raise ValueError("Passing evidence requires known component revisions.")
        return self

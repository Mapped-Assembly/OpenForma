"""Independent ThermalIR v1: thermal design topology, constraints and provenance.

No thermal value is inferred from CAD geometry, chemistry or electrical power.
Numerical results belong to a separately versioned, explicitly requested check.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from forma_core.domains.base import DomainDocument, DomainModel, Text, unique

EvidenceBasis = Literal["specified", "measured", "derived", "assumed", "illustrative", "unknown"]
QuantityKind = Literal[
    "temperature", "temperature_difference", "heat_rate", "energy",
    "resistance", "conductance", "capacity", "conductivity", "area",
    "heat_flux", "convection_coefficient", "emissivity",
]
UNITS: dict[str, frozenset[str]] = {
    "temperature": frozenset({"K", "degC"}),
    "temperature_difference": frozenset({"K", "delta_degC"}),
    "heat_rate": frozenset({"W", "kW"}),
    "energy": frozenset({"J", "kJ"}),
    "resistance": frozenset({"K/W"}),
    "conductance": frozenset({"W/K"}),
    "capacity": frozenset({"J/K"}),
    "conductivity": frozenset({"W/(m*K)"}),
    "area": frozenset({"m2"}),
    "heat_flux": frozenset({"W/m2"}),
    "convection_coefficient": frozenset({"W/(m2*K)"}),
    "emissivity": frozenset({"1"}),
}


class ThermalContext(DomainModel):
    """Required thermal-only authoring inputs; no global HardwareIR or scene."""

    scope: Text
    thermal_requirements: list[Text] = Field(min_length=1)
    boundary_requirements: list[Text] = Field(min_length=1)
    evidence_requirements: list[Text] = Field(min_length=1)


class ThermalEvidence(DomainModel):
    """A locatable source or an explicit unverified engineering assumption."""

    source_id: Text
    basis: EvidenceBasis
    title: Text
    publisher: Text | None = None
    locator: Text | None = None
    published_at: Text | None = None
    retrieved_at: Text | None = None
    effective_at: Text | None = None
    undated_reason: Text | None = None
    applicability: Text | None = None
    uncertainty: Text | None = None
    rationale: Text | None = None

    @model_validator(mode="after")
    def require_source_or_rationale(self) -> "ThermalEvidence":
        """Do not pass illustrative data off as sourced measurements."""
        if self.basis in {"measured", "specified"}:
            if not self.publisher or not self.locator or not self.retrieved_at:
                raise ValueError("Specified/measured evidence requires publisher, locator and retrieval time.")
            if not self.published_at and not self.undated_reason:
                raise ValueError("Undated sourced evidence requires undated_reason.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("Assumed/illustrative evidence requires rationale.")
        return self


class ThermalClaim(DomainModel):
    """The authority and traceability of one engineering design statement."""

    basis: EvidenceBasis = "unknown"
    source_ids: list[Text] = Field(default_factory=list)
    rationale: Text | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> "ThermalClaim":
        """Evidence requirements depend on what authority is claimed."""
        if self.basis in {"measured", "specified"} and not self.source_ids:
            raise ValueError("Specified/measured claims require source_ids.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("Assumed/illustrative claims require rationale.")
        return self


class ThermalQuantity(DomainModel):
    """One strictly dimensioned quantity; absolute and differential °C differ."""

    kind: QuantityKind
    value: float
    unit: Text
    claim: ThermalClaim = Field(default_factory=ThermalClaim)

    @model_validator(mode="after")
    def validate_dimension_and_physics(self) -> "ThermalQuantity":
        """Reject dimensional mismatches and physically nonsensical magnitudes."""
        if self.unit not in UNITS[self.kind]:
            raise ValueError(f"Unit '{self.unit}' is unsupported for '{self.kind}'.")
        if self.kind == "temperature":
            kelvin = self.value if self.unit == "K" else self.value + 273.15
            if kelvin < 0:
                raise ValueError("Temperature below absolute zero.")
        if self.kind in {"resistance", "conductance", "capacity", "conductivity",
                         "area", "convection_coefficient"} and self.value <= 0:
            raise ValueError(f"'{self.kind}' must be positive.")
        if self.kind in {"heat_rate", "energy", "heat_flux"} and self.value < 0:
            raise ValueError(f"'{self.kind}' is a nonnegative magnitude; use source/sink direction.")
        if self.kind == "emissivity" and not 0 <= self.value <= 1:
            raise ValueError("Emissivity must be between 0 and 1.")
        return self


class ThermalPort(DomainModel):
    """Stable thermal-energy interface for ProcessIR and other domains."""

    port_id: Text
    kind: Literal["heat"] = "heat"


class ThermalNode(DomainModel):
    """A lumped temperature/energy node or fixed environmental boundary."""

    node_id: Text
    kind: Literal["zone", "body", "boundary"]
    ports: list[ThermalPort] = Field(default_factory=lambda: [ThermalPort(port_id="heat")])
    capacity: ThermalQuantity | None = None
    initial_temperature: ThermalQuantity | None = None
    material_reference: Text | None = None
    description: Text | None = None

    @model_validator(mode="after")
    def check_node(self) -> "ThermalNode":
        """Do not confuse thermal capacity or initial temperature with other units."""
        unique([p.port_id for p in self.ports], "thermal port ID")
        if self.capacity is not None and self.capacity.kind != "capacity":
            raise ValueError("Thermal node capacity must use J/K.")
        if self.initial_temperature is not None and self.initial_temperature.kind != "temperature":
            raise ValueError("Initial temperature must be absolute.")
        return self


class ThermalEdge(DomainModel):
    """A heat-transfer design edge, optionally with measured/effective R or G."""

    edge_id: Text
    source_node_id: Text
    target_node_id: Text
    mechanism: Literal["lumped", "conduction", "convection", "radiation"]
    resistance: ThermalQuantity | None = None
    conductance: ThermalQuantity | None = None
    claim: ThermalClaim = Field(default_factory=ThermalClaim)

    @model_validator(mode="after")
    def check_transfer(self) -> "ThermalEdge":
        """A single edge may not have conflicting resistance descriptions."""
        if self.source_node_id == self.target_node_id:
            raise ValueError("Thermal transfer endpoints must differ.")
        if self.resistance is not None and self.resistance.kind != "resistance":
            raise ValueError("Thermal resistance must use K/W.")
        if self.conductance is not None and self.conductance.kind != "conductance":
            raise ValueError("Thermal conductance must use W/K.")
        if self.resistance is not None and self.conductance is not None:
            raise ValueError("Declare resistance or conductance, not both.")
        return self


class ThermalBoundary(DomainModel):
    """A fixed temperature, prescribed heat magnitude, or insulated condition."""

    boundary_id: Text
    node_id: Text
    kind: Literal["fixed_temperature", "prescribed_heat", "adiabatic"]
    quantity: ThermalQuantity | None = None
    claim: ThermalClaim = Field(default_factory=ThermalClaim)

    @model_validator(mode="after")
    def check_boundary(self) -> "ThermalBoundary":
        """Reject contradictory boundary field/unit combinations."""
        wanted = {"fixed_temperature": "temperature", "prescribed_heat": "heat_rate"}
        if self.kind == "adiabatic" and self.quantity is not None:
            raise ValueError("Adiabatic boundary cannot specify a heat or temperature quantity.")
        if self.kind in wanted and self.quantity is not None and self.quantity.kind != wanted[self.kind]:
            raise ValueError("Thermal boundary quantity kind does not match its boundary.")
        return self


class ThermalSourceReference(DomainModel):
    """Pinned external engineering input, not copied domain semantics."""

    domain: Literal["chemical", "process", "electrical", "fluidic", "mechanical"]
    artifact_id: Text
    entity_id: Text
    schema_version: Text
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    port_id: Text | None = None
    model_version: Text | None = None


class ThermalLoad(DomainModel):
    """A signed-by-role heat source/sink; the magnitude may be unknown."""

    load_id: Text
    node_id: Text
    direction: Literal["source", "sink"]
    heat_rate: ThermalQuantity | None = None
    origin: Literal["illustrative", "electrical", "chemical_reaction", "process", "fluidic"] = "illustrative"
    references: list[ThermalSourceReference] = Field(default_factory=list)
    calculation_model: Text | None = None
    claim: ThermalClaim = Field(default_factory=ThermalClaim)

    @model_validator(mode="after")
    def verify_heat_source(self) -> "ThermalLoad":
        """A chemical heat duty needs a supported chemistry and extent/rate input."""
        if self.heat_rate is not None and self.heat_rate.kind != "heat_rate":
            raise ValueError("Thermal load requires heat-rate magnitude.")
        kinds = {item.domain for item in self.references}
        expected = {"electrical": "electrical", "process": "process", "fluidic": "fluidic"}
        if self.origin in expected and self.heat_rate is not None and expected[self.origin] not in kinds:
            raise ValueError(f"Explicit {self.origin} heat requires a pinned {expected[self.origin]} input.")
        if self.origin == "chemical_reaction" and self.heat_rate is not None:
            if not {"chemical", "process"} <= kinds or not self.calculation_model:
                raise ValueError("Reaction heat requires chemical and process references plus model identity.")
        return self


class ThermalProperty(DomainModel):
    """A bounded, evidence-traceable thermophysical property claim."""

    property_id: Text
    name: Text
    quantity: ThermalQuantity
    min_temperature: ThermalQuantity | None = None
    max_temperature: ThermalQuantity | None = None
    claim: ThermalClaim = Field(default_factory=ThermalClaim)

    @model_validator(mode="after")
    def check_interval(self) -> "ThermalProperty":
        """Validate an optional absolute-temperature applicability interval."""
        for item in (self.min_temperature, self.max_temperature):
            if item is not None and item.kind != "temperature":
                raise ValueError("Thermal-property validity range uses absolute temperatures.")
        if self.min_temperature is not None and self.max_temperature is not None:
            lo = self.min_temperature.value if self.min_temperature.unit == "K" else self.min_temperature.value + 273.15
            hi = self.max_temperature.value if self.max_temperature.unit == "K" else self.max_temperature.value + 273.15
            if lo > hi:
                raise ValueError("Thermal-property applicability interval is inverted.")
        return self


class SteadyStateCase(DomainModel):
    """A request for the bounded, explicitly versioned one-zone evaluator."""

    case_id: Text
    zone_node_id: Text
    ambient_node_id: Text
    edge_id: Text
    load_ids: list[Text] = Field(min_length=1)
    model: Literal["lumped-resistance"] = "lumped-resistance"
    model_version: Literal["1.0"] = "1.0"
    tolerance_w: float = Field(1e-9, gt=0)


class ThermalIR(DomainDocument):
    """Thermal engineering design envelope; valid without any other domain."""

    domain: Literal["thermal"] = "thermal"
    schema_version: Literal["1.0"] = "1.0"
    nodes: list[ThermalNode] = Field(min_length=1)
    edges: list[ThermalEdge] = Field(default_factory=list)
    boundaries: list[ThermalBoundary] = Field(default_factory=list)
    loads: list[ThermalLoad] = Field(default_factory=list)
    properties: list[ThermalProperty] = Field(default_factory=list)
    cases: list[SteadyStateCase] = Field(default_factory=list)
    evidence: list[ThermalEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_local_topology(self) -> "ThermalIR":
        """Keep referential/structural validation separate from solver readiness."""
        collections = [
            ([item.node_id for item in self.nodes], "node"),
            ([item.edge_id for item in self.edges], "edge"),
            ([item.boundary_id for item in self.boundaries], "boundary"),
            ([item.load_id for item in self.loads], "load"),
            ([item.property_id for item in self.properties], "thermal property"),
            ([item.case_id for item in self.cases], "case"),
            ([item.source_id for item in self.evidence], "source"),
        ]
        for values, label in collections:
            unique(values, f"thermal {label} ID")
        unique([value for values, _ in collections[:-1] for value in values], "thermal entity ID")
        nodes = {item.node_id: item for item in self.nodes}
        edges = {item.edge_id: item for item in self.edges}
        loads = {item.load_id: item for item in self.loads}
        sources = {item.source_id for item in self.evidence}
        claims = [item.claim for item in self.edges + self.boundaries + self.loads + self.properties]
        claims += [q.claim for q in (
            [item.heat_rate for item in self.loads if item.heat_rate is not None] +
            [item.quantity for item in self.boundaries if item.quantity is not None] +
            [item.quantity for item in self.properties]
        )]
        for claim in claims:
            if set(claim.source_ids) - sources:
                raise ValueError("Thermal claim references an unknown evidence source.")
        for item in self.edges:
            if item.source_node_id not in nodes or item.target_node_id not in nodes:
                raise ValueError(f"Thermal edge '{item.edge_id}' references an unknown node.")
        for item in self.boundaries:
            if item.node_id not in nodes:
                raise ValueError(f"Thermal boundary '{item.boundary_id}' references an unknown node.")
        for item in self.loads:
            if item.node_id not in nodes:
                raise ValueError(f"Thermal load '{item.load_id}' references an unknown node.")
        for item in self.cases:
            if item.zone_node_id not in nodes or item.ambient_node_id not in nodes:
                raise ValueError("Steady-state case references an unknown node.")
            if item.zone_node_id == item.ambient_node_id:
                raise ValueError("Steady-state zone and ambient must differ.")
            if item.edge_id not in edges:
                raise ValueError("Steady-state case references an unknown transfer edge.")
            if set(item.load_ids) - set(loads):
                raise ValueError("Steady-state case references an unknown thermal load.")
            unique(item.load_ids, "steady-state load ID")
        boundaries: dict[str, set[str]] = {}
        for item in self.boundaries:
            active = boundaries.setdefault(item.node_id, set())
            if item.kind in active:
                raise ValueError("Duplicate thermal boundary condition at node.")
            active.add(item.kind)
            if "adiabatic" in active and len(active) > 1:
                raise ValueError("Adiabatic boundary conflicts with another boundary.")
            if "fixed_temperature" in active and "prescribed_heat" in active:
                raise ValueError("Fixed temperature conflicts with prescribed heat at node.")
        return self

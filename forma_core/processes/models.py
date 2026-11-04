"""Independent ProcessIR v1: unit operations, typed ports, streams, and design scenarios."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from forma_core.domains.base import DomainDocument, DomainModel, Text, unique


StreamKind = Literal["material", "energy", "signal"]
QuantityKind = Literal["mass_rate", "molar_rate", "volume_rate", "power", "signal"]
EvidenceBasis = Literal["specified", "measured", "derived", "assumed", "illustrative", "unknown"]
Phase = Literal["solid", "liquid", "gas", "aqueous", "mixed", "unknown"]


QUANTITY_UNITS: dict[str, frozenset[str]] = {
    "mass_rate": frozenset({"kg/s", "g/s", "kg/h"}),
    "molar_rate": frozenset({"mol/s", "mol/h"}),
    "volume_rate": frozenset({"m3/s", "L/s", "L/min"}),
    "power": frozenset({"W", "kW"}),
    "signal": frozenset({"1"}),
}


class ProcessContext(DomainModel):
    """Explicit process-design authoring context, independent of other IRs."""

    scope: Text
    process_requirements: list[Text] = Field(min_length=1)
    stream_requirements: list[Text] = Field(min_length=1)
    evidence_requirements: list[Text] = Field(min_length=1)


class ProcessEvidence(DomainModel):
    """Dated source or transparent authored assumption for process claims."""

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
    def evidence_requirements(self) -> "ProcessEvidence":
        """Disallow source claims with no citation or assumptions with no rationale."""
        if self.basis in {"measured", "specified"}:
            if not self.publisher or not self.locator or not self.retrieved_at:
                raise ValueError("Measured/specified evidence needs publisher, exact locator and retrieval time.")
            if not self.published_at and not self.undated_reason:
                raise ValueError("Undated sourced claims need an explicit undated_reason.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("Assumed/illustrative evidence requires a rationale.")
        return self


class ProcessClaim(DomainModel):
    """Provenance for an explicitly supplied or absent engineering value."""

    basis: EvidenceBasis = "unknown"
    source_ids: list[Text] = Field(default_factory=list)
    rationale: Text | None = None

    @model_validator(mode="after")
    def source_requirements(self) -> "ProcessClaim":
        """Never promote generated statements to measured or specified claims."""
        if self.basis in {"measured", "specified"} and not self.source_ids:
            raise ValueError("Measured/specified claims require evidence IDs.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("Assumed/illustrative claims require rationale.")
        return self


class ProcessQuantity(DomainModel):
    """A dimensional *design* quantity; it is not an operational observation."""

    kind: QuantityKind
    value: float = Field(ge=0)
    unit: Text
    claim: ProcessClaim = Field(default_factory=ProcessClaim)

    @model_validator(mode="after")
    def check_units(self) -> "ProcessQuantity":
        """Reject dimensional ambiguity instead of converting unrelated measures."""
        if self.unit not in QUANTITY_UNITS[self.kind]:
            raise ValueError(f"Unsupported unit '{self.unit}' for '{self.kind}'.")
        return self


class ProcessPort(DomainModel):
    """A typed, directed interface on one unit operation."""

    port_id: Text
    direction: Literal["in", "out"]
    kind: StreamKind
    quantity_kind: QuantityKind | None = None
    phase: Phase | None = None

    @model_validator(mode="after")
    def valid_quantity_dimension(self) -> "ProcessPort":
        """Do not attach material flow to an energy or signal interface."""
        allowed = {
            "material": {"mass_rate", "molar_rate", "volume_rate"},
            "energy": {"power"},
            "signal": {"signal"},
        }
        if self.quantity_kind is not None and self.quantity_kind not in allowed[self.kind]:
            raise ValueError("Port quantity_kind is incompatible with stream kind.")
        if self.kind != "material" and self.phase is not None:
            raise ValueError("Only material ports may declare a phase.")
        return self


class ProcessBatchQuantity(DomainModel):
    """Finite material inventory, distinct from a rate with an unknown duration."""

    kind: Literal["mass", "amount", "volume"]
    value: float = Field(ge=0)
    unit: Text
    claim: ProcessClaim = Field(default_factory=ProcessClaim)

    @model_validator(mode="after")
    def check_units(self) -> "ProcessBatchQuantity":
        """Keep batch dimensions explicit; no density or duration is inferred."""
        allowed = {"mass": {"g", "kg"}, "amount": {"mol", "mmol"}, "volume": {"mL", "L", "m3"}}
        if self.unit not in allowed[self.kind]:
            raise ValueError(f"Unsupported batch unit '{self.unit}' for '{self.kind}'.")
        return self


class ProcessOperation(DomainModel):
    """One declarative unit operation, without embedded chemistry or runtime code."""

    operation_id: Text
    operation_type: Text
    capability_version: Text = "conceptual"
    ports: list[ProcessPort] = Field(default_factory=list)
    description: Text | None = None
    model_fidelity: Literal["conceptual", "specified", "evaluated"] = "conceptual"
    claim: ProcessClaim = Field(default_factory=ProcessClaim)

    @model_validator(mode="after")
    def unique_ports(self) -> "ProcessOperation":
        """Names are arbitrary, but port IDs within an operation are unambiguous."""
        unique([port.port_id for port in self.ports], "operation port ID")
        return self


class ProcessEndpoint(DomainModel):
    """A stable local process port reference; no external IR is required."""

    operation_id: Text
    port_id: Text


class ProcessStream(DomainModel):
    """A directed process stream with optional explicit nominal rate."""

    stream_id: Text
    source: ProcessEndpoint
    target: ProcessEndpoint
    kind: StreamKind
    quantity: ProcessQuantity | None = None
    batch_quantity: ProcessBatchQuantity | None = Field(None, exclude_if=lambda value: value is None)
    phase: Phase | None = None
    claim: ProcessClaim = Field(default_factory=ProcessClaim)

    @model_validator(mode="after")
    def compatible_quantity(self) -> "ProcessStream":
        """A stream's declared rate cannot represent a different physical kind."""
        if self.batch_quantity is not None and self.kind != "material":
            raise ValueError("Batch amounts are only valid for material streams.")
        if self.quantity is not None:
            allowed = {
                "material": {"mass_rate", "molar_rate", "volume_rate"},
                "energy": {"power"},
                "signal": {"signal"},
            }
            if self.quantity.kind not in allowed[self.kind]:
                raise ValueError("Stream rate dimension does not match stream kind.")
        if self.kind != "material" and self.phase is not None:
            raise ValueError("Phase is only valid for a material stream.")
        return self


class ProcessBalanceSpec(DomainModel):
    """Explicit conservation terms, all required for a numerical balance.

    Inlet + generation - outlet - consumption - accumulation is the
    physically conventional residual when generation and consumption are positive.
    This corrects the preliminary issue text's reversed source/sink signs.
    A missing term means an unknown balance, not an implicit zero.
    """

    balance_id: Text
    operation_id: Text
    quantity_kind: Literal["mass_rate", "molar_rate"]
    accumulation: ProcessQuantity | None = None
    generation: ProcessQuantity | None = None
    consumption: ProcessQuantity | None = None


class ProcessScenarioOverride(DomainModel):
    """A design-side parameter override, not a hardware control command."""

    operation_id: Text
    parameter: Text
    quantity: ProcessQuantity


class ProcessScenario(DomainModel):
    """Versioned conceptual alternatives requiring a separate model to predict output."""

    scenario_id: Text
    description: Text
    overrides: list[ProcessScenarioOverride] = Field(default_factory=list)
    claim: ProcessClaim = Field(default_factory=ProcessClaim)


class ProcessStep(DomainModel):
    """A declarative intended workflow step; it never commands a live runtime."""

    step_id: Text
    operation_id: Text
    depends_on: list[Text] = Field(default_factory=list)
    preconditions: list[Text] = Field(default_factory=list)
    description: Text | None = None


class ProcessIR(DomainDocument):
    """Standalone process topology and declared flows with no implicit execution."""

    domain: Literal["process"] = "process"
    schema_version: Literal["1.0", "1.1"] = "1.0"
    operations: list[ProcessOperation] = Field(min_length=1)
    streams: list[ProcessStream] = Field(default_factory=list)
    balances: list[ProcessBalanceSpec] = Field(default_factory=list)
    steps: list[ProcessStep] = Field(default_factory=list)
    scenarios: list[ProcessScenario] = Field(default_factory=list)
    evidence: list[ProcessEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_and_interfaces(self) -> "ProcessIR":
        """Check identity and topology without loading chemistry or site geometry."""
        if self.schema_version == "1.0" and any(stream.batch_quantity is not None for stream in self.streams):
            raise ValueError("Batch material quantities require ProcessIR schema_version 1.1.")
        unique([o.operation_id for o in self.operations], "process operation ID")
        unique([s.stream_id for s in self.streams], "process stream ID")
        unique([b.balance_id for b in self.balances], "process balance ID")
        unique([step.step_id for step in self.steps], "process workflow step ID")
        unique([s.scenario_id for s in self.scenarios], "process scenario ID")
        unique(
            [o.operation_id for o in self.operations] + [s.stream_id for s in self.streams],
            "process entity ID",
        )
        operations = {op.operation_id: op for op in self.operations}
        ports = {(op.operation_id, p.port_id): p for op in self.operations for p in op.ports}
        sources = {item.source_id for item in self.evidence}
        claims = [op.claim for op in self.operations]
        claims += [stream.claim for stream in self.streams]
        claims += [scenario.claim for scenario in self.scenarios]
        claims += [stream.quantity.claim for stream in self.streams if stream.quantity]
        claims += [stream.batch_quantity.claim for stream in self.streams if stream.batch_quantity]
        claims += [override.quantity.claim for scenario in self.scenarios for override in scenario.overrides]
        for item in self.balances:
            claims += [
                value.claim for value in (item.accumulation, item.generation, item.consumption)
                if value is not None
            ]
        for claim in claims:
            if set(claim.source_ids) - sources:
                raise ValueError("Process claim references an unknown evidence source.")
        for stream in self.streams:
            left = ports.get((stream.source.operation_id, stream.source.port_id))
            right = ports.get((stream.target.operation_id, stream.target.port_id))
            if left is None or right is None:
                raise ValueError(f"Stream '{stream.stream_id}' references a missing operation port.")
            if left.direction != "out" or right.direction != "in":
                raise ValueError(f"Stream '{stream.stream_id}' has an invalid port direction.")
            if left.kind != stream.kind or right.kind != stream.kind:
                raise ValueError(f"Stream '{stream.stream_id}' crosses incompatible port kinds.")
            if stream.quantity is not None and any(
                port.quantity_kind is not None and port.quantity_kind != stream.quantity.kind
                for port in (left, right)
            ):
                raise ValueError(f"Stream '{stream.stream_id}' has incompatible port quantity dimensions.")
            if left.quantity_kind and right.quantity_kind and left.quantity_kind != right.quantity_kind:
                raise ValueError(f"Stream '{stream.stream_id}' has incompatible declared port dimensions.")
            known_phases = {phase for phase in (left.phase, right.phase, stream.phase)
                            if phase not in (None, "unknown", "mixed")}
            if len(known_phases) > 1:
                raise ValueError(f"Stream '{stream.stream_id}' has incompatible phases.")
        for balance in self.balances:
            if balance.operation_id not in operations:
                raise ValueError("Balance references an unknown operation.")
            for term in (balance.accumulation, balance.generation, balance.consumption):
                if term is not None and term.kind != balance.quantity_kind:
                    raise ValueError("Balance term's dimensional kind disagrees with its balance.")
        step_ids = {step.step_id for step in self.steps}
        step_edges = {step.step_id: set(step.depends_on) for step in self.steps}
        for step in self.steps:
            if step.operation_id not in operations:
                raise ValueError("Process workflow step references an unknown operation.")
            unique(step.depends_on, "step dependencies")
            if not set(step.depends_on) <= step_ids:
                raise ValueError("Process workflow step has an unknown dependency.")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(step_id: str) -> None:
            """Reject workflow dependency cycles without banning flowsheet recycles."""
            if step_id in active:
                raise ValueError("Process workflow steps contain a dependency cycle.")
            if step_id in visited:
                return
            active.add(step_id)
            for dependency in sorted(step_edges[step_id]):
                visit(dependency)
            active.remove(step_id)
            visited.add(step_id)

        for step_id in sorted(step_ids):
            visit(step_id)
        for scenario in self.scenarios:
            unique([f"{entry.operation_id}:{entry.parameter}" for entry in scenario.overrides],
                   "scenario parameter override")
            for entry in scenario.overrides:
                if entry.operation_id not in operations:
                    raise ValueError("Scenario references an unknown operation.")
        return self

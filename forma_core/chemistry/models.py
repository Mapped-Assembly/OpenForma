"""Standalone chemical-domain contracts; these do not simulate reaction kinetics."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from forma_core.domains.base import DomainDocument, DomainModel, Text, unique


EvidenceBasis = Literal["specified", "measured", "derived", "assumed", "illustrative", "unknown"]
Phase = Literal["solid", "liquid", "gas", "aqueous", "plasma", "unknown"]


class ChemicalContext(DomainModel):
    """Only the inputs required to author a chemical-design document."""

    scope: Text
    chemical_requirements: list[Text] = Field(min_length=1)
    evidence_requirements: list[Text] = Field(min_length=1)


class EvidenceRecord(DomainModel):
    """A source reference or a transparently authored assumption.

    Publication date and access time are distinct. An undated source must give
    a reason, and an assumption must not masquerade as an external publication.
    """

    source_id: Text
    basis: EvidenceBasis
    title: Text
    publisher: Text | None = None
    locator: Text | None = None
    url: Text | None = None
    published_at: Text | None = None
    retrieved_at: Text | None = None
    effective_at: Text | None = None
    undated_reason: Text | None = None
    applicability: Text | None = None
    uncertainty: Text | None = None
    rationale: Text | None = None

    @model_validator(mode="after")
    def check_source(self) -> "EvidenceRecord":
        """Demand locatable evidence for claims marked specified or measured."""
        if self.basis in {"specified", "measured"}:
            if not self.publisher or not self.locator or not self.retrieved_at:
                raise ValueError("Specified/measured evidence requires publisher, locator and retrieval time.")
            if not self.published_at and not self.undated_reason:
                raise ValueError("Undated evidence requires an explicit undated_reason.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("An assumption or illustrative input requires rationale.")
        return self


class Claim(DomainModel):
    """Claim classification and optional exact source identifiers."""

    basis: EvidenceBasis = "unknown"
    source_ids: list[Text] = Field(default_factory=list)
    rationale: Text | None = None

    @model_validator(mode="after")
    def check_origin(self) -> "Claim":
        """Disallow claiming measured/specification authority without a source."""
        if self.basis in {"specified", "measured"} and not self.source_ids:
            raise ValueError("Specified/measured claims require source_ids.")
        if self.basis in {"assumed", "illustrative"} and not self.rationale:
            raise ValueError("Assumed/illustrative claims require rationale.")
        return self


class Species(DomainModel):
    """A species identity; formula and charge may be unknown, never inferred."""

    species_id: Text
    name: Text
    formula: Text | None = None
    charge: int | None = None
    phase: Phase | None = None
    registry_id: Text | None = None
    claim: Claim = Field(default_factory=Claim)


class MixtureComponent(DomainModel):
    """A component amount in one declared mixture basis."""

    species_id: Text
    value: float = Field(ge=0)
    unit: Text


class Mixture(DomainModel):
    """A compositional declaration with no automatic fraction normalization."""

    mixture_id: Text
    basis: Literal["mole_fraction", "mass_fraction", "molar_concentration"]
    components: list[MixtureComponent] = Field(min_length=1)
    complete: bool = False
    claim: Claim = Field(default_factory=Claim)


class ReactionTerm(DomainModel):
    """One positive exact stoichiometric coefficient, encoded as an integer/fraction."""

    species_id: Text
    coefficient: Text

    @field_validator("coefficient")
    @classmethod
    def check_coefficient(cls, value: str) -> str:
        """Reject floating-point and invalid fraction coefficients."""
        from fractions import Fraction

        import re

        if not re.fullmatch(r"[0-9]+(?:/[0-9]+)?", value):
            raise ValueError("Stoichiometric coefficients must be positive integer/rational strings.")
        if Fraction(value) <= 0:
            raise ValueError("Stoichiometric coefficient must be positive.")
        return value


class ChemicalCondition(DomainModel):
    """A declared condition with a supported dimensioned quantity and provenance."""

    condition_id: Text
    kind: Literal["temperature", "pressure", "ph", "concentration"]
    value: float
    unit: Text
    claim: Claim = Field(default_factory=Claim)


class ChemicalModelReference(DomainModel):
    """A pinned model reference, not an instruction to run a chemistry solver."""

    model_id: Text
    kind: Literal["kinetics", "equilibrium", "thermochemistry", "properties"]
    provider: Text
    version: Text
    applicability: Text | None = None
    claim: Claim = Field(default_factory=Claim)


class Reaction(DomainModel):
    """A design reaction, not an assertion of yield, safety or operating rate."""

    reaction_id: Text
    reactants: list[ReactionTerm] = Field(min_length=1)
    products: list[ReactionTerm] = Field(min_length=1)
    reversible: bool = False
    catalyst_species_ids: list[Text] = Field(default_factory=list)
    condition_ids: list[Text] = Field(default_factory=list)
    model_ids: list[Text] = Field(default_factory=list)
    claim: Claim = Field(default_factory=Claim)


class ChemicalIR(DomainDocument):
    """Versioned chemical species, mixtures and reactions with independently checkable semantics."""

    domain: Literal["chemical"] = "chemical"
    schema_version: Literal["1.0"] = "1.0"
    species: list[Species] = Field(default_factory=list)
    mixtures: list[Mixture] = Field(default_factory=list)
    reactions: list[Reaction] = Field(default_factory=list)
    conditions: list[ChemicalCondition] = Field(default_factory=list)
    models: list[ChemicalModelReference] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references(self) -> "ChemicalIR":
        """Enforce local identity and reference integrity without loading other IRs."""
        for values, label in (
            ([s.species_id for s in self.species], "species ID"),
            ([m.mixture_id for m in self.mixtures], "mixture ID"),
            ([r.reaction_id for r in self.reactions], "reaction ID"),
            ([c.condition_id for c in self.conditions], "condition ID"),
            ([m.model_id for m in self.models], "chemical model ID"),
            ([e.source_id for e in self.evidence], "chemical evidence ID"),
        ):
            unique(values, label)
        all_entity_ids = ([s.species_id for s in self.species] +
                          [m.mixture_id for m in self.mixtures] +
                          [r.reaction_id for r in self.reactions] +
                          [c.condition_id for c in self.conditions] +
                          [m.model_id for m in self.models])
        unique(all_entity_ids, "chemical entity ID")
        species = {s.species_id for s in self.species}
        conditions = {c.condition_id for c in self.conditions}
        models = {m.model_id for m in self.models}
        sources = {e.source_id for e in self.evidence}
        claims = [s.claim for s in self.species] + [m.claim for m in self.mixtures]
        claims += [r.claim for r in self.reactions] + [c.claim for c in self.conditions]
        claims += [m.claim for m in self.models]
        for claim in claims:
            if set(claim.source_ids) - sources:
                raise ValueError("Chemical claim references unknown evidence source.")
        for mixture in self.mixtures:
            unique([c.species_id for c in mixture.components], "mixture species")
            if any(c.species_id not in species for c in mixture.components):
                raise ValueError(f"Mixture '{mixture.mixture_id}' references an unknown species.")
        for reaction in self.reactions:
            for terms in (reaction.reactants, reaction.products):
                unique([term.species_id for term in terms], "reaction-side species")
                if any(t.species_id not in species for t in terms):
                    raise ValueError(f"Reaction '{reaction.reaction_id}' references an unknown species.")
            if any(s not in species for s in reaction.catalyst_species_ids):
                raise ValueError(f"Reaction '{reaction.reaction_id}' references an unknown catalyst species.")
            if any(c not in conditions for c in reaction.condition_ids):
                raise ValueError(f"Reaction '{reaction.reaction_id}' references an unknown condition.")
            if any(m not in models for m in reaction.model_ids):
                raise ValueError(f"Reaction '{reaction.reaction_id}' references an unknown model.")
        return self

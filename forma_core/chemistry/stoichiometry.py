"""Exact single-reaction batch bounds, separate from chemical design semantics.

Numeric outputs are rational strings in explicit units, as in chemical balance
results. A successful calculation is a theoretical bound, not actual conversion.
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from typing import Literal

from pydantic import Field, model_validator

from forma_core.domains.base import DomainModel, Finding, Text, fingerprint, unique
from forma_core.domains.providers import ArtifactReference, NATIVE_PROVIDER
from .models import ChemicalIR, Claim, EvidenceRecord
from .validation import BalanceResult, check_reaction_balance, parse_formula


Status = Literal["pass", "fail", "unknown", "unsupported"]
MOLE_UNITS = {"mol": Fraction(1), "mmol": Fraction(1, 1000), "kmol": Fraction(1000)}
MASS_UNITS = {"g": Fraction(1), "mg": Fraction(1, 1000), "kg": Fraction(1000)}
MOLAR_MASS_UNITS = {"g/mol": Fraction(1), "kg/mol": Fraction(1000)}


class FeedAmount(DomainModel):
    """A known batch amount; missing entries are not automatically zero."""

    species_id: Text
    value: float = Field(ge=0)
    unit: Text
    role: Literal["unspecified", "inert", "catalyst", "solvent"] = "unspecified"
    claim: Claim = Field(default_factory=Claim)


class MolarMass(DomainModel):
    """A supplied property, with its provenance declared rather than inferred."""

    species_id: Text
    value: float = Field(gt=0)
    unit: Text
    claim: Claim

    @model_validator(mode="after")
    def require_origin(self) -> MolarMass:
        """Require a source or explicit rationale for a usable property."""
        if self.claim.basis == "unknown" or not (self.claim.source_ids or self.claim.rationale):
            raise ValueError("Molar mass requires a known claim basis and a source or rationale.")
        return self


class StoichiometryRequest(DomainModel):
    """Pinned forward-reaction request; unsupported modes remain diagnosable."""

    schema_version: Literal["1.0"] = "1.0"
    chemical: ArtifactReference
    reaction_id: Text
    direction: Literal["forward", "reverse"] = "forward"
    mode: Literal["single_reaction_batch", "equilibrium", "reaction_network", "continuous_flow"] = "single_reaction_batch"
    inventory: list[FeedAmount] = Field(default_factory=list)
    inventory_policy: Literal["explicit", "zero_unlisted_nonreactants"] = "explicit"
    molar_masses: list[MolarMass] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    limiting_absolute_tolerance_mol: float = Field(1e-12, ge=0)
    limiting_relative_tolerance: float = Field(1e-9, ge=0, lt=1)
    mass_balance_tolerance_g: float = Field(1e-9, ge=0)

    @model_validator(mode="after")
    def unique_inputs(self) -> StoichiometryRequest:
        """Reject duplicate quantities/properties instead of overwriting them."""
        unique([item.species_id for item in self.inventory], "feed species")
        unique([item.species_id for item in self.molar_masses], "molar-mass species")
        unique([item.source_id for item in self.evidence], "evaluation evidence ID")
        return self


class SpeciesAmountResult(DomainModel):
    """Exact net accounting; initial products remain distinct from formation."""

    species_id: Text
    unit: Literal["mol"] = "mol"
    initial: str
    consumed: str
    formed: str
    final: str
    net_coefficient: str
    role: Literal["unspecified", "inert", "catalyst", "solvent"]
    molar_mass_g_per_mol: str | None = None
    initial_mass_g: str | None = None
    consumed_mass_g: str | None = None
    formed_mass_g: str | None = None
    final_mass_g: str | None = None


class ConservationResult(DomainModel):
    """Conservation of net species changes, not a claim of batch completeness."""

    reaction_balance: BalanceResult
    element_residuals_mol: dict[str, str]
    charge_residual_mol: str
    mass_status: Status
    mass_residual_g: str | None = None


class StoichiometryResult(DomainModel):
    """Portable calculation receipt using existing artifact and digest contracts."""

    schema_version: Literal["1.0"] = "1.0"
    model: Literal["single-reaction-stoichiometric-bound"] = "single-reaction-stoichiometric-bound"
    model_version: Literal["1.0"] = "1.0"
    status: Status
    input_digest: str
    evaluated_chemical: ArtifactReference
    request: StoichiometryRequest
    evidence: list[EvidenceRecord]
    assumptions: list[str]
    findings: list[Finding] = Field(default_factory=list)
    maximum_extent_mol: str | None = None
    limiting_species_ids: list[str] = Field(default_factory=list)
    amounts: list[SpeciesAmountResult] = Field(default_factory=list)
    conservation: ConservationResult | None = None


def evaluate_stoichiometry(document: ChemicalIR, request: StoichiometryRequest) -> StoichiometryResult:
    """Derive a forward batch upper bound with exact rational arithmetic.

Pydantic validation errors provide structured input diagnostics. Evaluation
findings distinguish invalid, unknown and unsupported scientific requests.
Revalidate mutable models at entry so edited inputs cannot bypass constraints.
"""
    document = ChemicalIR.model_validate(document.model_dump(mode="json"))
    request = StoichiometryRequest.model_validate(request.model_dump(mode="json"))
    reference = ArtifactReference(
        artifact_id=document.artifact_id, domain=document.domain,
        schema_version=document.schema_version, revision=document.revision,
        sha256=fingerprint(document),
    )
    result = StoichiometryResult(
        status="pass", input_digest=fingerprint({
            "document": document.model_dump(mode="json"), "request": request.model_dump(mode="json"),
        }),
        evaluated_chemical=reference, request=request,
        evidence=document.evidence + request.evidence,
        assumptions=[
            "Maximum forward extent assuming completion of the selected reaction; not actual conversion, equilibrium, isolated yield or throughput.",
            "Only the selected reaction is evaluated; no competing reactions or transport limitations are modeled.",
            "Amounts are batch inventories, not flow rates. Net stoichiometry determines consumption and formation.",
        ],
    )

    def stop(status: Status, code: str, location: str, message: str) -> StoichiometryResult:
        """Return a structured failure without inventing numerical outputs."""
        result.status = status
        result.findings.append(Finding(code=code, location=location, message=message,
                                       severity="error" if status == "fail" else "warning"))
        return result

    pin = request.chemical
    if (pin.provider_id != NATIVE_PROVIDER or pin.provider_version != reference.provider_version
            or any(getattr(pin, key) != getattr(reference, key) for key in
                   ("artifact_id", "domain", "schema_id", "schema_version", "revision", "sha256"))):
        return stop("fail", "stale_chemical_reference", "chemical", "Chemical artifact identity, revision or digest does not match the request.")
    if request.mode != "single_reaction_batch" or request.direction != "forward":
        return stop("unsupported", "stoichiometry_mode", "mode", "Only a single selected forward batch reaction is supported.")
    reaction = next((item for item in document.reactions if item.reaction_id == request.reaction_id), None)
    if reaction is None:
        return stop("fail", "unknown_reaction", request.reaction_id, "Selected reaction is not present in the pinned artifact.")
    sources: dict[str, EvidenceRecord] = {}
    for record in result.evidence:
        if record.source_id in sources and record != sources[record.source_id]:
            return stop("fail", "conflicting_evidence", record.source_id, "An evidence ID has conflicting document and request records.")
        sources[record.source_id] = record
    result.evidence = [sources[key] for key in sorted(sources)]
    species = {item.species_id: item for item in document.species}
    for item in [*request.inventory, *request.molar_masses]:
        if item.species_id not in species:
            return stop("fail", "unknown_species", item.species_id, "Input references a species outside the chemical artifact.")
        if set(item.claim.source_ids) - sources.keys():
            return stop("fail", "unknown_evidence", item.species_id, "Input claim references an unresolved evidence source.")
    balance = check_reaction_balance(document, reaction)
    if balance.status != "pass":
        return stop(balance.status, f"reaction_balance_{balance.status}", reaction.reaction_id, balance.message)

    coefficients: dict[str, Fraction] = defaultdict(Fraction)
    for terms, sign in ((reaction.reactants, -1), (reaction.products, 1)):
        for term in terms:
            coefficients[term.species_id] += sign * Fraction(term.coefficient)
    consumed_ids = {key for key, value in coefficients.items() if value < 0}
    if not consumed_ids:
        return stop("unsupported", "no_net_consumption", reaction.reaction_id, "Reaction has no net consumed species and no finite feed-derived extent bound.")
    roles = {item.species_id: item.role for item in request.inventory}
    for key in set(reaction.catalyst_species_ids) | {key for key, role in roles.items() if role != "unspecified"}:
        if coefficients.get(key, Fraction(0)) != 0:
            return stop("fail", "nonreacting_role_conflict", key, "A catalyst, solvent or inert species has nonzero net stoichiometry.")
    for key in reaction.catalyst_species_ids:
        if roles.get(key, "unspecified") == "unspecified":
            roles[key] = "catalyst"
    masses: dict[str, Fraction] = {}
    for prop in request.molar_masses:
        if prop.unit not in MOLAR_MASS_UNITS:
            return stop("fail", "molar_mass_unit", prop.species_id, "Supported molar-mass units are g/mol and kg/mol.")
        masses[prop.species_id] = Fraction(str(prop.value)) * MOLAR_MASS_UNITS[prop.unit]
    initial: dict[str, Fraction] = {}
    for feed in request.inventory:
        value = Fraction(str(feed.value))
        if feed.unit in MOLE_UNITS:
            initial[feed.species_id] = value * MOLE_UNITS[feed.unit]
        elif feed.unit in MASS_UNITS:
            if feed.species_id not in masses:
                return stop("unknown", "missing_molar_mass", feed.species_id, "Mass-to-mole conversion requires an explicit molar mass and provenance.")
            initial[feed.species_id] = value * MASS_UNITS[feed.unit] / masses[feed.species_id]
        else:
            return stop("fail", "feed_unit", feed.species_id, "Expected mol/mmol/kmol or g/mg/kg batch amounts; rates and concentrations are not batch amounts.")
    required = set(coefficients) | set(reaction.catalyst_species_ids)
    for key in sorted(required - initial.keys()):
        if key in consumed_ids or request.inventory_policy == "explicit":
            return stop("unknown", "missing_inventory", key, "Initial inventory is missing; omitted required feed is not zero.")
        initial[key] = Fraction(0)
        result.assumptions.append(f"Initial amount of '{key}' is zero under the explicitly selected zero_unlisted_nonreactants policy.")
    result.assumptions.append("Inventory covers supplied species and selected reaction participants; unrelated document species are outside the batch scope.")
    extent = min(initial[key] / -coefficients[key] for key in consumed_ids)
    result.maximum_extent_mol = str(extent)
    absolute = Fraction(str(request.limiting_absolute_tolerance_mol))
    relative = Fraction(str(request.limiting_relative_tolerance))
    for key in sorted(initial):
        coefficient = coefficients.get(key, Fraction(0))
        delta = coefficient * extent
        consumed, formed = max(-delta, Fraction(0)), max(delta, Fraction(0))
        final = initial[key] + delta
        if key in consumed_ids and final <= absolute + relative * initial[key]:
            result.limiting_species_ids.append(key)
        item = SpeciesAmountResult(
            species_id=key, initial=str(initial[key]), consumed=str(consumed), formed=str(formed),
            final=str(final), net_coefficient=str(coefficient), role=roles.get(key, "unspecified"),
        )
        if key in masses:
            item.molar_mass_g_per_mol = str(masses[key])
            item.initial_mass_g = str(initial[key] * masses[key])
            item.consumed_mass_g = str(consumed * masses[key])
            item.formed_mass_g = str(formed * masses[key])
            item.final_mass_g = str(final * masses[key])
        result.amounts.append(item)
    elements: dict[str, Fraction] = defaultdict(Fraction)
    charge = Fraction(0)
    for key, coefficient in coefficients.items():
        delta = coefficient * extent
        formula, ionic_charge = species[key].formula, species[key].charge
        assert formula is not None and ionic_charge is not None  # Guaranteed by balance pass.
        for element, count in parse_formula(formula).items():
            elements[element] += count * delta
        charge += ionic_charge * delta
    active = {key for key, coefficient in coefficients.items() if coefficient}
    mass_residual = (sum((coefficients[key] * extent * masses[key] for key in active), Fraction(0))
                     if active <= masses.keys() else None)
    mass_status: Status = "unknown" if mass_residual is None else (
        "pass" if abs(mass_residual) <= Fraction(str(request.mass_balance_tolerance_g)) else "fail")
    result.conservation = ConservationResult(
        reaction_balance=balance, element_residuals_mol={key: str(value) for key, value in sorted(elements.items())},
        charge_residual_mol=str(charge), mass_status=mass_status,
        mass_residual_g=str(mass_residual) if mass_residual is not None else None,
    )
    if mass_status != "pass":
        result.findings.append(Finding(
            code=f"mass_balance_{mass_status}", location=reaction.reaction_id,
            severity="error" if mass_status == "fail" else "warning",
            message="Supplied molar masses do not conserve mass for the calculated extent." if mass_status == "fail"
            else "Missing molar masses leave mass conservation unknown; mole/element results remain available.",
        ))
    if mass_status == "fail":
        result.status = "fail"
    return result

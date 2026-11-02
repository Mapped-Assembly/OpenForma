"""Conservative chemical-domain checks with exact stoichiometric accounting."""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import re
from typing import Literal

from pydantic import Field

from forma_core.domains.base import DomainModel, Finding
from .models import ChemicalIR, Reaction


# Atomic symbols only; this is not an isotope/speciation or chemical-structure grammar.
ELEMENTS = frozenset((
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba "
    "La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb "
    "Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs "
    "Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split())


class BalanceResult(DomainModel):
    """An exact symbolic result; 'unknown' and 'unsupported' are never passes."""

    reaction_id: str
    status: Literal["pass", "fail", "unknown", "unsupported"]
    residuals: dict[str, str] = Field(default_factory=dict)
    message: str


class UnsupportedFormula(ValueError):
    """Formula lies outside the deliberately bounded neutral-formula grammar."""


def parse_formula(formula: str) -> dict[str, int]:
    """Parse element symbols and nested parentheses with positive integer counts.

    The supported grammar is formulas such as H2O, CH3OH and Ca(OH)2.
    Isotopes, dot hydrates, ions encoded in formula text, polymers, variable
    stoichiometry and brackets are deliberately unsupported. Charge is stored
    separately in Species. No property or chemical identity is inferred.
    """
    if not formula or len(formula) > 256 or not re.fullmatch(r"[A-Za-z0-9()]+", formula):
        raise UnsupportedFormula("Formula grammar does not support this notation.")
    index = 0

    def group(nested: bool = False) -> dict[str, int]:
        """Return a counted group, consuming a final ')' only if nested."""
        nonlocal index
        result: dict[str, int] = defaultdict(int)
        had_content = False
        while index < len(formula):
            char = formula[index]
            if char == ")":
                if not nested or not had_content:
                    raise UnsupportedFormula("Unexpected or empty closing parenthesis.")
                index += 1
                return dict(result)
            if char == "(":
                index += 1
                atoms = group(True)
            elif char.isupper():
                match = re.match(r"[A-Z][a-z]?", formula[index:])
                if match is None or match.group() not in ELEMENTS:
                    raise UnsupportedFormula("Unknown element symbol.")
                symbol = match.group()
                index += len(symbol)
                atoms = {symbol: 1}
            else:
                raise UnsupportedFormula("Unsupported token in chemical formula.")
            had_content = True
            count = re.match(r"[0-9]+", formula[index:])
            multiplier = int(count.group()) if count else 1
            if count:
                index += len(count.group())
            if multiplier <= 0 or multiplier > 1_000_000:
                raise UnsupportedFormula("Formula count must be positive and bounded.")
            for symbol, quantity in atoms.items():
                result[symbol] += quantity * multiplier
                if result[symbol] > 1_000_000:
                    raise UnsupportedFormula("Expanded formula exceeds count limit.")
        if nested:
            raise UnsupportedFormula("Unclosed parenthesis.")
        if not had_content:
            raise UnsupportedFormula("Empty chemical formula.")
        return dict(result)

    values = group()
    if index != len(formula):
        raise UnsupportedFormula("Trailing chemical formula tokens.")
    return values


def check_reaction_balance(document: ChemicalIR, reaction: Reaction) -> BalanceResult:
    """Compute exact product-minus-reactant element and charge residuals."""
    species = {s.species_id: s for s in document.species}
    residuals: dict[str, Fraction] = defaultdict(Fraction)
    missing_charge = False
    try:
        for side, sign in ((reaction.reactants, -1), (reaction.products, 1)):
            for term in side:
                item = species[term.species_id]
                if item.formula is None:
                    return BalanceResult(reaction_id=reaction.reaction_id, status="unknown",
                                         message=f"Formula missing for '{item.species_id}'.")
                atoms = parse_formula(item.formula)
                coeff = Fraction(term.coefficient) * sign
                for element, amount in atoms.items():
                    residuals[element] += coeff * amount
                if item.charge is None:
                    missing_charge = True
                else:
                    residuals["charge"] += coeff * item.charge
    except UnsupportedFormula as exc:
        return BalanceResult(reaction_id=reaction.reaction_id, status="unsupported",
                             message=f"Unsupported chemical formula: {exc}")
    differences = {key: str(value) for key, value in sorted(residuals.items()) if value}
    noncharge = {key: value for key, value in differences.items() if key != "charge"}
    if noncharge or (not missing_charge and "charge" in differences):
        return BalanceResult(reaction_id=reaction.reaction_id, status="fail",
                             residuals=differences, message="Atom or charge conservation failed.")
    if missing_charge:
        return BalanceResult(reaction_id=reaction.reaction_id, status="unknown",
                             residuals=differences, message="At least one species has unknown charge.")
    return BalanceResult(reaction_id=reaction.reaction_id, status="pass",
                         message="Element and charge balances pass on the supported formula grammar.")


def validate_chemical(document: ChemicalIR) -> list[Finding]:
    """Report chemical-semantic findings independently of cross-domain links."""
    findings: list[Finding] = []
    for item in document.mixtures:
        expected_unit = "1" if item.basis in {"mole_fraction", "mass_fraction"} else "mol/L"
        for component in item.components:
            if component.unit != expected_unit:
                findings.append(Finding(code="composition_unit", location=f"{item.mixture_id}.{component.species_id}",
                                        message=f"Expected '{expected_unit}' for {item.basis}."))
        if item.basis in {"mass_fraction", "mole_fraction"}:
            total = sum(component.value for component in item.components)
            if total > 1.0 + 1e-9:
                findings.append(Finding(code="composition_overallocated", location=item.mixture_id,
                                        message="Fractions exceed unity; no normalization is performed."))
            elif total < 1.0 - 1e-9:
                findings.append(Finding(code="composition_incomplete", location=item.mixture_id,
                                        severity="error" if item.complete else "warning",
                                        message="Unallocated mixture fraction remains unknown."))
    valid_conditions = {
        "temperature": {"K", "degC"}, "pressure": {"Pa", "kPa", "bar"},
        "ph": {"1"}, "concentration": {"mol/L"},
    }
    for condition in document.conditions:
        if condition.unit not in valid_conditions[condition.kind]:
            findings.append(Finding(code="condition_unit", location=condition.condition_id,
                                    message=f"Invalid {condition.kind} unit '{condition.unit}'."))
        if (condition.kind == "temperature" and
                ((condition.unit == "K" and condition.value < 0) or
                 (condition.unit == "degC" and condition.value < -273.15))):
            findings.append(Finding(code="physical_temperature", location=condition.condition_id,
                                    message="Temperature is below absolute zero."))
        if condition.kind == "pressure" and condition.value < 0:
            findings.append(Finding(code="physical_pressure", location=condition.condition_id,
                                    message="Absolute pressure cannot be negative."))
        if condition.kind == "ph" and not 0 <= condition.value <= 14:
            findings.append(Finding(code="ph_range", location=condition.condition_id,
                                    severity="warning", message="pH outside 0–14 needs an explicit validated model."))
        if condition.kind == "concentration" and condition.value < 0:
            findings.append(Finding(code="physical_concentration", location=condition.condition_id,
                                    message="Concentration cannot be negative."))
    for reaction in document.reactions:
        balance = check_reaction_balance(document, reaction)
        if balance.status != "pass":
            findings.append(Finding(code=f"reaction_balance_{balance.status}", location=reaction.reaction_id,
                                    severity="error" if balance.status == "fail" else "warning",
                                    message=balance.message))
    return findings

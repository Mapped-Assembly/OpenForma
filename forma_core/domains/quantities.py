"""Deterministic dimension-aware scalar conversions, including affine temperatures."""
from __future__ import annotations

from fractions import Fraction

# unit -> (dimension, scale to canonical unit, offset to canonical unit).
UNITS: dict[str, tuple[str, Fraction, Fraction]] = {
    'g': ('mass', Fraction(1), Fraction(0)), 'mg': ('mass', Fraction(1, 1000), Fraction(0)),
    'kg': ('mass', Fraction(1000), Fraction(0)),
    'mol': ('amount', Fraction(1), Fraction(0)), 'mmol': ('amount', Fraction(1, 1000), Fraction(0)),
    'kmol': ('amount', Fraction(1000), Fraction(0)),
    'K': ('temperature', Fraction(1), Fraction(0)),
    'degC': ('temperature', Fraction(1), Fraction('273.15')),
    'delta_degC': ('temperature_difference', Fraction(1), Fraction(0)),
    'J': ('energy', Fraction(1), Fraction(0)), 'kJ': ('energy', Fraction(1000), Fraction(0)),
    'W': ('heat_rate', Fraction(1), Fraction(0)), 'kW': ('heat_rate', Fraction(1000), Fraction(0)),
    'J/(kg*K)': ('specific_heat', Fraction(1), Fraction(0)),
    'J/(g*K)': ('specific_heat', Fraction(1000), Fraction(0)),
    'J/K': ('capacity', Fraction(1), Fraction(0)),
    's': ('time', Fraction(1), Fraction(0)), 'min': ('time', Fraction(60), Fraction(0)),
    'L': ('volume', Fraction(1), Fraction(0)), 'mL': ('volume', Fraction(1, 1000), Fraction(0)),
    'Pa': ('pressure', Fraction(1), Fraction(0)), 'kPa': ('pressure', Fraction(1000), Fraction(0)),
    'mol/L': ('concentration', Fraction(1), Fraction(0)),
    'Pa*s': ('dynamic_viscosity', Fraction(1), Fraction(0)),
    'mPa*s': ('dynamic_viscosity', Fraction(1, 1000), Fraction(0)),
    '1': ('dimensionless', Fraction(1), Fraction(0)),
}


def convert(value: Fraction, source: str, target: str, dimension: str, *, interval: bool = False) -> Fraction | None:
    """Convert compatible values; uncertainty/resolution use scale only, never offsets."""
    left, right = UNITS.get(source), UNITS.get(target)
    if dimension == 'temperature_difference':
        left = ('temperature_difference', Fraction(1), Fraction(0)) if source == 'K' else left
        right = ('temperature_difference', Fraction(1), Fraction(0)) if target == 'K' else right
    if left is None or right is None or left[0] != dimension or right[0] != dimension:
        return None
    return (value * left[1] + (0 if interval else left[2] - right[2])) / right[1]

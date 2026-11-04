"""Analytical, mass-conserving co-current passive membrane exchange model.

Concentrations are nmol/mm³ (numerically mM); flows are mm³/s. Each channel
is well mixed across its cross-section. Axial diffusion, bulk transmembrane
flow, cell uptake and reactions are excluded. Effective permeability is an
explicit demonstration assumption, not inferred from the membrane pore size.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MembraneExchange:
    """Couple two plug-flow channels through J = P (Cv - Ce)."""

    vascular_flow_mm3_s: float
    epithelial_flow_mm3_s: float
    width_mm: float
    length_mm: float
    permeability_mm_s: float
    vascular_inlet_mm: float = 1.0
    epithelial_inlet_mm: float = 0.0

    def __post_init__(self) -> None:
        """Reject nonfinite inputs, nonpositive geometry/flows and negative solute."""
        positive = (self.vascular_flow_mm3_s, self.epithelial_flow_mm3_s,
                    self.width_mm, self.length_mm)
        nonnegative = (self.permeability_mm_s, self.vascular_inlet_mm, self.epithelial_inlet_mm)
        if any(not math.isfinite(v) or v <= 0 for v in positive):
            raise ValueError("Geometry and flows must be positive and finite.")
        if any(not math.isfinite(v) or v < 0 for v in nonnegative):
            raise ValueError("Permeability and inlet concentrations must be nonnegative and finite.")

    def concentrations(self, x_mm: float) -> tuple[float, float]:
        """Return vascular/epithelial concentrations at position x in mM."""
        if not math.isfinite(x_mm) or not 0 <= x_mm <= self.length_mm:
            raise ValueError("Position must be within the modeled channel.")
        qv, qe = self.vascular_flow_mm3_s, self.epithelial_flow_mm3_s
        weighted = (qv*self.vascular_inlet_mm + qe*self.epithelial_inlet_mm)/(qv+qe)
        decay = math.exp(-self.permeability_mm_s*self.width_mm*(1/qv+1/qe)*x_mm)
        difference = (self.vascular_inlet_mm-self.epithelial_inlet_mm)*decay
        return weighted + qe/(qv+qe)*difference, weighted - qv/(qv+qe)*difference

    def flux_nmol_mm2_s(self, x_mm: float) -> float:
        """Return signed net solute flux; positive is vascular → epithelial."""
        cv, ce = self.concentrations(x_mm)
        return self.permeability_mm_s*(cv-ce)

    def mass_flow_nmol_s(self, x_mm: float) -> float:
        """Return the total solute carried by both channels at x."""
        cv, ce = self.concentrations(x_mm)
        return self.vascular_flow_mm3_s*cv + self.epithelial_flow_mm3_s*ce

    def report(self) -> dict[str, object]:
        """Export reproducible parameters, profiles and the mass-balance residual."""
        cv, ce = self.concentrations(self.length_mm)
        transfer = self.epithelial_flow_mm3_s*(ce-self.epithelial_inlet_mm)
        return {
            "model": "steady-state 1D co-current plug flow, passive membrane exchange",
            "permeability_mm_s": self.permeability_mm_s,
            "permeability_provenance": "Illustrative effective value, not measured or calibrated.",
            "inlet_mM": {"vascular": self.vascular_inlet_mm, "epithelial": self.epithelial_inlet_mm},
            "outlet_mM": {"vascular": cv, "epithelial": ce},
            "net_transfer_nmol_s": transfer,
            "mass_in_nmol_s": self.mass_flow_nmol_s(0),
            "mass_out_nmol_s": self.mass_flow_nmol_s(self.length_mm),
            "mass_balance_residual_nmol_s": self.mass_flow_nmol_s(self.length_mm)-self.mass_flow_nmol_s(0),
            "profile": [{"x_mm": x, "vascular_mM": self.concentrations(x)[0],
                         "epithelial_mM": self.concentrations(x)[1],
                         "flux_nmol_mm2_s": self.flux_nmol_mm2_s(x)}
                        for x in (self.length_mm*i/100 for i in range(101))],
        }

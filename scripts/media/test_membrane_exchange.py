"""Independent conservation and limiting-case checks for membrane exchange."""
from __future__ import annotations

import math
import unittest
from membrane_exchange import MembraneExchange


class MembraneExchangeTests(unittest.TestCase):
    """Check physical invariants rather than renderer appearance alone."""

    def model(self, permeability: float = .005, cv: float = 1, ce: float = 0) -> MembraneExchange:
        """Construct the documented demonstration case."""
        return MembraneExchange(.5, 1/6, 1, 20, permeability, cv, ce)

    def test_mass_conservation_and_bounds(self) -> None:
        """Exchange preserves total solute while the receiving concentration rises."""
        m = self.model()
        previous_cv, previous_ce = m.concentrations(0)
        for i in range(201):
            cv, ce = m.concentrations(i/10)
            self.assertAlmostEqual(.5*cv+ce/6, .5, places=13)
            self.assertTrue(0 <= ce <= cv <= 1)
            self.assertLessEqual(cv, previous_cv+1e-12)
            self.assertGreaterEqual(ce, previous_ce-1e-12)
            previous_cv, previous_ce = cv, ce
        cv, ce = m.concentrations(20)
        self.assertAlmostEqual(.5*(1-cv), ce/6)

    def test_zero_permeability(self) -> None:
        """An impermeable membrane transfers no solute."""
        m = self.model(0)
        self.assertEqual(m.concentrations(20), (1, 0))
        self.assertEqual(m.flux_nmol_mm2_s(10), 0)

    def test_equal_and_reversed_gradient(self) -> None:
        """Equilibrium stops exchange; reversed concentrations reverse net flux."""
        m = self.model(cv=.4, ce=.4)
        self.assertEqual(m.concentrations(20), (.4, .4))
        self.assertEqual(m.flux_nmol_mm2_s(5), 0)
        reversed_model = self.model(cv=0, ce=1)
        self.assertLess(reversed_model.flux_nmol_mm2_s(10), 0)
        cv, ce = reversed_model.concentrations(20)
        self.assertGreater(cv, 0)
        self.assertLess(ce, 1)

    def test_differential_balance(self) -> None:
        """An independent finite difference matches each channel's flux balance."""
        m = self.model()
        dx = 1e-4
        for x in (1, 5, 10, 19):
            before, after = m.concentrations(x-dx), m.concentrations(x+dx)
            j = m.flux_nmol_mm2_s(x)
            self.assertAlmostEqual(.5*(after[0]-before[0])/(2*dx), -j, places=10)
            self.assertAlmostEqual((after[1]-before[1])/(6*2*dx), j, places=10)

    def test_large_permeability_equilibrates(self) -> None:
        """High permeability approaches the flow-weighted equilibrium, not 0.5."""
        cv, ce = self.model(1).concentrations(20)
        self.assertAlmostEqual(cv, .75)
        self.assertAlmostEqual(ce, .75)

    def test_invalid_parameters_fail(self) -> None:
        """Reject unphysical and nonfinite parameters."""
        for value in (-.1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                self.model(value)
        with self.assertRaises(ValueError):
            self.model().concentrations(21)


if __name__ == '__main__':
    unittest.main()

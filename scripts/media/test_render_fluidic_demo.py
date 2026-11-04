"""Regression checks for connected chip geometry and real-time flow animation."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from render_fluidic_demo import LEFT, RIGHT, ROOT, load_fixture, render_frame, tracer_positions, iso


class FluidicPreviewTests(unittest.TestCase):
    """Protect the visual connections, flow ratio and animation loop."""

    def test_real_time_speed_and_loop(self) -> None:
        """Both channels use one spatial scale and return to the same loop phase."""
        circuits, _ = load_fixture(ROOT / "examples/organ-on-chip/two-channel.json")
        self.assertAlmostEqual(circuits[1].speed_mm_s / circuits[0].speed_mm_s, 3)
        for circuit in circuits:
            before = tracer_positions(circuit, 0)
            after = tracer_positions(circuit, .05)
            self.assertAlmostEqual(after[0] - before[0], circuit.speed_mm_s * .05 * 30)
            for initial, final in zip(sorted(before), sorted(tracer_positions(circuit, 12))):
                self.assertAlmostEqual(initial, final)
            self.assertTrue(all(LEFT <= x < RIGHT for x in before))

    def test_membrane_contacts_channels(self) -> None:
        """No background-colored gap exists at either membrane/channel interface."""
        circuits, membrane = load_fixture(ROOT / "examples/organ-on-chip/two-channel.json")
        image = render_frame(circuits, membrane, 1)
        background = image.getpixel((10, 10))
        for along in (100,300,500):
            for z in range(61,76):
                x,y=iso(along,0,z)
                self.assertNotEqual(image.getpixel((round(x),round(y))), background)
        for circuit,z in zip(circuits,(103,33)):
            for x,y in (iso(-90,0,z),iso(600,60,z),iso(660,60,z)):
                self.assertNotEqual(image.getpixel((round(x),round(y))), background)
        # Orthogonal world axes have equal projected length: true isometry.
        origin=iso(0,0,0)
        for endpoint in (iso(1,0,0),iso(0,1,0),iso(0,0,1)):
            self.assertAlmostEqual(sum((a-b)**2 for a,b in zip(origin,endpoint)),1)

    def test_unsupported_networks_fail(self) -> None:
        """The renderer refuses a missing waste endpoint and extra dosing branch."""
        for name in ("broken-missing-outlet.json", "with-dosing.json"):
            with self.assertRaises(ValueError):
                load_fixture(ROOT / "examples/organ-on-chip" / name)

    def test_changed_geometry_fails(self) -> None:
        """Changed dimensions cannot silently retain hard-coded display labels."""
        data = json.loads((ROOT / "examples/organ-on-chip/two-channel.json").read_text())
        data["fluidic_system"]["channels"][2]["length_mm"] = 25
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_fixture(path)


if __name__ == "__main__":
    unittest.main()

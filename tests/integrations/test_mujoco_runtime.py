"""Physics-level checks for live Forma control sessions."""

import json
from pathlib import Path
import unittest
from importlib.util import find_spec

from forma_core.mechanics.mujoco_runtime import MuJoCoRuntime, compile_project


class RuntimeTests(unittest.TestCase):
    """Exercise supported CAD input, control, and lifecycle with the optional engine."""

    def setUp(self) -> None:
        """Remove all preview samples so they cannot supply simulated state."""
        path = Path(__file__).resolve().parents[2] / "examples/print_in_place_hinge.json"
        self.project = json.loads(path.read_text())
        self.track = self.project["cad_model"]["kinematics"]["tracks"][0]
        del self.track["samples"]

    @unittest.skipUnless(find_spec("mujoco"), "requires the mujoco extra")
    def test_live_session(self) -> None:
        """New targets alter future motion, and reset restores the initial state."""
        with MuJoCoRuntime(self.project) as session:
            initial = session.state()
            session.start()
            session.set_target(1.0)
            self.assertEqual(session.state().position, 0.0)
            transient = session.step()
            self.assertGreater(transient.position, 0.0)
            self.assertLess(transient.position, 1.0)
            self.assertAlmostEqual(session.step(99).position, 1.0)
            session.pause()
            paused = session.state()
            with self.assertRaises(RuntimeError):
                session.step()
            self.assertEqual(session.state(), paused)
            with self.assertRaises(ValueError):
                session.set_target(10)
            session.set_target(0.25)
            session.start()
            self.assertAlmostEqual(session.step(100).position, 0.25)
            self.assertEqual(session.reset(), initial)
        session.close()
        with self.assertRaises(RuntimeError):
            session.start()

    @unittest.skipUnless(find_spec("mujoco"), "requires the mujoco extra")
    def test_slider_units(self) -> None:
        """A 10 mm prismatic joint accepts SI targets and reports meters."""
        self.track["joint"].update(type="prismatic", unit="mm", upper_limit=10)
        with MuJoCoRuntime(self.project) as session:
            self.assertEqual(session.control.maximum, 0.01)
            session.start()
            session.set_target(0.005)
            self.assertAlmostEqual(session.step(2000).position, 0.005, places=5)

    def test_reject_unresolved_target(self) -> None:
        """Invalid part references cannot produce a misleading live control."""
        self.track["target_ref"] = "missing"
        with self.assertRaisesRegex(ValueError, "placement"):
            compile_project(self.project)

"""Regression tests for device-neutral XR coordinate-frame registration."""

import math
import unittest

from forma_core.spatial import (
    FrameEdge,
    FrameGraph,
    RigidTransform,
    openforma_mm_to_webxr_m,
    webxr_m_to_openforma_mm,
)


class SpatialFrameTests(unittest.TestCase):
    """Validate transform geometry, integrity and time provenance."""

    def test_metric_axes_and_roundtrip(self) -> None:
        """OpenForma Z-up millimetres map to WebXR Y-up metres."""
        point = (120.0, 200.0, 45.0)
        self.assertEqual(openforma_mm_to_webxr_m(point), (0.12, 0.045, -0.2))
        self.assertEqual(webxr_m_to_openforma_mm((0.12, 0.045, -0.2)), point)

    def test_rotation_composition_and_inverse(self) -> None:
        """Rotation is applied before translation, and inverses cancel."""
        q = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))
        a_from_b = RigidTransform((1.0, 0.0, 0.0), q)
        b_from_c = RigidTransform((0.0, 2.0, 0.0))
        a_from_c = a_from_b.compose(b_from_c)
        coordinates = a_from_c.apply((1.0, 0.0, 0.0))
        for actual, expected in zip(coordinates, (-1.0, 1.0, 0.0)):
            self.assertAlmostEqual(actual, expected, places=6)
        recovered = a_from_c.inverse().apply(coordinates)
        for actual, expected in zip(recovered, (1.0, 0.0, 0.0)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_graph_registration_and_wire_roundtrip(self) -> None:
        """Resolve machine frames through an external anchor without a device SDK."""
        frames = FrameGraph()
        frames.set_edge(FrameEdge(
            "world", "table-anchor", RigidTransform((2.0, 0.0, 1.0)),
            "measured", timestamp_ns=1_000_000_000,
        ))
        frames.set_edge(FrameEdge(
            "table-anchor", "so101", RigidTransform((0.2, 0.0, 0.0)),
            "specified",
        ))
        frames.set_edge(FrameEdge(
            "so101", "tool", RigidTransform((0.0, 0.1, 0.0)),
            "simulated", timestamp_ns=1_010_000_000,
        ))
        result = frames.resolve("world", "tool", now_ns=1_020_000_000,
                                max_age_ns=100_000_000)
        self.assertEqual(result.translation_m, (2.2, 0.1, 1.0))
        restored = FrameGraph.from_dict(frames.to_dict())
        self.assertEqual(restored.resolve("table-anchor", "tool").translation_m,
                         (0.2, 0.1, 0.0))

    def test_rejects_stale_unknown_or_disconnected_frames(self) -> None:
        """Absent tracking is not silently treated as a world origin."""
        frames = FrameGraph()
        frames.set_edge(FrameEdge("world", "head", RigidTransform(),
                                  "measured", timestamp_ns=20))
        frames.set_edge(FrameEdge("other", "robot", RigidTransform(), "specified"))
        with self.assertRaisesRegex(ValueError, "Stale"):
            frames.resolve("world", "head", now_ns=100, max_age_ns=10)
        with self.assertRaisesRegex(ValueError, "not connected"):
            frames.resolve("world", "robot")
        with self.assertRaises(KeyError):
            frames.resolve("world", "unseen")

    def test_invalid_data_parent_changes_and_cycles(self) -> None:
        """Reject invalid rotations and accidental frame graph topology changes."""
        with self.assertRaises(ValueError):
            RigidTransform(rotation_xyzw=(0, 0, 0, 0))
        with self.assertRaises(ValueError):
            RigidTransform(translation_m=(float("nan"), 0, 0))
        frames = FrameGraph()
        frames.set_edge(FrameEdge("world", "camera", RigidTransform(), "measured", 5))
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            frames.set_edge(FrameEdge("world", "camera", RigidTransform(), "measured", 4))
        with self.assertRaisesRegex(ValueError, "change parent"):
            frames.set_edge(FrameEdge("table", "camera", RigidTransform(), "measured", 6))
        with self.assertRaisesRegex(ValueError, "cycle"):
            frames.set_edge(FrameEdge("camera", "world", RigidTransform(), "specified"))
        with self.assertRaises(ValueError):
            FrameGraph.from_dict({"format": "other", "version": 1, "edges": []})


if __name__ == "__main__":
    unittest.main()

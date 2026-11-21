"""Device-agnostic coordinate frames for registering physical and virtual systems.

The spatial module does not own tracking, device calibration, scene geometry,
or machine control. Pose providers publish child-to-parent transforms here.
All transforms are right-handed, metric, and explicitly timestamped.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Literal, Mapping, Sequence

Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]  # x, y, z, w
Provenance = Literal["measured", "estimated", "specified", "simulated"]


def _vector(values: Sequence[float], count: int, name: str) -> tuple[float, ...]:
    """Validate a finite vector without silently dropping coordinates."""
    if len(values) != count:
        raise ValueError(f"{name} must contain {count} values")
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain finite numbers") from exc
    if not all(isfinite(value) for value in result):
        raise ValueError(f"{name} must contain finite numbers")
    return result


def _multiply(left: Quaternion, right: Quaternion) -> Quaternion:
    """Return the Hamilton product of two xyzw quaternions."""
    x, y, z, w = left
    a, b, c, d = right
    return (
        w * a + x * d + y * c - z * b,
        w * b - x * c + y * d + z * a,
        w * c + x * b - y * a + z * d,
        w * d - x * a - y * b - z * c,
    )


def _rotate(quaternion: Quaternion, vector: Vector3) -> Vector3:
    """Rotate a metric vector by a unit xyzw quaternion."""
    x, y, z, w = quaternion
    vx, vy, vz = vector
    tx, ty, tz = (
        2.0 * (y * vz - z * vy),
        2.0 * (z * vx - x * vz),
        2.0 * (x * vy - y * vx),
    )
    return (
        vx + w * tx + y * tz - z * ty,
        vy + w * ty + z * tx - x * tz,
        vz + w * tz + x * ty - y * tx,
    )


@dataclass(frozen=True)
class RigidTransform:
    """An SE(3) transform mapping points from a source into a target frame.

    Translation is in metres; rotation is a normalized xyzw quaternion.
    No implicit millimetre conversion or coordinate-axis permutation occurs.
    """

    translation_m: Vector3 = (0.0, 0.0, 0.0)
    rotation_xyzw: Quaternion = (0.0, 0.0, 0.0, 1.0)

    def __post_init__(self) -> None:
        """Reject malformed or unnormalized rigid transforms."""
        translation = _vector(self.translation_m, 3, "translation_m")
        rotation = _vector(self.rotation_xyzw, 4, "rotation_xyzw")
        norm = sqrt(sum(value * value for value in rotation))
        if abs(norm - 1.0) > 1e-5:
            raise ValueError("rotation_xyzw must be a unit quaternion")
        object.__setattr__(self, "translation_m", translation)
        object.__setattr__(self, "rotation_xyzw", rotation)

    def apply(self, point_m: Sequence[float]) -> Vector3:
        """Transform a 3D point expressed in metres."""
        x, y, z = _rotate(self.rotation_xyzw, _vector(point_m, 3, "point_m"))
        tx, ty, tz = self.translation_m
        return (x + tx, y + ty, z + tz)

    def compose(self, other: RigidTransform) -> RigidTransform:
        """Compose self(T_A_B) with other(T_B_C) to produce T_A_C."""
        return RigidTransform(
            translation_m=self.apply(other.translation_m),
            rotation_xyzw=_multiply(self.rotation_xyzw, other.rotation_xyzw),
        )

    def inverse(self) -> RigidTransform:
        """Return the inverse transform T_B_A."""
        x, y, z, w = self.rotation_xyzw
        inverse_rotation = (-x, -y, -z, w)
        tx, ty, tz = self.translation_m
        return RigidTransform(
            translation_m=_rotate(inverse_rotation, (-tx, -ty, -tz)),
            rotation_xyzw=inverse_rotation,
        )

    def to_dict(self) -> dict[str, list[float]]:
        """Return a JSON-safe transform with explicit units and ordering."""
        return {
            "translation_m": list(self.translation_m),
            "rotation_xyzw": list(self.rotation_xyzw),
        }


@dataclass(frozen=True)
class FrameEdge:
    """Pose T_parent_child supplied by a tracking or registration provider.

    Timestamp is monotonic nanoseconds in the *provider's* clock domain;
    callers may check freshness only when they share that clock domain.
    """

    parent: str
    child: str
    transform: RigidTransform
    provenance: Provenance
    timestamp_ns: int | None = None

    def __post_init__(self) -> None:
        """Validate IDs, provenance and optional monotonic timestamp."""
        if not self.parent or not self.child or self.parent == self.child:
            raise ValueError("Frame IDs must be distinct nonempty strings")
        if self.provenance not in ("measured", "estimated", "specified", "simulated"):
            raise ValueError("Unknown pose provenance")
        if self.timestamp_ns is not None and (
            type(self.timestamp_ns) is not int or self.timestamp_ns < 0
        ):
            raise ValueError("timestamp_ns must be a nonnegative integer")

    def to_dict(self) -> dict[str, object]:
        """Serialize a frame edge without losing provenance."""
        return {
            "parent": self.parent,
            "child": self.child,
            **self.transform.to_dict(),
            "provenance": self.provenance,
            "timestamp_ns": self.timestamp_ns,
        }


class FrameGraph:
    """A rooted forest of directed, single-parent rigid coordinate frames.

    Pose input is always T_parent_child; queries return T_target_source.
    An unknown frame, a disconnected tree or stale tracking is an error,
    never an identity transform fallback.
    """

    def __init__(self) -> None:
        """Initialize an empty spatial frame forest."""
        self._edges: dict[str, FrameEdge] = {}

    def set_edge(self, edge: FrameEdge) -> None:
        """Insert or replace an edge, rejecting cycles and parent changes."""
        old = self._edges.get(edge.child)
        if old is not None and old.parent != edge.parent:
            raise ValueError("A frame cannot silently change parent")
        if old is not None and old.timestamp_ns is not None and (
            edge.timestamp_ns is None or edge.timestamp_ns < old.timestamp_ns
        ):
            raise ValueError("Out-of-order frame update")
        cursor = edge.parent
        while cursor in self._edges:
            if cursor == edge.child:
                raise ValueError("Frame graph cycle")
            cursor = self._edges[cursor].parent
        if cursor == edge.child:
            raise ValueError("Frame graph cycle")
        self._edges[edge.child] = edge

    def resolve(
        self,
        target: str,
        source: str,
        *,
        now_ns: int | None = None,
        max_age_ns: int | None = None,
    ) -> RigidTransform:
        """Return T_target_source, optionally rejecting stale frame samples.

        Freshness queries require a shared monotonic clock. Specified static
        edges normally omit timestamps and are exempt from the age check.
        """
        if max_age_ns is not None and (
            now_ns is None or max_age_ns < 0 or now_ns < 0
        ):
            raise ValueError("Freshness requires nonnegative now_ns and max_age_ns")
        known = set(self._edges)
        known.update(edge.parent for edge in self._edges.values())
        if target not in known or source not in known:
            raise KeyError("Unknown coordinate frame")

        def ascend(frame: str) -> tuple[str, RigidTransform]:
            current = frame
            cumulative = RigidTransform()
            while current in self._edges:
                edge = self._edges[current]
                if max_age_ns is not None and edge.timestamp_ns is not None:
                    age = now_ns - edge.timestamp_ns  # type: ignore[operator]
                    if age < 0 or age > max_age_ns:
                        raise ValueError(f"Stale or future pose: {edge.child}")
                cumulative = edge.transform.compose(cumulative)
                current = edge.parent
            return current, cumulative

        target_root, root_from_target = ascend(target)
        source_root, root_from_source = ascend(source)
        if target_root != source_root:
            raise ValueError("Frames are not connected")
        return root_from_target.inverse().compose(root_from_source)

    def to_dict(self) -> dict[str, object]:
        """Serialize a versioned, standalone spatial-frame manifest."""
        return {
            "format": "forma.spatial-frames",
            "version": 1,
            "edges": [edge.to_dict() for edge in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> FrameGraph:
        """Read a spatial-frame manifest with explicit units and provenance."""
        if payload.get("format") != "forma.spatial-frames" or payload.get("version") != 1:
            raise ValueError("Unsupported spatial-frame manifest")
        graph = cls()
        raw_edges = payload.get("edges")
        if not isinstance(raw_edges, list):
            raise ValueError("edges must be a list")
        for record in raw_edges:
            if not isinstance(record, dict):
                raise ValueError("Each edge must be an object")
            graph.set_edge(FrameEdge(
                parent=record["parent"],
                child=record["child"],
                transform=RigidTransform(
                    translation_m=record["translation_m"],
                    rotation_xyzw=record["rotation_xyzw"],
                ),
                provenance=record["provenance"],
                timestamp_ns=record.get("timestamp_ns"),
            ))
        return graph


def openforma_mm_to_webxr_m(point_mm: Sequence[float]) -> Vector3:
    """Map OpenForma Z-up millimetres to WebXR Y-up metres: (x,z,-y)."""
    x, y, z = _vector(point_mm, 3, "point_mm")
    return (x * 0.001, z * 0.001, -y * 0.001)


def webxr_m_to_openforma_mm(point_m: Sequence[float]) -> Vector3:
    """Reverse the explicit OpenForma-to-WebXR coordinate conversion."""
    x, y, z = _vector(point_m, 3, "point_m")
    return (x * 1000.0, -z * 1000.0, y * 1000.0)


__all__ = [
    "FrameEdge",
    "FrameGraph",
    "RigidTransform",
    "openforma_mm_to_webxr_m",
    "webxr_m_to_openforma_mm",
]

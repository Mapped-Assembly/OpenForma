# Spatial coordinate frames for XR and digital-twin clients

`forma_core.spatial` provides a small, device-agnostic frame registration
contract. It does **not** claim that a headset supports 3DoF, 6DoF, SLAM, or
persistent anchors; these are capabilities of an external tracker.

## Contract

Every edge is `T_parent_child`: its translation in **metres** and unit rotation
quaternion **xyzw** transform a point from the child frame to its parent.
`FrameGraph.resolve(target, source)` returns `T_target_source`. Separate
specified, measured, estimated, and simulated provenance; do not pass simulated
ground truth off as physical measurement.

Typical graph:

```text
world (tracking reference space)
  └── table-anchor (tracked or marker-estimated)
        └── machine (specified calibration)
              └── tool (simulated or measured machine state)
```

```python
from forma_core.spatial import FrameEdge, FrameGraph, RigidTransform

frames = FrameGraph()
frames.set_edge(FrameEdge(
    "world", "table-anchor",
    RigidTransform((0.0, 0.0, -0.5)),
    provenance="measured", timestamp_ns=1_000_000_000,
))
frames.set_edge(FrameEdge(
    "table-anchor", "machine", RigidTransform((0.2, 0.0, 0.0)),
    provenance="specified",
))
pose = frames.resolve("world", "machine", now_ns=1_010_000_000,
                      max_age_ns=50_000_000)
manifest = frames.to_dict()  # versioned JSON-compatible frame graph
```

Timestamps are **monotonic nanoseconds within the pose provider's clock domain**.
Convert/align clock domains before comparing age. Unavailable, stale, future or
disconnected samples fail explicitly; missing edges never become identity.

## OpenForma and WebXR conventions

The existing OpenForma Control Studio scene is **millimetres, Z-up**; WebXR
is **metres, Y-up**. Convert positions explicitly:

```python
from forma_core.spatial import openforma_mm_to_webxr_m
assert openforma_mm_to_webxr_m((100, 200, 300)) == (0.1, 0.3, -0.2)
```

When rendering, apply exactly one axis conversion + scale, then an XR
anchor/world pose, not an additional conversion to each child body. This module
does not convert orientations by itself; use frame-graph composition for rigid
poses and a consistent render convention at the client boundary.

## Required XR adapter inputs

- A pose provider (WebXR, native headset tracker, marker tracking or calibrated
  camera/IMU fusion) that declares tracking confidence, clock domain and
  timestamp. A camera feed alone is *not* 6DoF tracking.
- A measured or specified `T_anchor_machine` registration, with calibration
  provenance. For marker tracking, ensure the marker's size and camera
  intrinsics are calibrated.
- OpenForma machine-package/viewer-scene model identity and live body poses.
  Keep machine state and control leases authoritative in Control Studio.
- A client that checks stale frames, decouples rendering from control and
  labels simulated versus physically measured telemetry.

Session-local WebXR hit testing supplies a temporary anchor; persistent physical
registration and device hardware access require additional adapters. Do not
expose this local development protocol to untrusted networks without auth.

## Validation

```bash
python -m unittest discover -s tests/spatial -v
```

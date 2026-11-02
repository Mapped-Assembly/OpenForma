# Semantic ↔ geometric bindings (OpenForma #21)

This is an **independently versioned sidecar**, not a new domain IR and not an
alternate source of CAD, electrical, fluidic or biological authority.

A binding maps a pinned semantic entity **and occurrence path** to a geometric
entity selector scoped to one exact **CAD/mesh artifact revision and SHA-256**.
One domain entity can have multiple geometric features, multiple semantic
domain views can bind to one region/body, and repeated occurrences of the same
definition remain distinct. Labels/refdes are aliases, never global IDs.

## Contract and supported fidelity

- `BindingManifest` contains version, product, pinned `GeometryArtifact`,
  `GeometryEntity` catalog, `Binding` associations, and bounded requirements.
- `SemanticRef` pins domain artifact ID, domain, revision, SHA-256, entity ID,
  optional feature ID, and physical occurrence path.
- `GeometryEntity` selects a body, face, port, region, datum etc., but only
  within the exact declared artifact revision. A named feature selector can
  match **zero** (unresolved), **one** (candidate), or **several** (ambiguous).
  Verified CAD/mesh entities must come from a real source adapter. Merely
  supplying a descriptive name does not prove geometry exists.
- `Binding` separately records role, origin (supplied/measured/specification/
  authored/estimate/heuristic), fidelity (envelope/simplified/detailed/unknown),
  resolution, required/optional status and assumptions. A mesh envelope is
  **not** an exact mating face, connector pin or fluid-solid boundary.
- `Resolution` distinguishes resolved, unresolved, ambiguous, stale,
  unsupported and not-applicable. Required unresolved or stale geometry
  produces an error only in **binding validation**, not in the owning domain IR.
- A `GeometryRequirement` is authored intent; `RequirementResult` is an
  evidence-bound check. The v1 checker supports `coincident` planes,
  `aligned` normals, and `axis_offset` using explicit anchors and normals.
  `envelope_clearance` returns **unsupported**, never a false positive for
  exact solid collision/clearance.
- Entity points are artifact-local; active right-handed Euler rotations use
  **Rx then Ry then Rz**, and each local-to-parent pose is applied once.
  Artifact units are mm or m and explicitly normalized. No implicit scene/world
  transform or double application is performed.

## Public CLI / MCP / SDK

From a checkout with dependencies:

```bash
python -m forma_core domains bindings examples/bindings/validate-request.json
python -m pytest tests/projects/test_geometry_bindings.py -q
```

For the existing `forma.opencode.domains` MCP operation, call
`operation: "bindings"` with the JSON-string payload:
- `{"action": "schema"}` for the versioned contract.
- `{"action": "validate", "manifest": ...}` for resolution and bounded checks.
- `{"action": "forward", "manifest": ..., "semantic": {"domain": "...", "artifact_id": "...", "entity_id": "...", "occurrence_path": [...]}}` for semantic→geometric lookup.
- `{"action": "reverse", "manifest": ..., "geometric_entity_id": "..."}` for geometry→semantic lookup.
- `{"action": "invalidate", "manifest": ..., "geometry": {"revision": 2, "sha256": "<64 hex chars>"}}` to retire all stale geometry-bound selections after a revision.
- `{"action": "legacy", "input": ...}` to adapt existing placements into
  explicitly **unverified envelopes**, not fabricated exact STEP body/face IDs.

These operations are stateless. Lifecycle/fingerprints of domain products and
OpenCAD artifacts remain authoritative; no second persistent scene store is
created.

## Synthetic acceptance fixtures and limits

- `examples/bindings/rotated-component.json`: illustrated 40×20×10 mm
  part, +90° Z rotation and (100,50,0) mm translation. A local datum at
  (+20,0,0) mm becomes (100,70,0) mm; assembly AABB is 20×40×10 mm. A
  second occurrence reuses the definition but retains a distinct path.
- `examples/bindings/connector-panel.json`: named connector/panel faces
  with normals, a quantitative flush/normal check and an unresolved
  envelope-only pin.
- `examples/bindings/chamber-host.json`: biological and fluidic semantic
  entities refer to a common fluid **region**, not the enclosing solid.
  This does not validate biological transport behavior.

**Synthetic fixtures do not constitute an actual OpenCAD feature-tree
round-trip or a real UI selection proof.** The current legacy adapter records
unverified envelope geometry; it never upgrades inferred placement geometry
to exact body/face claims. A BRep adapter must inspect the actual exported
feature/shape catalog, preserve lineage where supported and mark split/merged
faces ambiguous. Downstream OpenIndustries scene import and selectable UI
proof remain separate work under milestones B/C of #21.

## Generic CAD placement correction

The **legacy** generic CAD generator previously omitted
`MechanicalPlacement.orientation_deg` when producing component envelopes.
It now emits the requested rotation and uses an explicitly rotated XY sketch
for supported Z rotations, so the generated *solid* has the rotated footprint.
Unsupported X/Y rotations fail explicitly instead of being silently treated as
unrotated boxes. The existing label-derived display/USB cutout behavior remains
a **legacy heuristic**, not a new typed feature request. This change does not
claim that label matching creates verified connector features.

## Next integration gates before closing #21

1. Real OpenCAD BRep/STEP artifact catalog and revision-pinned feature IDs,
   including a deterministic rotated asymmetric real-export test and
   confidence/unsupported reporting after topology changes.
2. Explicit typed feature requests replacing label heuristics **only in a new
   opt-in path** without breaking older hardware projects.
3. Wiring same binding sidecar through actual supported CAD export/reimport,
   scene instance import, and 2D/3D selection in the UI.
4. Resolve inherited geometric validation obligations using only supported
   measurement APIs, and keep invalidation/revision receipts with existing
   lifecycle state.

Passing the bounded validator does **not** certify fabrication,
dimension tolerances, mating safety, geometric clearance, or scientific
validity of electrical, fluidic or biological designs.

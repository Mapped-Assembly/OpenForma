"""Issue #21: pinned geometry bindings, spatial checks and public inspection."""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from pydantic import ValidationError

from forma_core.domains.tools import call_domain_tool
from forma_core.geometry_bindings.models import BindingManifest, GeometryArtifact, Pose
from forma_core.geometry_bindings.resolution import (
    apply_pose, axis_bounds, composed_pose, evaluate_requirement, forward_lookup,
    invalidate_geometry, reverse_lookup, validate_manifest,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/bindings"


def load(name: str) -> BindingManifest:
    """Read only the checked-in illustrative geometry fixtures."""
    return BindingManifest.model_validate_json((EXAMPLES / name).read_text(encoding="utf-8"))


class BindingContractTests(unittest.TestCase):
    """Semantic identities, occurrence transforms and geometric findings."""

    def test_90_degree_rotation_and_repeated_occurrences(self) -> None:
        """The named feature maps to 100,70,0 and bounds are 20,40,10."""
        manifest = load("rotated-component.json")
        report = validate_manifest(manifest)
        self.assertTrue(report.is_valid, report.model_dump())
        target = next(b for b in report.bindings if b.binding_id == "occurrence-1-datum")
        self.assertIsNotNone(target.anchor_mm)
        for actual, expected in zip(target.anchor_mm, (100, 70, 0)):
            self.assertAlmostEqual(expected, actual, places=7)
        body = next(b for b in report.bindings if b.binding_id == "occurrence-1-body")
        for actual, expected in zip(body.bounds_mm, (20, 40, 10)):
            self.assertAlmostEqual(expected, actual, places=7)
        self.assertEqual("pass", report.requirements[0].status)
        refs = forward_lookup(manifest, domain="mechanical", artifact_id="product.mechanical",
                              entity_id="M1")
        self.assertEqual(3, len(refs))
        scoped = forward_lookup(manifest, domain="mechanical", artifact_id="product.mechanical",
                                entity_id="M1", occurrence_path=("assembly", "M2"))
        self.assertEqual(["occurrence-2-body"], [item.binding_id for item in scoped])
        self.assertEqual(2, len(reverse_lookup(manifest, geometric_entity_id="body")))

    def test_right_handed_rotation_order_and_one_time_composition(self) -> None:
        """Rx->Ry->Rz produces different output from a second applied placement."""
        pose = Pose(translation_mm=(2, 1, 3), euler_xyz_deg=(90, 0, 90))
        point = apply_pose((0, 1, 0), pose)
        for actual, expected in zip(point, (2, 1, 4)):
            self.assertAlmostEqual(expected, actual, places=7)
        first = Pose(translation_mm=(10, 0, 0), euler_xyz_deg=(0, 0, 90))
        second = Pose(translation_mm=(0, 5, 0), euler_xyz_deg=(90, 0, 0))
        result = composed_pose((1, 0, 0), first, second)
        expected = apply_pose(apply_pose((1, 0, 0), first), second)
        self.assertEqual(expected, result)
        self.assertNotEqual(result, apply_pose(result, second))
        self.assertEqual((20, 40, 10), tuple(round(x) for x in axis_bounds(
            (40, 20, 10), Pose(euler_xyz_deg=(0, 0, 90)))))

    def test_label_rename_preserves_identity_and_geometry(self) -> None:
        """Names are aliases, never geometry-target keys."""
        manifest = load("rotated-component.json")
        before = validate_manifest(manifest)
        manifest.bindings[0].semantic.label = "A completely new display label"
        after = validate_manifest(manifest)
        self.assertEqual(before.bindings[0].anchor_mm, after.bindings[0].anchor_mm)
        self.assertEqual(before.bindings[0].target_ids, after.bindings[0].target_ids)

    def test_panel_mating_faces_are_explicit(self) -> None:
        """A well-declared coincident planar mating check passes quantitatively."""
        manifest = load("connector-panel.json")
        report = validate_manifest(manifest)
        self.assertTrue(report.is_valid)
        self.assertEqual(["pass", "pass"], [r.status for r in report.requirements])
        self.assertEqual("unsupported", next(b.status for b in report.bindings
                         if b.binding_id == "approximate-pin"))
        displaced = manifest.model_copy(deep=True)
        next(b for b in displaced.bindings if b.binding_id == "connector").occurrence_pose.translation_mm = (0, 0, 2)
        report = validate_manifest(displaced)
        self.assertEqual("fail", report.requirements[0].status)
        self.assertFalse(report.is_valid)
        rotated = manifest.model_copy(deep=True)
        next(b for b in rotated.bindings if b.binding_id == "connector").occurrence_pose.euler_xyz_deg = (0, 25, 0)
        report = validate_manifest(rotated)
        self.assertEqual("fail", report.requirements[1].status)

    def test_biological_fluidic_region_is_not_a_solid_body(self) -> None:
        """One actual region may have multiple independently owned domain views."""
        manifest = load("chamber-host.json")
        report = validate_manifest(manifest)
        self.assertTrue(report.is_valid)
        self.assertEqual(2, len(reverse_lookup(manifest, geometric_entity_id="fluid-volume")))
        self.assertEqual([], reverse_lookup(manifest, geometric_entity_id="solid-housing"))
        self.assertEqual({"biological", "fluidic"}, {
            b["semantic"]["domain"] for b in reverse_lookup(
                manifest, geometric_entity_id="fluid-volume")
        })

    def test_revision_change_makes_old_selector_stale(self) -> None:
        """CAD revision must not silently bind the next face at the old index."""
        manifest = load("rotated-component.json")
        changed = invalidate_geometry(manifest, new_revision=2, new_sha256="c" * 64)
        self.assertEqual([], changed.entities)
        self.assertEqual("stale", changed.bindings[0].resolution)
        self.assertNotEqual(manifest.geometry.sha256, changed.geometry.sha256)
        self.assertFalse(validate_manifest(changed).is_valid)
        self.assertEqual("resolved", manifest.bindings[0].resolution)

    def test_missing_and_ambiguous_selectors(self) -> None:
        """A missing named feature is unresolved and two matches are ambiguous."""
        manifest = load("rotated-component.json")
        missing = manifest.model_copy(deep=True)
        missing.entities = [e for e in missing.entities if e.selector != "datum/east"]
        self.assertIn("geometry_unresolved", [f.code for f in validate_manifest(missing).findings])
        ambiguous = manifest.model_copy(deep=True)
        duplicate = ambiguous.entities[-1].model_copy(update={
            "entity_id": "another-datum", "selector": "datum/east"
        })
        ambiguous.entities.append(duplicate)
        self.assertIn("geometry_ambiguous", [f.code for f in validate_manifest(ambiguous).findings])

    def test_real_artifact_without_verified_target_fails_honestly(self) -> None:
        """Source names and runtime IDs alone do not prove a CAD feature exists."""
        manifest = load("rotated-component.json")
        manifest.geometry.format = "step"
        manifest.entities[0].verified_from_artifact = False
        report = validate_manifest(manifest)
        self.assertIn("geometry_unsupported", [f.code for f in report.findings])
        self.assertFalse(report.is_valid)

    def test_constraints_never_claim_solid_clearance_from_aabb(self) -> None:
        """Approximate bounding boxes cannot prove true solid separation."""
        manifest = load("rotated-component.json")
        requirement = manifest.requirements[0].model_copy(update={"kind": "envelope_clearance"})
        result = evaluate_requirement(manifest, requirement)
        self.assertEqual("unsupported", result.status)

    def test_strict_schema_and_portable_round_trip(self) -> None:
        """Binding sidecars are independently serialized and reject malformed data."""
        manifest = load("rotated-component.json")
        self.assertEqual(manifest, BindingManifest.model_validate_json(manifest.model_dump_json()))
        bad = manifest.model_dump(mode="json")
        bad["schema_version"] = "8.0"
        with self.assertRaises(ValidationError):
            BindingManifest.model_validate(bad)
        bad = manifest.model_dump(mode="json")
        bad["geometry"]["sha256"] = "sha-1"
        with self.assertRaises(ValidationError):
            BindingManifest.model_validate(bad)

    def test_legacy_placements_are_explicitly_unverified(self) -> None:
        """Old MechanicalPlacement data provides envelopes, not exact STEP faces."""
        payload = {
            "action": "legacy",
            "input": {
                "product_id": "legacy",
                "geometry": load("rotated-component.json").geometry.model_dump(mode="json"),
                "semantic_artifact_id": "legacy.mechanical",
                "semantic_revision": 3,
                "semantic_sha256": "e" * 64,
                "placements": [{
                    "ref_des": "U1", "label": "USB connector",
                    "position": {"x_mm": 100, "y_mm": 50, "z_mm": 0},
                    "size": {"x_mm": 40, "y_mm": 20, "z_mm": 10},
                    "orientation_deg": {"x_deg": 0, "y_deg": 0, "z_deg": 90},
                }],
                "object_ids": {"U1": "stable-connector"},
            },
        }
        result = call_domain_tool("bindings", payload)
        binding = result["bindings"][0]
        self.assertEqual("stable-connector", binding["semantic"]["entity_id"])
        self.assertEqual("unsupported", binding["resolution"])
        self.assertEqual(90, binding["occurrence_pose"]["euler_xyz_deg"][2])
        self.assertNotIn("pin", binding["role"])
        self.assertEqual("unsupported", validate_manifest(BindingManifest.model_validate(result)).bindings[0].status)

    def test_cli_and_mcp_share_same_binding_schema(self) -> None:
        """The public CLI and restricted MCP argument schema expose bindings."""
        from forma_core.opencode.models import DomainToolArguments
        from apps.api.opencode_mcp import opencode_mcp_tools

        manifest = load("rotated-component.json").model_dump(mode="json")
        request = {"action": "validate", "manifest": manifest}
        python_report = call_domain_tool("bindings", request)
        script = subprocess.run([sys.executable, "-m", "forma_core", "domains", "bindings", "-"],
                                input=json.dumps(request), capture_output=True, text=True, check=False)
        self.assertEqual(0, script.returncode, script.stderr)
        self.assertEqual(python_report, json.loads(script.stdout))
        self.assertEqual("bindings", DomainToolArguments(
            operation="bindings", payload=json.dumps(request)).operation)
        self.assertIn("forma.opencode.domains", [tool["name"] for tool in opencode_mcp_tools()])

    @unittest.skipUnless(__import__("os").environ.get("FORMA_CAD_RUN_INTEGRATION_TESTS") == "true",
                         "Native OpenCAD/OCCT integration gate")
    def test_real_opencad_rotated_asymmetric_body_exports_step_and_stl(self) -> None:
        """Prove a real OCCT-built/exported component obeys the same Z pose."""
        from tempfile import TemporaryDirectory
        from forma_core.mechanics.primitives import (
            MechanicalNotes, MechanicalPlacement, MechanicalVector3, MechanicalRotation3,
        )
        from forma_core.workspaces.projects.cad_generation import (
            _adapter_path, _cad_source, _run_adapter, _stl_mesh,
        )
        from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

        project = HardwareIntermediateRepresentation(mechanical=MechanicalNotes(
            enclosure_type="Open frame", mounting_guidance="Test", manufacturability_rating="Easy",
            component_placements=[MechanicalPlacement(
                ref_des="C1", position=MechanicalVector3(x_mm=100, y_mm=50, z_mm=0),
                size=MechanicalVector3(x_mm=40, y_mm=20, z_mm=10),
                orientation_deg=MechanicalRotation3(z_deg=90),
            )],
        ))
        full_source = _cad_source(project)
        func = next(node for node in ast.parse(full_source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "oriented_xy_prism")
        function_source = ast.get_source_segment(full_source, func)
        self.assertIsNotNone(function_source)
        snippet = (
            "import math\\nfrom opencad import Part, Sketch\\n\\n"
            + function_source +
            "\\n\\nmodel = oriented_xy_prism(100,50,0,40,20,10,90,'fixture')\\nmodel\\n"
        )
        with TemporaryDirectory() as root:
            source = Path(root) / "rotated.py"
            step = Path(root) / "rotated.step"
            stl = Path(root) / "rotated.stl"
            source.write_text(snippet, encoding="utf-8")
            self.assertTrue(_run_adapter(_adapter_path(), source, step)["valid"])
            self.assertTrue(step.is_file() and step.stat().st_size > 200)
            self.assertTrue(_run_adapter(_adapter_path(), source, stl)["valid"])
            mesh = _stl_mesh(stl)
            coords = mesh["vertices"]
            bounds = [(min(coords[i::3]), max(coords[i::3])) for i in range(3)]
            for (minimum, maximum), expected in zip(
                bounds, ((90, 110), (30, 70), (-5, 5))
            ):
                self.assertAlmostEqual(expected[0], minimum, delta=0.5)
                self.assertAlmostEqual(expected[1], maximum, delta=0.5)

    def test_cad_template_includes_rotation_not_just_preview(self) -> None:
        """Generic OpenCAD source rotates actual Sketch vertices, not just metadata."""
        from forma_core.mechanics.primitives import (
            MechanicalNotes, MechanicalPlacement, MechanicalVector3, MechanicalRotation3,
        )
        from forma_core.workspaces.projects.cad_generation import _cad_source, _placement_payload, CadGenerationError
        from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

        project = HardwareIntermediateRepresentation(mechanical=MechanicalNotes(
            enclosure_type="Open frame", mounting_guidance="Test", manufacturability_rating="Easy",
            component_placements=[MechanicalPlacement(
                ref_des="U1", position=MechanicalVector3(x_mm=100, y_mm=50, z_mm=0),
                size=MechanicalVector3(x_mm=40, y_mm=20, z_mm=10),
                orientation_deg=MechanicalRotation3(z_deg=90),
            )],
        ))
        records = _placement_payload(project)
        self.assertEqual(90, records[0]["rz_deg"])
        source = _cad_source(project)
        ast.parse(source)
        self.assertIn("def oriented_xy_prism", source)
        self.assertIn("component = oriented_xy_prism", source)
        self.assertIn('"rz_deg": 90.0', source)
        project.mechanical.component_placements[0].orientation_deg.x_deg = 30
        with self.assertRaises(CadGenerationError):
            _cad_source(project)


if __name__ == "__main__":
    unittest.main()

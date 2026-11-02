"""Offline acceptance tests for externally owned IRs; no OpenIndustries dependency.

The synthetic wire format below is test data, not a proposed SpaceIR schema.
Its deliberately different field names expose accidental envelope assumptions.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from forma_core.domains.base import Finding, fingerprint
from forma_core.domains.composition import DomainLink, LinkEndpoint, ProductBundle, compose_product, validate_product
from forma_core.domains.compatibility import export_hardware_ir, import_hardware_ir
from forma_core.domains.generation import DeclaredInput, fixture_author, generate_product
from forma_core.domains.planning import ProductPlan, ProductScope, select_domains
from forma_core.domains.providers import (
    ArtifactIdentity, ArtifactReference, ContextRequirement, DomainDependency,
    DomainRegistry, ProviderMetadata, SchemaSupport,
)
from forma_core.domains.registry import create_registry, domain_model, parse_domain, validate_domain
from forma_core.domains.storage import load_product, save_product
from forma_core.domains.tools import call_domain_tool
from tests.projects.test_domain_irs import fixture_input, legacy_motor


class SyntheticProvider:
    """An external owner with independent JSON, validation and artifact resolution."""

    def __init__(self, domain: str = "space") -> None:
        """Advertise a test-only contract and create one independently stored artifact."""
        self.metadata = ProviderMetadata(domain=domain, ir_type="SyntheticIR", owner="External test owner",
            provider_id=f"tests.{domain}", provider_version="2.1",
            schemas=[SchemaSupport(schema_id=f"test.example/{domain}", versions=["0.4"])],
            validation=True, resolution=True, discovery=True,
            required_context=[ContextRequirement(name="site_id", description="Existing site identity.")],
            reference_types=["spatial", "same_object"], fact_authorities=["regions"],
            limitations=["Test fixture only; no spatial or physics implementation."])
        self.payload: dict[str, Any] = {"key": f"external:{domain}:lab", "edit": "rev-7",
            "contract": f"test.example/{domain}", "edition": "0.4", "members": ["room-A"]}

    def reference(self) -> ArtifactReference:
        """Return a version-pinned reference with cross-repository provenance."""
        return ArtifactReference(artifact_id=self.payload["key"], domain=self.metadata.domain,
            schema_id=self.payload["contract"], schema_version=self.payload["edition"], revision=self.payload["edit"],
            provider_id=self.metadata.provider_id, provider_version=self.metadata.provider_version,
            sha256=fingerprint(self.payload), locator="test-artifact://lab/rev-7",
            provenance={"repository": "https://example.invalid/owner/spaces", "commit": "fixture-commit",
                        "path": "artifacts/lab.json", "evidence": ["survey:fixture"]})

    def validate_context(self, context: dict[str, Any]) -> list[str]:
        """Validate this owner's context without any native OpenForma model."""
        return [] if isinstance(context.get("site_id"), str) and context["site_id"] else ["site_id: required"]

    def validate(self, payload: dict[str, Any], reference: ArtifactReference) -> list[Finding]:
        """Validate only fixture-local semantics, independently of composition."""
        if set(payload) != {"key", "edit", "contract", "edition", "members"} or not payload.get("members"):
            return [Finding(code="synthetic_schema", location="members", message="Expected independent fixture fields and at least one member.")]
        if payload["edition"] != "0.4":
            return [Finding(code="synthetic_version", location="edition", message="Unsupported fixture edition.")]
        return []

    def describe(self, payload: dict[str, Any], reference: ArtifactReference) -> ArtifactIdentity:
        """Map the owner's identity fields into reference metadata, not another IR."""
        return ArtifactIdentity(artifact_id=payload["key"], domain=self.metadata.domain,
            schema_id=payload["contract"], schema_version=payload["edition"], revision=payload["edit"])

    def entities(self, payload: dict[str, Any], reference: ArtifactReference) -> dict[str, set[str]]:
        """Expose stable entity IDs for composition without exposing domain semantics."""
        return {key: set() for key in payload["members"]}

    def resolve(self, reference: ArtifactReference) -> dict[str, Any] | None:
        """Load independent bytes; this fixture uses a dictionary instead of a service."""
        return deepcopy(self.payload) if reference.artifact_id == self.payload["key"] else None

    def discover(self, query: dict[str, Any]) -> list[ArtifactReference]:
        """Return this fixture's independently owned artifact."""
        return [self.reference()]

    def author(self, request: dict[str, Any]) -> ArtifactReference:
        """Authoring is deliberately not advertised by the read-only test provider."""
        raise NotImplementedError("Read-only fixture")


def external_registry() -> tuple[DomainRegistry, SyntheticProvider]:
    """Register a mock in an isolated registry, replacing the unavailable reservation."""
    registry = create_registry()
    provider = SyntheticProvider()
    registry.register(provider.metadata, provider, replace=True)
    return registry, provider


def mixed_bundle(provider: SyntheticProvider) -> ProductBundle:
    """Compose a valid native mechanical document with one external spatial reference."""
    source = fixture_input()["designs"]["mechanical"]
    mechanical = parse_domain({**source, "domain": "mechanical", "schema_version": "1.0",
                               "artifact_id": "product.mechanical", "revision": 1})
    reference = provider.reference()
    link = DomainLink(link_id="placement", kind="spatial",
        source=LinkEndpoint(artifact_id=mechanical.artifact_id, revision=1, object_id="M1", units="mm", frame="lab"),
        target=LinkEndpoint(artifact_id=reference.artifact_id, revision=reference.revision, object_id="room-A",
                            sha256=reference.sha256, units="mm", frame="lab"),
        fact_owners={"geometry": "mechanical", "regions": "space"}, provenance={"authored_by": "test"})
    return compose_product("product", [mechanical], references=[reference], links=[link])


class ExternalProviderTests(unittest.TestCase):
    """Selection, identity, independent serialization and availability boundaries."""

    def test_default_space_is_reserved_without_any_schema(self) -> None:
        """OpenForma advertises ownership and absence instead of inventing SpaceIR."""
        info = call_domain_tool("registry", {})["domains"]["space"]
        self.assertEqual("OpenIndustries", info["owner"])
        self.assertEqual("unavailable", info["availability"])
        self.assertEqual([], info["schemas"])
        self.assertIsNone(call_domain_tool("schema", {"domain": "space"})["document"])
        script = ("import sys; from forma_core.domains.tools import call_domain_tool; "
                  "p=call_domain_tool('plan', {'product_id':'p','brief':'Map a site','scope':{'domains':['space']}}); "
                  "assert p['tasks'][0]['domain']=='space'; "
                  "assert not any('openindustries' in x.lower() or 'llm_providers' in x for x in sys.modules); "
                  "assert 'forma_core.mechanics.models' not in sys.modules")
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)

    def test_registration_and_selection_are_registry_driven(self) -> None:
        """Native + external domains select together; an arbitrary fifth domain also works."""
        registry, provider = external_registry()
        plan = select_domains("p", "Mount a motor in a site", registry=registry,
            scope=ProductScope(physical_structure=True, domains=["space"]), contexts={"space": {"site_id": "lab"}})
        self.assertEqual(["mechanical", "space"], [t.domain for t in plan.tasks])
        self.assertEqual("ready", plan.tasks[1].status)
        self.assertEqual(provider.metadata, plan.tasks[1].provider)
        self.assertEqual("site_id", plan.tasks[1].required_context[0].name)
        extra = SyntheticProvider("synthetic_heat")
        registry.register(extra.metadata, extra)
        selected = select_domains("p", "Thermal analysis", registry=registry,
                                  scope=ProductScope(domains=["synthetic_heat"]))
        self.assertEqual(["synthetic_heat"], [t.domain for t in selected.tasks])
        self.assertEqual(["site_id: required"], selected.tasks[0].missing_inputs)

    def test_unavailable_required_domain_is_not_silently_removed(self) -> None:
        """Selection and generation retain an unresolved SpaceIR task without a placeholder."""
        plan = select_domains("p", "Spatial layout", scope=ProductScope(domains=["space"]))
        self.assertEqual("blocked", plan.tasks[0].status)
        self.assertTrue(plan.tasks[0].unresolved_dependencies)
        bundle = generate_product(plan, fixture_author({}))
        self.assertEqual({}, bundle.artifacts)
        self.assertEqual([], bundle.manifest.artifacts)
        self.assertIn("space", validate_product(bundle).blocked_domains)

    def test_declared_dependency_is_selected_and_unavailability_reported(self) -> None:
        """Dependencies come from provider declarations rather than native domain switches."""
        registry, _ = external_registry()
        extra = SyntheticProvider("synthetic_heat")
        extra.metadata.dependencies = [DomainDependency(domain="space", relationship="located_in")]
        registry.register(extra.metadata, extra)
        plan = select_domains("p", "Analyze a site", scope=ProductScope(domains=["synthetic_heat"]), registry=registry)
        self.assertEqual({"space", "synthetic_heat"}, {t.domain for t in plan.tasks})
        absent = registry.metadata("space").model_copy(update={"availability": "unavailable"})
        registry.register(absent, replace=True)
        plan = select_domains("p", "Analyze a site", scope=ProductScope(domains=["synthetic_heat"]), registry=registry)
        thermal = next(t for t in plan.tasks if t.domain == "synthetic_heat")
        self.assertTrue(thermal.unresolved_dependencies)
        self.assertEqual("located_in", thermal.dependencies[0].relationship)

    def test_registration_rejects_duplicates_and_unbound_available_advertisements(self) -> None:
        """Configuration cannot silently switch providers or claim an absent implementation."""
        registry, provider = external_registry()
        with self.assertRaisesRegex(ValueError, "already has"):
            registry.register(provider.metadata, provider)
        with self.assertRaisesRegex(ValueError, "require an implementation"):
            DomainRegistry().register(provider.metadata)
        snapshot = registry.metadata("space")
        snapshot.availability = "unavailable"
        self.assertIs(provider, registry.provider("space"))

    def test_native_context_metadata_matches_owning_schema(self) -> None:
        """Lazy discovery descriptions stay aligned with all four existing context contracts."""
        for domain, info in create_registry().catalog().items():
            if domain == "space":
                continue
            required = domain_model(domain, context=True).model_json_schema()["required"]
            self.assertEqual(set(required), {r["name"] for r in info["required_context"] if r["required"]})

    def test_external_handoff_pins_registry_and_rejects_changed_capabilities(self) -> None:
        """An agent can select SpaceIR without loading it; stale registry decisions reject."""
        registry, provider = external_registry()
        request = call_domain_tool("plan", {"product_id": "p", "brief": "Map the lab"}, registry=registry)
        submission = {**request["project"], "decision": {"request_sha256": request["request_sha256"],
            "registry_sha256": request["registry_sha256"], "scope": ProductScope(domains=["space"]).model_dump(),
            "reasons": {"space": "The requested work is the lab's spatial representation."}}}
        result = call_domain_tool("plan", submission, registry=registry)
        self.assertEqual(["space"], [t["domain"] for t in result["tasks"]])
        provider.metadata.provider_version = "2.2"
        registry.register(provider.metadata, provider, replace=True)
        with self.assertRaisesRegex(ValueError, "registry changed"):
            call_domain_tool("plan", submission, registry=registry)

    def test_serialization_validation_and_reference_only_composition(self) -> None:
        """Independent JSON needs no native envelope; the manifest retains exact provenance."""
        registry, provider = external_registry()
        standalone = json.loads(json.dumps(provider.payload))
        self.assertNotIn("domain", standalone)
        result = call_domain_tool("validate-domain", {"reference": provider.reference().model_dump(),
                                   "artifact": standalone}, registry=registry)
        self.assertTrue(result["is_valid"])
        bundle = mixed_bundle(provider)
        original = fingerprint(bundle)
        report = validate_product(bundle, registry=registry)
        self.assertTrue(report.is_valid, report)
        self.assertEqual([], report.local[provider.reference().artifact_id])
        self.assertNotIn(provider.reference().artifact_id, bundle.artifacts)
        self.assertEqual(original, fingerprint(bundle))
        self.assertEqual(bundle, ProductBundle.model_validate_json(bundle.model_dump_json()))
        self.assertEqual(provider.reference(), bundle.manifest.artifacts[1])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product.json"
            save_product(bundle, path)
            self.assertEqual(bundle, load_product(path))

    def test_broken_links_never_change_local_artifact_validity(self) -> None:
        """Entity/revision/hash/frame errors stay composition findings and preserve source bytes."""
        registry, provider = external_registry()
        for field, value, expected in [("object_id", "missing", "link_object"), ("revision", "rev-8", "link_revision"),
                                        ("sha256", "0" * 64, "link_hash"), ("frame", "other", "spatial_transform")]:
            bundle = mixed_bundle(provider)
            original = deepcopy(bundle.artifacts)
            setattr(bundle.manifest.links[0].target, field, value)
            report = validate_product(bundle, registry=registry)
            self.assertIn(expected, {f.code for f in report.composition})
            self.assertTrue(all(not f for f in report.local.values()))
            self.assertEqual(original, bundle.artifacts)

    def test_provider_outage_is_composition_only_and_does_not_corrupt_cached_ir(self) -> None:
        """Unknown validation state differs from a locally invalid external artifact."""
        registry, provider = external_registry()
        bundle = mixed_bundle(provider)
        bundle.artifacts[provider.reference().artifact_id] = deepcopy(provider.payload)
        before = fingerprint(bundle)
        registry.register(provider.metadata.model_copy(update={"availability": "unavailable"}), replace=True)
        result = validate_product(bundle, registry=registry)
        self.assertEqual([], result.local["product.mechanical"])
        self.assertNotIn(provider.reference().artifact_id, result.local)
        self.assertIn("provider_unavailable", {f.code for f in result.composition})
        self.assertEqual(before, fingerprint(bundle))
        standalone = call_domain_tool("validate-domain", {"reference": provider.reference().model_dump(),
            "artifact": provider.payload}, registry=registry)
        self.assertIsNone(standalone["is_valid"])
        self.assertEqual("unresolved", standalone["status"])

    def test_missing_artifact_and_provider_exceptions_are_explicit(self) -> None:
        """A service outage never bubbles out or turns a valid mechanical IR invalid."""
        registry, provider = external_registry()
        for response, code in [(None, "missing_artifact"), (RuntimeError("offline"), "provider_resolution")]:
            with patch.object(provider, "resolve", **({"side_effect": response} if isinstance(response, Exception) else {"return_value": response})):
                report = validate_product(mixed_bundle(provider), registry=registry)
                self.assertEqual([], report.local["product.mechanical"])
                self.assertIn(code, {f.code for f in report.composition})
        with patch.object(provider, "entities", side_effect=RuntimeError("index offline")):
            report = validate_product(mixed_bundle(provider), registry=registry)
            self.assertIn("provider_validation", {f.code for f in report.composition})

    def test_reference_identity_provider_version_schema_and_digest_are_checked(self) -> None:
        """Cross-repository references cannot silently drift to another version or provider."""
        registry, provider = external_registry()
        for field, value, code in [("provider_id", "other", "provider_version"), ("provider_version", "3", "provider_version"),
                                   ("schema_id", "other/schema", "provider_schema"), ("schema_version", "99", "provider_schema"),
                                   ("sha256", "0" * 64, "artifact_hash"), ("revision", "changed", "artifact_metadata")]:
            bundle = mixed_bundle(provider)
            setattr(bundle.manifest.artifacts[1], field, value)
            report = validate_product(bundle, registry=registry)
            self.assertIn(code, {f.code for f in report.composition})
            self.assertEqual([], report.local["product.mechanical"])
        value = provider.reference().model_dump()
        del value["provider_id"]
        with self.assertRaises(ValidationError):
            ArtifactReference.model_validate(value)

    def test_invalid_external_document_remains_local_to_its_domain(self) -> None:
        """Broken external semantics do not contaminate the native domain report."""
        registry, provider = external_registry()
        provider.payload["members"] = []
        bundle = mixed_bundle(provider)
        report = validate_product(bundle, registry=registry)
        self.assertEqual([], report.local["product.mechanical"])
        self.assertEqual("synthetic_schema", report.local[provider.reference().artifact_id][0].code)

    def test_content_addressed_external_artifact_needs_no_invented_revision(self) -> None:
        """A digest can pin an owner's artifact when its schema has no revision counter."""
        registry, provider = external_registry()
        provider.payload["edit"] = None
        bundle = mixed_bundle(provider)
        self.assertIsNone(bundle.manifest.artifacts[1].revision)
        self.assertTrue(validate_product(bundle, registry=registry).is_valid)
        with self.assertRaises(ValidationError):
            LinkEndpoint(artifact_id="unversioned", object_id="room")

    def test_declared_native_input_checks_new_endpoint_fingerprint(self) -> None:
        """Digest pins cannot be silently ignored by the existing generation adapter."""
        fixture = fixture_input()
        plan = ProductPlan.model_validate(fixture["plan"])
        baseline = generate_product(plan, fixture_author(fixture["designs"]))
        source = baseline.artifacts["motor-example.mechanical"]
        dependency = DeclaredInput(target_domain="electrical", field="parts", source=LinkEndpoint(
            artifact_id=source["artifact_id"], object_id="M1", sha256=fingerprint(source)))
        accepted = generate_product(plan, fixture_author(fixture["designs"]), inputs=[dependency])
        self.assertEqual("generated", accepted.plan.tasks[1].status)
        dependency.source.sha256 = "0" * 64
        rejected = generate_product(plan, fixture_author(fixture["designs"]), inputs=[dependency])
        self.assertEqual("blocked", rejected.plan.tasks[1].status)

    def test_standalone_provider_failure_reports_unresolved_not_invalid(self) -> None:
        """Transport failures are not evidence of invalid local semantics."""
        registry, provider = external_registry()
        with patch.object(provider, "validate", side_effect=RuntimeError("offline")):
            result = call_domain_tool("validate-domain", {"reference": provider.reference().model_dump(),
                                      "artifact": provider.payload}, registry=registry)
        self.assertIsNone(result["is_valid"])
        self.assertEqual("provider_validation", result["dependencies"][0]["code"])

    def test_native_generation_with_external_reference_and_update_preserves_identity(self) -> None:
        """The existing author handles native work; external documents remain with their provider."""
        registry, provider = external_registry()
        fixture = fixture_input()
        plan = select_domains("p", "Mount a motor in a lab", registry=registry,
            scope=ProductScope(physical_structure=True, domains=["space"]),
            contexts={"mechanical": fixture["plan"]["tasks"][0]["context"], "space": {"site_id": "lab"}})
        bundle = generate_product(plan, fixture_author(fixture["designs"]), references=[provider.reference()], registry=registry)
        self.assertTrue(validate_product(bundle, registry=registry).is_valid)
        updated = generate_product(bundle.plan, fixture_author(fixture["designs"]), existing=bundle,
                                   update_domains={"mechanical"}, registry=registry)
        self.assertEqual(provider.reference(), next(r for r in updated.manifest.artifacts if r.domain == "space"))
        self.assertEqual(2, updated.artifacts["p.mechanical"]["revision"])
        self.assertNotIn(provider.reference().artifact_id, updated.artifacts)

    def test_external_reference_cannot_bypass_unchanged_legacy_export_loss_reporting(self) -> None:
        """Adding an external IR to an unchanged import cannot silently discard it."""
        registry, provider = external_registry()
        bundle = import_hardware_ir(legacy_motor(), product_id="legacy")
        bundle.manifest.artifacts.append(provider.reference())
        before = fingerprint(bundle)
        result = export_hardware_ir(bundle, registry=registry)
        self.assertIsNone(result.payload)
        self.assertIn("unrepresentable", {f.code for f in result.findings})
        acknowledged = export_hardware_ir(bundle, registry=registry, allow_lossy=True)
        self.assertIsNotNone(acknowledged.payload)
        self.assertIn("unrepresentable", {f.code for f in acknowledged.findings})
        self.assertEqual(before, fingerprint(bundle))

    def test_native_updates_survive_external_resolution_outage(self) -> None:
        """Independent native work can proceed while its unchanged foreign reference is unresolved."""
        registry, provider = external_registry()
        fixture = fixture_input()
        plan = select_domains("p", "Mount in lab", registry=registry,
            scope=ProductScope(physical_structure=True, domains=["space"]),
            contexts={"mechanical": fixture["plan"]["tasks"][0]["context"], "space": {"site_id": "lab"}})
        bundle = generate_product(plan, fixture_author(fixture["designs"]), references=[provider.reference()], registry=registry)
        with patch.object(provider, "resolve", return_value=None):
            updated = generate_product(bundle.plan, fixture_author(fixture["designs"]), existing=bundle,
                                       update_domains={"mechanical"}, registry=registry)
            report = validate_product(updated, registry=registry)
        self.assertEqual(2, updated.artifacts["p.mechanical"]["revision"])
        self.assertEqual([], report.local["p.mechanical"])
        self.assertIn("missing_artifact", {f.code for f in report.composition})

    def test_external_updates_require_explicit_references_and_do_not_reuse_stale_cache(self) -> None:
        """Foreign data is neither silently ignored nor overwritten by a native author."""
        registry, provider = external_registry()
        plan = select_domains("p", "Map site", registry=registry, scope=ProductScope(domains=["space"]),
                              contexts={"space": {"site_id": "lab"}})
        old_ref = provider.reference()
        bundle = generate_product(plan, fixture_author({}), references=[old_ref], registry=registry)
        bundle.artifacts[old_ref.artifact_id] = deepcopy(provider.payload)
        before = fingerprint(bundle)
        with self.assertRaisesRegex(ValueError, "updated pinned reference"):
            generate_product(bundle.plan, fixture_author({}), existing=bundle, update_domains={"space"}, registry=registry)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            generate_product(plan, fixture_author({}), references=[old_ref, old_ref], registry=registry)
        with self.assertRaisesRegex(ValueError, "External designs"):
            call_domain_tool("generate", {"plan": plan.model_dump(), "designs": {"space": provider.payload}}, registry=registry)
        provider.payload["edit"] = "rev-8"
        updated = generate_product(bundle.plan, fixture_author({}), existing=bundle, update_domains={"space"},
                                   references=[provider.reference()], registry=registry)
        self.assertNotIn(old_ref.artifact_id, updated.artifacts)
        self.assertTrue(validate_product(updated, registry=registry).is_valid)
        self.assertEqual(before, fingerprint(bundle))

    def test_public_compose_cli_and_schema_discovery_keep_external_schema_opaque(self) -> None:
        """SDK/CLI share the registry and composition operation without plugins or a server."""
        from forma_core.cli.main import main
        registry, provider = external_registry()
        bundle = mixed_bundle(provider)
        request = {"product_id": "product", "documents": list(bundle.artifacts.values()),
                   "references": [provider.reference().model_dump()],
                   "links": [link.model_dump() for link in bundle.manifest.links]}
        output = StringIO()
        with patch("forma_core.domains.tools.get_registry", return_value=registry), patch(
            "sys.stdin", StringIO(json.dumps(request))), redirect_stdout(output):
            self.assertEqual(0, main(["domains", "compose", "-"]))
        self.assertEqual(bundle, ProductBundle.model_validate_json(output.getvalue()))
        self.assertIsNone(call_domain_tool("schema", {"domain": "space"}, registry=registry)["document"])


if __name__ == "__main__":
    unittest.main()

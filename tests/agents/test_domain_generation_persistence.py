"""Domain generation through real SQLite revisions and the existing worker controller."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from forma_core.persistence.providers import create_sqlite_provider
from forma_core.persistence.repositories import SqlAlchemyRepository
from forma_core.workspaces.design_briefs import DesignBrief, DESIGN_BRIEF_SCHEMA_VERSION
from forma_core.workspaces.projects import ProjectStateService, ProjectStateError
from forma_core.workspaces.projects.resolver import ProjectReadResolver, ProjectReadNotFoundError
from forma_core.workers import (
    EngineeringGenerationEngine, GenerationWorker, WorkerOrchestrator, WorkerRequest,
    WORKER_CONTRACT_VERSION, GENERATION_WORKER_ID, GENERATION_CAPABILITY_ID, GENERATION_INPUT_VERSION,
)
from forma_core.workspaces.validation.evaluator import evaluate_project_revision
from forma_core.domains.generation import EngineeringResult, generate_engineering
from tests.projects.test_unified_generation import chemistry_request, decision_for


class DomainPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """No HardwareIR wrapper is needed for a saved chemical/process project."""

    def setUp(self) -> None:
        """Create a real repository and freeze the chemistry benchmark brief."""
        self.directory = tempfile.TemporaryDirectory()
        self.provider = create_sqlite_provider(source="domain persistence test", url=f"sqlite:///{Path(self.directory.name) / 'state.db'}", import_legacy_jobs=False)
        self.provider.initialize()
        self.repository = SqlAlchemyRepository(self.provider.session_factory)
        self.state = ProjectStateService(self.repository)
        self.project_id = uuid4()
        from forma_core.workspaces.workflow import ProjectWorkflowService, ProjectWorkflowState, WorkflowActorType
        self.workflow = ProjectWorkflowService(self.repository)
        self.workflow.initialize(str(self.project_id), "owner")
        self.workflow.transition(str(self.project_id), "owner", ProjectWorkflowState.BUILDING,
                                 actor_type=WorkflowActorType.USER, actor_id="owner", reason="Chemistry benchmark")
        self.brief = DesignBrief(
            schema_version=DESIGN_BRIEF_SCHEMA_VERSION,
            conversation_id="chemistry", intent=chemistry_request().brief, summary=chemistry_request().brief,
            requirements=["Chemical and process representation"], constraints=[], requested_outputs=["portable artifacts"],
            validation_criteria=["Element and charge balance"], assumptions=[], readiness="ready",
            design_brief_id=uuid4(), project_id=self.project_id, brief_version=1, created_at=datetime.now(timezone.utc))
        self.repository.insert_design_brief_version({
            "id": str(uuid4()), "design_brief_id": str(self.brief.design_brief_id), "project_id": str(self.project_id),
            "conversation_id": "chemistry", "owner_user_id": "owner", "brief_version": 1,
            "schema_version": self.brief.schema_version, "previous_version": None,
            "payload_json": self.brief.model_dump(mode="json"), "created_at": self.brief.created_at.isoformat(),
        })

    def tearDown(self) -> None:
        """Release the isolated SQLite repository."""
        self.provider.dispose()
        self.directory.cleanup()

    async def test_worker_persists_reloads_and_replays_domain_artifacts(self) -> None:
        """The worker, immutable state service and read resolver preserve portable hashes."""
        fixture = chemistry_request()
        old_id = fixture.product_id
        fixture.product_id = str(self.project_id)
        for link in fixture.links:
            for endpoint in (link.source, link.target):
                endpoint.artifact_id = endpoint.artifact_id.replace(old_id, fixture.product_id)
        fixture.decision = decision_for(fixture, fixture.decision.scope.model_dump(), fixture.decision.reasons)
        result = generate_engineering(fixture)
        worker = GenerationWorker(self.state, EngineeringGenerationEngine(generate_image=False))
        controller = WorkerOrchestrator(self.repository, [worker])
        request = WorkerRequest(
            contract_version=WORKER_CONTRACT_VERSION, project_id=self.project_id, project_revision=1,
            design_brief_id=self.brief.design_brief_id, design_brief_version=1, job_id="chemistry-worker",
            correlation_id="chemistry-worker", worker_id=GENERATION_WORKER_ID, capability_id=GENERATION_CAPABILITY_ID,
            input_contract_version=GENERATION_INPUT_VERSION, payload={"design_brief": self.brief.model_dump(mode="json")})
        plan = controller.create_plan([request], "owner")
        with patch("forma_core.agents.workflows.generate_project_with_workflow", return_value=result) as generation:
            completed = await controller.execute(plan.plan_id, "owner")
        self.assertEqual("partial", completed.status.value)
        generation.assert_called_once()
        saved = self.state.get_latest(self.project_id, "owner")
        self.assertIsInstance(saved.state, EngineeringResult)
        self.assertEqual(result.product, saved.state.product)
        self.assertEqual([], saved.components)
        self.assertEqual({"chemical", "process"}, {a.kind for a in saved.artifacts})
        resolved = ProjectReadResolver(self.repository).resolve(str(self.project_id), "owner")
        self.assertEqual("forma.engineering-result", resolved.project_ir["format"])
        with self.assertRaises(ProjectReadNotFoundError):
            ProjectReadResolver(self.repository).resolve(str(self.project_id), "another-owner")
        report = evaluate_project_revision(saved, self.brief)
        self.assertEqual("passed", report.findings[0].status.value)
        self.assertEqual("warning", report.findings[1].status.value)
        self.assertEqual(saved, self.state.get_by_source_job(self.project_id, "owner", "chemistry-worker"))

    async def test_api_response_is_saved_and_reloadable(self) -> None:
        """The normal builder persists the same report in canonical revisions."""
        from apps.api import a2a, main
        from forma_core import database
        from forma_core.domains.base import fingerprint
        from types import SimpleNamespace
        fixture = chemistry_request()
        previous_id = fixture.product_id
        fixture.product_id = str(self.project_id)
        for link in fixture.links:
            for endpoint in (link.source, link.target):
                endpoint.artifact_id = endpoint.artifact_id.replace(previous_id, fixture.product_id)
        fixture.decision = decision_for(fixture, fixture.decision.scope.model_dump(), fixture.decision.reasons)
        with patch.object(database, "_DATABASE_REPOSITORY", self.repository), patch.object(database, "ensure_chat_project", return_value=self.brief), patch.object(a2a, "ensure_hosted_chat_enabled"), patch.object(a2a, "_resolve_owner_user_integrations", return_value=None):
            response = a2a.build_generation_response(fixture.brief, project_id=fixture.product_id, owner_user_id="owner", frontend_job_id="api-domain", engineering_request=fixture.model_dump(mode="json"))
            replay = a2a.build_generation_response(fixture.brief, project_id=fixture.product_id, owner_user_id="owner", frontend_job_id="api-domain", engineering_request=fixture.model_dump(mode="json"))
            self.assertEqual(response["engineering_result"], replay["engineering_result"])
            with self.assertRaisesRegex(ValueError, "reload"):
                a2a.build_generation_response(fixture.brief, project_id=fixture.product_id, owner_user_id="owner", frontend_job_id="stale-update", engineering_request=fixture.model_dump(mode="json"))
            resolved = ProjectReadResolver(self.repository).resolve(fixture.product_id, "owner")
            with patch.object(main, "resolve_project_for_read", return_value=resolved):
                loaded = main.get_project_endpoint(fixture.product_id, SimpleNamespace(owner_user_id="owner"))
        self.assertEqual(response["engineering_result"], loaded["engineering_result"])
        saved = self.state.get_latest(self.project_id, "owner")
        self.assertEqual(fingerprint(saved.state.product), saved.state.provenance["artifact_sha256"])

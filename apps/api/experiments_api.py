"""Authenticated remote experiment receipts using existing private artifact storage."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from apps.api.auth import UserContext, require_user_context
from forma_core.domains.base import DomainModel, canonical_json
from forma_core.domains.providers import Digest
from forma_core.experiments import ExperimentPlan, ExperimentRequest, ExperimentRun, compare_experiment, prepare_experiment
from forma_core.experiments.tools import ExperimentToolInput, experiment_tool
from forma_core.persistence.project_artifacts import ProjectArtifactStorage, ProjectArtifactStorageError

router = APIRouter(prefix="/experiments", tags=["experiments"])
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def artifact_storage() -> ProjectArtifactStorage:
    """Use the configured private local/Supabase/S3 backend, never ephemeral fallback."""
    return ProjectArtifactStorage()


def _scope(user: UserContext, kind: str) -> str:
    """Derive ownership on the server; clients cannot select another user's namespace."""
    if not user.owner_user_id:
        raise HTTPException(401, "Sign in to save experiments.")
    return f"experiments:{user.owner_user_id}:{kind}"


def _save(storage: ProjectArtifactStorage, user: UserContext, kind: str, value: dict[str, Any]) -> dict[str, Any]:
    """Persist immutable bytes and return a portable content address."""
    content = canonical_json(value).encode()
    digest = hashlib.sha256(content).hexdigest()
    try:
        storage.put(_scope(user, kind), digest, content, "application/json")
    except ProjectArtifactStorageError as exc:
        raise HTTPException(503, "Private artifact storage unavailable; record was not saved.") from exc
    return {"sha256": digest, "record": value}


def _load(storage: ProjectArtifactStorage, user: UserContext, kind: str, digest: str,
          media_type: str = "application/json") -> bytes:
    """Read only owner-scoped bytes and verify their content address on every read."""
    try:
        content = storage.get(_scope(user, kind), digest, media_type).content
    except FileNotFoundError as exc:
        raise HTTPException(404, "Record not found for this account.") from exc
    except ProjectArtifactStorageError as exc:
        raise HTTPException(503, "Private artifact storage unavailable.") from exc
    if content is None or hashlib.sha256(content).hexdigest() != digest:
        raise HTTPException(409, "Stored artifact integrity mismatch.")
    return content


@router.post("/tools")
def tools_endpoint(payload: ExperimentToolInput, user: UserContext = Depends(require_user_context)) -> dict[str, Any]:
    """Expose the same pure SDK/CLI/MCP operation over the existing REST service."""
    try:
        return experiment_tool(payload.model_dump(mode="json", exclude_none=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class FreezeInput(DomainModel):
    """Freeze inputs before execution, optionally linking to a saved predecessor."""

    request: ExperimentRequest
    parent_record_sha256: Digest | None = None


@router.post("/plans")
def freeze_plan(payload: FreezeInput, user: UserContext = Depends(require_user_context),
                storage: ProjectArtifactStorage = Depends(artifact_storage)) -> dict[str, Any]:
    """Recompute and save a timestamped original prediction, with no replacement endpoint."""
    try:
        plan = prepare_experiment(payload.request)
        if payload.request.revision > 1:
            if payload.parent_record_sha256 is None:
                raise ValueError("Refinement requires the saved parent record.")
            parent = json.loads(_load(storage, user, "plans", payload.parent_record_sha256))
            previous = ExperimentPlan.model_validate(parent["plan"])
            if (previous.scientific_sha256 != payload.request.parent_plan_sha256
                    or previous.request.experiment_id != payload.request.experiment_id
                    or previous.request.revision + 1 != payload.request.revision):
                raise ValueError("Parent identity/revision/hash mismatch.")
        elif payload.parent_record_sha256 is not None or payload.request.parent_plan_sha256 is not None:
            raise ValueError("An initial plan cannot declare a parent.")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _save(storage, user, "plans", {"format": "forma.saved-experiment-plan",
        "created_at": datetime.now(timezone.utc).isoformat(), "parent_record_sha256": payload.parent_record_sha256,
        "plan": plan.model_dump(mode="json")})


class ReportInput(DomainModel):
    """Compare against a stored original, including all desired independent trials."""

    plan_record_sha256: Digest
    runs: list[ExperimentRun] = Field(default_factory=list, max_length=100)


@router.post("/reports")
def save_report(payload: ReportInput, user: UserContext = Depends(require_user_context),
                storage: ProjectArtifactStorage = Depends(artifact_storage)) -> dict[str, Any]:
    """Store supplied observations plus reproducible comparisons without mutating the plan."""
    original = json.loads(_load(storage, user, "plans", payload.plan_record_sha256))
    now = datetime.now(timezone.utc)
    try:
        for run in payload.runs:
            if run.kind == "physical" and (run.started_at < datetime.fromisoformat(original["created_at"])
                                          or run.completed_at > now):
                raise ValueError("Physical trials must start after plan freeze and finish before submission.")
            for attachment in run.attachments:
                _load(storage, user, "evidence", attachment.sha256, attachment.media_type)
        report = compare_experiment(ExperimentPlan.model_validate(original["plan"]), payload.runs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _save(storage, user, "reports", {"format": "forma.saved-experiment-report",
        "created_at": now.isoformat(), "plan_record_sha256": payload.plan_record_sha256, "report": report})


@router.post("/evidence")
async def upload_evidence(request: Request, user: UserContext = Depends(require_user_context),
                          storage: ProjectArtifactStorage = Depends(artifact_storage)) -> dict[str, Any]:
    """Accept bounded raw evidence bytes; do not infer measurements from photographs."""
    media_type = request.headers.get("content-type", "").split(";")[0]
    if media_type not in {"image/jpeg", "image/png", "image/webp", "text/plain", "application/json"}:
        raise HTTPException(415, "Use JPEG, PNG, WebP, plain text or JSON evidence.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_EVIDENCE_BYTES:
            raise HTTPException(413, "Evidence exceeds 8 MiB.")
        content.extend(chunk)
    if not content:
        raise HTTPException(422, "Evidence is empty.")
    digest = hashlib.sha256(content).hexdigest()
    try:
        await run_in_threadpool(storage.put, _scope(user, "evidence"), digest, bytes(content), media_type)
    except ProjectArtifactStorageError as exc:
        raise HTTPException(503, "Evidence storage unavailable; upload was not saved.") from exc
    return {"sha256": digest, "media_type": media_type, "size_bytes": len(content)}


@router.get("/records/{kind}/{digest}")
def read_record(kind: Literal["plans", "reports", "evidence"],
                digest: str = Path(pattern=r"^[a-f0-9]{64}$"),
                user: UserContext = Depends(require_user_context),
                storage: ProjectArtifactStorage = Depends(artifact_storage)) -> Response:
    """Download immutable private records; evidence is delivered as an attachment."""
    content = _load(storage, user, kind, digest)
    return Response(content, media_type="application/octet-stream" if kind == "evidence" else "application/json",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-{digest}.json"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

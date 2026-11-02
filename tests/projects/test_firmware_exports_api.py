"""Authenticated firmware export flow through the /exports manifest.

Only the native worker and artifact store are fakes, mirroring
tests/projects/test_ender_exports_api.py. The firmware zip is regenerated
deterministically from the persisted hardware IR, so the manifest digest and
the served bytes must agree on every round-trip.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api import readiness_api as api
from forma_core.firmware import esp32_example_ir, generate_firmware
from forma_core.workspaces.projects.models import HardwareIntermediateRepresentation

PROJECT = str(uuid4())
STEP_SOURCE = b"ISO-10303-21; firmware exports fixture"
STEP_DIGEST = hashlib.sha256(STEP_SOURCE).hexdigest()


def _project_ir() -> HardwareIntermediateRepresentation:
    ir = esp32_example_ir()
    ir.cad_model = {"format": "step", "stored_sha256": STEP_DIGEST}
    return ir


@pytest.fixture
def rig(monkeypatch):
    saved = {(PROJECT, STEP_DIGEST, api.STEP_MEDIA_TYPE): STEP_SOURCE}
    ir = _project_ir()

    class Storage:
        def put(self, project_id, digest, content, media_type):
            saved[(project_id, digest, media_type)] = content

        def get(self, project_id, digest, media_type):
            try:
                return SimpleNamespace(content=saved[(project_id, digest, media_type)])
            except KeyError as exc:
                raise FileNotFoundError() from exc

    monkeypatch.setattr(api, "get_project_identity", lambda pid: {"owner_user_id": "owner", "status": "active"})
    monkeypatch.setattr(
        api,
        "get_latest_project_revision",
        lambda *a: SimpleNamespace(state=ir),
    )
    monkeypatch.setattr(api, "ProjectArtifactStorage", Storage)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_user_context] = lambda: SimpleNamespace(owner_user_id="owner")
    with TestClient(app) as client:
        yield client, saved, ir


def test_manifest_lists_firmware_with_content_addressed_url(rig):
    client, _saved, _ir = rig
    manifest = client.get(f"/projects/{PROJECT}/exports")
    assert manifest.status_code == 200, manifest.text
    firmware = manifest.json()["firmware"]
    assert firmware is not None
    assert firmware["filename"] == "firmware.zip"
    assert firmware["media_type"] == "application/zip"
    assert firmware["board_id"] == "esp32dev"
    assert len(firmware["sha256"]) == 64
    assert firmware["download_url"] == (
        f"/projects/{PROJECT}/exports/firmware/{firmware['sha256']}"
    )


def test_firmware_download_round_trips_the_zip(rig):
    client, _saved, ir = rig
    firmware = client.get(f"/projects/{PROJECT}/exports").json()["firmware"]

    downloaded = client.get(firmware["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="firmware.zip"'
    assert hashlib.sha256(downloaded.content).hexdigest() == firmware["sha256"]

    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        extracted = json.loads(json.dumps({name: archive.read(name).decode() for name in archive.namelist()}))
    assert extracted == generate_firmware(ir).files


def test_firmware_digest_is_stable_across_regenerations(rig):
    client, _saved, _ir = rig
    first = client.get(f"/projects/{PROJECT}/exports").json()["firmware"]
    second = client.get(f"/projects/{PROJECT}/exports").json()["firmware"]
    assert first["sha256"] == second["sha256"]


def test_unknown_sha256_is_not_served(rig):
    client, _saved, _ir = rig
    response = client.get(f"/projects/{PROJECT}/exports/firmware/{'0' * 64}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "firmware_not_found"


def test_no_mcu_project_has_no_firmware_entry(monkeypatch):
    empty = HardwareIntermediateRepresentation()
    empty.cad_model = {"format": "step", "stored_sha256": STEP_DIGEST}
    monkeypatch.setattr(api, "get_project_identity", lambda pid: {"owner_user_id": "owner", "status": "active"})
    monkeypatch.setattr(api, "get_latest_project_revision", lambda *a: SimpleNamespace(state=empty))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_user_context] = lambda: SimpleNamespace(owner_user_id="owner")
    client = TestClient(app)

    manifest = client.get(f"/projects/{PROJECT}/exports")
    assert manifest.status_code == 200
    assert manifest.json()["firmware"] is None

"""Remote round trips using a temporary private store; all readings are test data."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.api.auth import UserContext, require_user_context
from apps.api.experiments_api import artifact_storage, router
from forma_core.persistence.project_artifacts import ProjectArtifactStorage
from forma_core.experiments import ExperimentPlan
from forma_core.experiments.models import Attachment
from tests.projects.test_empirical_verification import fixture, trial


ClientFixture = tuple[TestClient, FastAPI, ProjectArtifactStorage]


@pytest.fixture
def client(tmp_path: Any) -> Iterator[ClientFixture]:
    """Use real content-addressed storage with replaceable owner identity."""
    app=FastAPI(); app.include_router(router)
    storage=ProjectArtifactStorage(storage_config={'enabled':True,'backend':'local','directory':str(tmp_path),'max_bytes':10*1024*1024})
    app.dependency_overrides[artifact_storage]=lambda: storage
    app.dependency_overrides[require_user_context]=lambda: UserContext('test','alice','alice',True,False)
    with TestClient(app) as test_client:
        yield test_client, app, storage


def test_freeze_upload_compare_restore_and_cross_owner_denial(client: ClientFixture) -> None:
    """Persist original, evidence and report; reject access from another account."""
    browser, app, storage=client
    frozen=browser.post('/experiments/plans',json={'request':fixture().model_dump(mode='json')})
    assert frozen.status_code==200, frozen.text
    saved=frozen.json(); plan=ExperimentPlan.model_validate(saved['record']['plan'])
    raw=b'raw instrument record for synthetic test'
    upload=browser.post('/experiments/evidence',content=raw,headers={'content-type':'text/plain'})
    assert upload.status_code==200, upload.text
    run=trial(plan); run.attachments=[Attachment(sha256=upload.json()['sha256'],media_type='text/plain',source_id='raw')]
    report=browser.post('/experiments/reports',json={'plan_record_sha256':saved['sha256'],'runs':[run.model_dump(mode='json')]})
    assert report.status_code==200, report.text
    receipt=report.json()
    assert receipt['record']['report']['physical_status']=='not_run'
    restored=browser.get('/experiments/records/reports/'+receipt['sha256'])
    assert restored.json()==receipt['record']
    assert hashlib.sha256(restored.content).hexdigest()==receipt['sha256']
    assert browser.get('/experiments/records/plans/'+saved['sha256']).json()==saved['record']
    app.dependency_overrides[require_user_context]=lambda: UserContext('test','bob','bob',True,False)
    assert browser.get('/experiments/records/plans/'+saved['sha256']).status_code==404


def test_preregistration_missing_photo_and_refinement(client: ClientFixture) -> None:
    """Reject pre-freeze physical data, missing evidence, and a mismatched parent."""
    browser,_,_=client
    saved=browser.post('/experiments/plans',json={'request':fixture().model_dump(mode='json')}).json()
    plan=ExperimentPlan.model_validate(saved['record']['plan'])
    run=trial(plan,kind='physical')
    response=browser.post('/experiments/reports',json={'plan_record_sha256':saved['sha256'],'runs':[run.model_dump(mode='json')]})
    assert response.status_code==422 and 'after plan freeze' in response.text
    run=trial(plan); raw=run.model_dump(mode='json')
    raw['attachments']=[{'sha256':'0'*64,'media_type':'image/png','source_id':'raw'}]
    assert browser.post('/experiments/reports',json={'plan_record_sha256':saved['sha256'],'runs':[raw]}).status_code==404
    request=fixture();request.revision=2;request.parent_plan_sha256=plan.scientific_sha256
    request.calculation.inventory[0].value=1
    assert browser.post('/experiments/plans',json={'request':request.model_dump(mode='json')}).status_code==422
    refined=browser.post('/experiments/plans',json={'request':request.model_dump(mode='json'),'parent_record_sha256':saved['sha256']})
    assert refined.status_code==200, refined.text
    assert browser.get('/experiments/records/plans/'+saved['sha256']).json()==saved['record']
    assert refined.json()['record']['plan']['scientific_sha256']!=plan.scientific_sha256


def test_transport_validation_and_unavailable_storage(client: ClientFixture) -> None:
    """No successful save is reported for unavailable persistence or malformed input."""
    browser,app,_=client
    assert browser.post('/experiments/evidence',content=b'<script/>',headers={'content-type':'text/html'}).status_code==415
    assert browser.post('/experiments/evidence',content=b'',headers={'content-type':'text/plain'}).status_code==422
    assert browser.post('/experiments/tools',json={'action':'benchmark','unexpected':1}).status_code==422
    assert browser.post('/experiments/tools',json={'action':'schema'}).status_code==200
    assert browser.get('/experiments/records/plans/not-a-hash').status_code==422
    class BrokenStore:
        """Emulate a storage outage without making network calls."""
        def put(self, *args: Any) -> None:
            """Fail before returning a persistence acknowledgement."""
            from forma_core.persistence.project_artifacts import ProjectArtifactStorageError
            raise ProjectArtifactStorageError('offline')
    app.dependency_overrides[artifact_storage]=lambda: BrokenStore()
    assert browser.post('/experiments/plans',json={'request':fixture().model_dump(mode='json')}).status_code==503


def test_corrupt_storage_is_not_trusted(client: ClientFixture) -> None:
    """Digest verification detects altered persisted bytes."""
    browser,_,storage=client
    saved=browser.post('/experiments/plans',json={'request':fixture().model_dump(mode='json')}).json()
    from pathlib import Path
    from forma_core.persistence.project_artifacts import project_artifact_storage_key
    path=Path(storage.config['directory']) / project_artifact_storage_key('experiments:alice:plans',saved['sha256'])
    path.write_bytes(b'{}')
    assert browser.get('/experiments/records/plans/'+saved['sha256']).status_code==409

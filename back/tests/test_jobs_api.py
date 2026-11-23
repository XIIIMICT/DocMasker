"""Tests for the job API: submit, status, result, cancel, validation, 429."""

import io
import zipfile
from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient
from test_docx import _build_docx

from src.config import Settings
from src.jobs.repository import JobRepository
from src.jobs.storage import ArtifactStore
from src.main import app


@pytest.fixture
def jobs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings.model_validate(
        {
            "jobs_db_path": str(tmp_path / "jobs.sqlite3"),
            "jobs_dir": str(tmp_path),
            "jobs_max_active": 16,
            "jobs_ttl_seconds": 3600.0,
            "max_text_chars": 200_000,
            "sync_max_text_chars": 20_000,
        }
    )
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    import src.jobs.api as jobs_api
    import src.jobs.dependencies as deps

    monkeypatch.setattr(jobs_api, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps.job_repository.cache_clear()
    deps.artifact_store.cache_clear()
    return settings


@pytest.fixture
def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _submit_text(client: AsyncClient, text: str = "Иван Петров") -> dict:
    response = await client.post(
        "/api/jobs",
        data={"kind": "mask_text", "text": text},
    )
    assert response.status_code == 202
    return response.json()


async def test_submit_text_returns_202_with_urls(client: AsyncClient, jobs_env: Settings) -> None:
    body = await _submit_text(client)
    assert len(body["job_id"]) == 36
    assert body["status"] == "queued"
    assert body["status_url"] == f"/api/jobs/{body['job_id']}"
    assert body["result_url"] == f"/api/jobs/{body['job_id']}/result"


async def test_status_is_safe_and_result_not_ready(client: AsyncClient, jobs_env: Settings) -> None:
    accepted = await _submit_text(client)
    response = await client.get(accepted["status_url"])
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert body["progress"] == 0
    assert body["result_url"] is None
    # No PII in the response.
    assert "Иван" not in response.text
    result = await client.get(accepted["result_url"])
    assert result.status_code == 409


async def test_unknown_job_and_result_are_404(client: AsyncClient, jobs_env: Settings) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/jobs/{missing}")).status_code == 404
    assert (await client.get(f"/api/jobs/{missing}/result")).status_code == 404
    assert (await client.delete(f"/api/jobs/{missing}")).status_code == 404


async def test_cancel_job(client: AsyncClient, jobs_env: Settings) -> None:
    accepted = await _submit_text(client)
    response = await client.delete(f"/api/jobs/{accepted['job_id']}")
    assert response.status_code == 204
    status = await client.get(accepted["status_url"])
    assert status.json()["status"] == "cancelled"
    # Cancelling a terminal job is a no-op (not 404).
    assert (await client.delete(f"/api/jobs/{accepted['job_id']}")).status_code == 204


async def test_result_serves_zip_with_no_store(client: AsyncClient, jobs_env: Settings) -> None:
    accepted = await _submit_text(client)
    repo = JobRepository(jobs_env)
    repo.claim(lease_seconds=30, concurrency=1)
    repo.succeed(accepted["job_id"], "result", ttl_seconds=3600)
    store = ArtifactStore(jobs_env.jobs_dir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("masked.docx", b"docx")
    store.write(accepted["job_id"], "result", buffer.getvalue())
    response = await client.get(accepted["result_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["masked.docx"]


async def test_submit_file_validates_docx(client: AsyncClient, jobs_env: Settings) -> None:
    response = await client.post(
        "/api/jobs",
        data={"kind": "mask_file"},
        files={"file": ("doc.docx", _build_docx(), "application/octet-stream")},
    )
    assert response.status_code == 202
    store = ArtifactStore(jobs_env.jobs_dir)
    assert store.exists(response.json()["job_id"], "input")


async def test_submit_file_rejects_garbage(client: AsyncClient, jobs_env: Settings) -> None:
    response = await client.post(
        "/api/jobs",
        data={"kind": "mask_file"},
        files={"file": ("doc.docx", b"not a zip" * 20, "application/octet-stream")},
    )
    assert response.status_code == 415


async def test_unknown_kind_and_profile_rejected(client: AsyncClient, jobs_env: Settings) -> None:
    assert (await client.post("/api/jobs", data={"kind": "fly", "text": "x"})).status_code == 422
    assert (
        await client.post("/api/jobs", data={"kind": "mask_text", "text": "x", "profile_id": "nope"})
    ).status_code == 422


async def test_queue_full_returns_429(client: AsyncClient, jobs_env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.jobs import routing

    monkeypatch.setattr(routing, "get_settings", lambda: jobs_env.model_copy(update={"jobs_max_active": 0}))
    response = await client.post("/api/jobs", data={"kind": "mask_text", "text": "x"})
    assert response.status_code == 429

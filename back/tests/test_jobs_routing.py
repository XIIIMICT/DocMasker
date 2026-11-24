"""Tests: oversized sync inputs are accepted as durable jobs (ADR-0002)."""

from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient
from test_docx import _build_docx

from src.config import Settings
from src.jobs.storage import ArtifactStore
from src.main import app


@pytest.fixture
def routed_env(tmp_path: Path, pg_url: str, clean_jobs: None, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings.model_validate(
        {
            "database_url": pg_url,
            "jobs_dir": str(tmp_path),
            "jobs_max_active": 16,
            "jobs_ttl_seconds": 3600.0,
            "max_text_chars": 200_000,
            "max_keys_chars": 1_000_000,
            "sync_max_text_chars": 50,  # tiny for the test
            "sync_max_docx_bytes": 1024 * 1024,
            "sync_max_docx_paragraphs": 1,
            "upload_max_mb": 25,
        }
    )
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    import src.jobs.dependencies as deps
    import src.masking.router as masking_router
    from src.jobs import routing

    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(routing, "get_settings", lambda: settings)
    monkeypatch.setattr(masking_router, "get_settings", lambda: settings)
    deps.job_repository.cache_clear()
    deps.artifact_store.cache_clear()
    return settings


@pytest.fixture
def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_small_text_stays_sync(client: AsyncClient, routed_env: Settings) -> None:
    response = await client.post("/api/mask", json={"text": "Иван Петров"})
    assert response.status_code == 200
    assert "text" in response.json()


async def test_large_text_becomes_job(client: AsyncClient, routed_env: Settings) -> None:
    text = "Иван Петров " * 20  # > 50 chars
    response = await client.post("/api/mask", json={"text": text})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["status_url"].startswith("/api/jobs/")
    # Input was spooled, not stored in a DB row.
    store = ArtifactStore(routed_env.jobs_dir)
    assert store.exists(body["job_id"], "input")
    assert store.read(body["job_id"], "input").decode("utf-8") == text


async def test_large_unmask_text_becomes_job_with_keys(client: AsyncClient, routed_env: Settings) -> None:
    keys = "Физлица:\n  - Иван Петров"
    text = "[PERSON_1] " * 20
    response = await client.post("/api/unmask", json={"text": text, "keys": keys})
    assert response.status_code == 202
    body = response.json()
    store = ArtifactStore(routed_env.jobs_dir)
    assert store.exists(body["job_id"], "keys")
    assert store.read(body["job_id"], "keys").decode("utf-8") == keys


async def test_large_docx_becomes_job_by_paragraph_count(client: AsyncClient, routed_env: Settings) -> None:
    # _build_docx has several paragraphs across body/header/table → > 1.
    response = await client.post(
        "/api/mask-file",
        files={"file": ("doc.docx", _build_docx(), "application/octet-stream")},
    )
    assert response.status_code == 202
    body = response.json()
    store = ArtifactStore(routed_env.jobs_dir)
    assert store.exists(body["job_id"], "name")
    assert store.read(body["job_id"], "name").decode("utf-8") == "doc.docx"
    assert store.read(body["job_id"], "input").startswith(b"PK")

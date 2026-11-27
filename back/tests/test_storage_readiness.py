"""Artifact storage readiness: typed failures before staging or worker claims."""

import errno
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from src.config import Settings
from src.errors import REQUEST_ID_HEADER, install_error_handlers
from src.jobs import routing
from src.jobs.readiness import ARTIFACT_STORAGE_CODE, ArtifactStorageError, check_artifact_storage
from src.jobs.storage import ArtifactStore
from src.jobs.worker import Worker
from src.main import app

if TYPE_CHECKING:
    from src.jobs.repository import JobRepository


def _usage(*, free: int) -> SimpleNamespace:
    return SimpleNamespace(total=free * 2, used=free, free=free)


def _settings(tmp_path: Path, *, min_free: int = 4096) -> Settings:
    return Settings.model_validate({"jobs_dir": str(tmp_path), "jobs_min_free_bytes": min_free})


def test_default_free_space_floor_is_one_gib() -> None:
    settings = Settings.model_validate({})
    assert settings.jobs_min_free_bytes == 1024 * 1024 * 1024


def test_readiness_probe_leaves_no_artifact(tmp_path: Path) -> None:
    check_artifact_storage(tmp_path, 1)
    assert list(tmp_path.iterdir()) == []


def test_full_volume_raises_typed_error_without_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.jobs.readiness.shutil.disk_usage", lambda _path: _usage(free=0))

    with pytest.raises(ArtifactStorageError) as raised:
        check_artifact_storage(tmp_path, 4096)

    assert raised.value.code == ARTIFACT_STORAGE_CODE
    assert raised.value.detail == "Недостаточно свободного места для сохранения результатов задания."
    assert list(tmp_path.iterdir()) == []


def test_read_only_volume_raises_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.jobs.readiness.shutil.disk_usage", lambda _path: _usage(free=10**12))

    def _deny(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EROFS, "read-only file system")

    monkeypatch.setattr("src.jobs.readiness.tempfile.mkstemp", _deny)

    with pytest.raises(ArtifactStorageError) as raised:
        check_artifact_storage(tmp_path, 4096)

    assert raised.value.code == ARTIFACT_STORAGE_CODE
    assert raised.value.detail == "Хранилище артефактов недоступно для записи."
    assert list(tmp_path.iterdir()) == []


def test_uninspectable_volume_raises_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(_path: Path) -> None:
        raise OSError(errno.EIO, "device error")

    monkeypatch.setattr("src.jobs.readiness.shutil.disk_usage", _deny)

    with pytest.raises(ArtifactStorageError) as raised:
        check_artifact_storage(tmp_path, 4096)

    assert raised.value.code == ARTIFACT_STORAGE_CODE
    assert raised.value.detail == "Хранилище артефактов недоступно."


def test_admission_rejects_before_staging_or_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(routing, "get_settings", lambda: settings)
    monkeypatch.setattr(routing, "artifact_store", lambda: ArtifactStore(tmp_path))
    monkeypatch.setattr("src.jobs.cache.build_cache_key", lambda *_args: "key")
    monkeypatch.setattr("src.jobs.readiness.shutil.disk_usage", lambda _path: _usage(free=0))

    def _no_repository() -> None:
        pytest.fail("job repository must not be touched before the readiness gate")

    monkeypatch.setattr(routing, "job_repository", _no_repository)

    with pytest.raises(ArtifactStorageError) as raised:
        routing.enqueue("mask_text", input_bytes=b"x", filename="")

    assert raised.value.code == ARTIFACT_STORAGE_CODE
    assert list(tmp_path.iterdir()) == []


def test_worker_run_fails_before_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, min_free=10**12)
    monkeypatch.setattr("src.jobs.readiness.shutil.disk_usage", lambda _path: _usage(free=0))

    class _Repo:
        def claim_with_owner(self, **_kwargs: object) -> None:
            pytest.fail("no claim may happen before the readiness gate")

    worker = Worker(settings, cast("JobRepository", _Repo()), ArtifactStore(tmp_path))

    with pytest.raises(ArtifactStorageError) as raised:
        worker.run()

    assert raised.value.code == ARTIFACT_STORAGE_CODE
    assert list(tmp_path.iterdir()) == []


async def test_typed_error_uses_the_job_error_envelope() -> None:
    """503 is the same `{code, detail, request_id}` contract, not an ambiguous 500."""
    probe = FastAPI()
    install_error_handlers(probe)

    @probe.post("/api/jobs")
    async def submit() -> None:
        raise ArtifactStorageError(
            ARTIFACT_STORAGE_CODE, "Недостаточно свободного места для сохранения результатов задания."
        )

    transport = ASGITransport(app=probe, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/jobs")

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"code", "detail", "request_id"}
    assert body["code"] == ARTIFACT_STORAGE_CODE
    assert "места" in body["detail"]
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_job_submission_surfaces_typed_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """A storage rejection at admission is client-visible through the real app."""
    import src.jobs.api as jobs_api

    def _deny(*_args: object, **_kwargs: object) -> None:
        raise ArtifactStorageError(ARTIFACT_STORAGE_CODE, "Хранилище артефактов недоступно для записи.")

    monkeypatch.setattr(jobs_api, "enqueue", _deny)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/jobs", data={"kind": "mask_text", "text": "Иван Петров"})

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == ARTIFACT_STORAGE_CODE
    assert body["detail"] == "Хранилище артефактов недоступно для записи."
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"

"""Worker integration tests (ADR-0002): end-to-end jobs, retry, sweep."""

import io
import json
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from test_docx import _build_docx

from src.config import Settings
from src.jobs.repository import JobRepository
from src.jobs.routing import enqueue
from src.jobs.storage import ArtifactStore
from src.jobs.worker import Worker

if TYPE_CHECKING:
    from src.jobs.models import Job


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings.model_validate(
        {
            "jobs_db_path": str(tmp_path / "jobs.sqlite3"),
            "jobs_dir": str(tmp_path),
            "jobs_concurrency": 2,
            "jobs_max_attempts": 3,
            "jobs_lease_seconds": 30.0,
            "jobs_poll_seconds": 0.01,
            "jobs_ttl_seconds": 3600.0,
            "max_text_chars": 200_000,
            "upload_max_mb": 25,
        }
    )
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    import src.jobs.dependencies as deps
    import src.masking.docx_validation as dv
    from src.jobs import routing

    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(routing, "get_settings", lambda: settings)
    monkeypatch.setattr(dv, "get_settings", lambda: settings)
    deps.job_repository.cache_clear()
    deps.artifact_store.cache_clear()
    return settings


@pytest.fixture
def worker(env: Settings) -> Worker:
    return Worker(env, JobRepository(env), ArtifactStore(env.jobs_dir))


def _run(worker: Worker, env: Settings, accepted_id: str) -> Job:
    repo = JobRepository(env)
    claimed = repo.claim(lease_seconds=30, concurrency=1)
    assert claimed is not None
    assert claimed.id == accepted_id
    worker.run_one(accepted_id)
    return repo.get(accepted_id)


def _result_zip(store: ArtifactStore, job_id: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(store.read(job_id, "result")))


def test_mask_text_job_round_trip(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes="Иван Петров, ivan@mail.ru".encode(), filename="")
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    assert job.phase == "completed"
    assert job.progress == 100
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job.id) as archive:
        payload = json.loads(archive.read("result.json"))
    assert payload["text"] == "[PERSON_1], [EMAIL_1]"
    assert "Иван Петров" in payload["keys"]
    assert payload["stats"]["total"] == 2


def test_unmask_text_job_round_trip(env: Settings, worker: Worker) -> None:
    keys = "Физлица:\n  - Иван Петров"
    accepted = enqueue(
        "unmask_text",
        input_bytes="[PERSON_1] пришёл.".encode(),
        keys_bytes=keys.encode(),
        filename="",
    )
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job.id) as archive:
        payload = json.loads(archive.read("result.json"))
    assert payload["text"] == "Иван Петров пришёл."


def test_mask_file_job_round_trip(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_file", input_bytes=_build_docx(), filename="отчёт.docx")
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job.id) as archive:
        names = archive.namelist()
        assert any(name.endswith("(замаскированный).docx") for name in names)
        assert any(name.endswith("(ключи демаскировки).yaml") for name in names)
        masked = archive.read(next(name for name in names if name.endswith(".docx")))
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(masked)).paragraphs)
    assert "Иван Петров" not in text
    assert "[PERSON_1]" in text


def test_unmask_file_job_round_trip(env: Settings, worker: Worker) -> None:
    # First mask a document, then restore it through a job.
    from src.masking.docx_service import mask_docx_in_memory

    source = _build_docx()
    masked_bytes, _entities, keys = mask_docx_in_memory(source)
    accepted = enqueue(
        "unmask_file",
        input_bytes=masked_bytes,
        keys_bytes=keys.to_yaml().encode(),
        filename="отчёт (замаскированный).docx",
    )
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job.id) as archive:
        names = archive.namelist()
        restored = archive.read(next(name for name in names if name.endswith(".docx")))
        manifest = json.loads(archive.read(next(name for name in names if name.endswith(".json"))))
    assert manifest == {"unused_keys": []}
    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(restored)).paragraphs)
    assert "Иван Петров" in text


def test_failed_job_is_retried_then_final(env: Settings, worker: Worker) -> None:
    repo = JobRepository(env)
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    # Invalid UTF-8 in the input is an internal (retryable) failure.
    store = ArtifactStore(env.jobs_dir)
    store.write(accepted.job_id, "input", b"\xff\xfe")
    for attempt in range(3):
        claimed = repo.claim(lease_seconds=30, concurrency=1)
        assert claimed is not None
        worker.run_one(accepted.job_id)
        status = repo.get(accepted.job_id).status
        if attempt < 2:
            assert status == "queued"
    assert status == "failed"
    assert repo.get(accepted.job_id).attempt == 3


def test_lease_loss_stops_execution(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    repo.cancel(accepted.job_id)  # no longer claimable
    with pytest.raises(AssertionError):
        _run(worker, env, accepted.job_id)


def test_progress_callback_detects_cancellation(env: Settings, worker: Worker) -> None:
    from src.jobs.worker import LeaseLostError

    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    claimed = repo.claim(lease_seconds=30, concurrency=1)
    assert claimed is not None
    report = worker._progress(accepted.job_id)
    repo.cancel(accepted.job_id)
    with pytest.raises(LeaseLostError):
        report("detection", 50)


def test_sweep_removes_expired_jobs_and_artifacts(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    store = ArtifactStore(env.jobs_dir)
    # Force TTL expiry in the row.
    with repo._connect() as conn:
        conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (time.time() - 1, accepted.job_id))
    worker.sweep()
    with pytest.raises(KeyError):
        repo.get(accepted.job_id)
    assert not store.exists(accepted.job_id, "input")

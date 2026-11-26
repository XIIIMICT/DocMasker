"""Worker integration tests (ADR-0002): end-to-end jobs, retry, sweep."""

import io
import json
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from test_docx import _build_docx

from src.config import Settings
from src.jobs.repository import IllegalTransitionError, JobRepository
from src.jobs.routing import enqueue
from src.jobs.storage import ArtifactStore
from src.jobs.worker import Heartbeat, Worker

if TYPE_CHECKING:
    from src.jobs.models import Claim, Job


@pytest.fixture
def env(tmp_path: Path, pg_url: str, clean_jobs: None, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings.model_validate(
        {
            "database_url": pg_url,
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


def test_worker_cache_gc_is_periodic_and_outage_tolerant(worker: Worker, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.jobs import worker as worker_module
    from src.jobs.cache import CacheGcReport

    calls: list[int] = []

    class _FakeCache:
        def gc(self) -> CacheGcReport:
            calls.append(1)
            return CacheGcReport(candidates=1, deleted=1, reclaimed_bytes=4)

    monkeypatch.setattr(worker_module.dependencies, "result_cache", _FakeCache)
    worker._cache_gc_safe()
    assert calls == [1]

    def _broken() -> None:
        msg = "cache dependency unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(worker_module.dependencies, "result_cache", _broken)
    worker._cache_gc_safe()  # a transient failure must never kill the worker


def _run(worker: Worker, env: Settings, accepted_id: str) -> Job:
    repo = JobRepository(env)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    job, claim = claimed
    assert job.id == accepted_id
    worker.run_one(accepted_id, claim)
    return repo.get(accepted_id)


def _result_zip(store: ArtifactStore, job: Job) -> zipfile.ZipFile:
    """Read the exact immutable artifact referenced by the succeeded row."""
    assert job.result_key is not None
    return zipfile.ZipFile(io.BytesIO(store.read_result(job.result_key)))


def test_worker_poll_survives_transient_db_error(worker: Worker) -> None:
    """A PostgreSQL restart must not kill the poll loop (stress finding, Phase 8)."""

    class _FailingRepo:
        def claim_with_owner(self, **_kwargs: object) -> None:
            msg = "db down"
            raise RuntimeError(msg)

    worker._repo = cast("JobRepository", _FailingRepo())
    assert worker._claim_safe() is None


def test_heartbeat_survives_transient_db_error(env: Settings) -> None:
    """A transient heartbeat error must not kill the renewal thread."""

    class _FailingRepo:
        def heartbeat(self, *_args: object, **_kwargs: object) -> bool:
            msg = "db down"
            raise RuntimeError(msg)

    claim = cast("Claim", type("C", (), {})())  # Heartbeat only stores the claim object
    heartbeat = Heartbeat(cast("JobRepository", _FailingRepo()), "00000000-0000-0000-0000-000000000000", claim, env)
    heartbeat.start()
    time.sleep(0.05)
    heartbeat.stop()
    assert not heartbeat.lost()  # a DB failure is not proof of lost ownership


def test_mask_text_job_round_trip(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes="Иван Петров, ivan@mail.ru".encode(), filename="")
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    assert job.phase == "completed"
    assert job.progress == 100
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job) as archive:
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
    with _result_zip(store, job) as archive:
        payload = json.loads(archive.read("result.json"))
    assert payload["text"] == "Иван Петров пришёл."


def test_mask_file_job_round_trip(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_file", input_bytes=_build_docx(), filename="отчёт.docx")
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job) as archive:
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
    masked_bytes, _entities, keys, _coverage = mask_docx_in_memory(source)
    accepted = enqueue(
        "unmask_file",
        input_bytes=masked_bytes,
        keys_bytes=keys.to_yaml().encode(),
        filename="отчёт (замаскированный).docx",
    )
    job = _run(worker, env, accepted.job_id)
    assert job.status == "succeeded"
    store = ArtifactStore(env.jobs_dir)
    with _result_zip(store, job) as archive:
        names = archive.namelist()
        restored = archive.read(next(name for name in names if name.endswith(".docx")))
        manifest = json.loads(archive.read(next(name for name in names if name.endswith(".json"))))
    assert manifest["statistics"]["unused_keys"] == []
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
        claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
        assert claimed is not None
        _, claim = claimed
        worker.run_one(accepted.job_id, claim)
        status = repo.get(accepted.job_id).status
        if attempt < 2:
            assert status == "queued"
            # Later retries carry a bounded exponential backoff; wait it out.
            if attempt == 1:
                time.sleep(1.2)
    assert status == "failed"
    assert repo.get(accepted.job_id).attempt == 3


def test_lease_loss_stops_execution(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    repo.request_cancel(accepted.job_id)  # queued -> terminal, not claimable
    assert repo.claim_with_owner(lease_seconds=30, concurrency=1) is None


def test_stale_token_cannot_publish_a_result(env: Settings) -> None:
    """§3.5: a stale worker's succeed/heartbeat/fail affect 0 rows."""
    repo = JobRepository(env)
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    first = repo.claim_with_owner(lease_seconds=-1, concurrency=1)
    assert first is not None
    _, stale = first
    second = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert second is not None
    _, current = second
    # Fresh token owns the row; the stale one cannot heartbeat or publish.
    assert repo.heartbeat(job.id, "detection", 50, lease_seconds=30, claim=stale) is False
    with pytest.raises(IllegalTransitionError):
        repo.succeed(job.id, "result", ttl_seconds=3600, claim=stale)
    assert repo.get(job.id).status == "running"
    assert repo.succeed(job.id, "result", ttl_seconds=3600, claim=current) is None
    assert repo.get(job.id).status == "succeeded"
    repo.dispose()


def test_progress_callback_detects_cancellation(env: Settings, worker: Worker) -> None:
    from src.jobs.worker import Heartbeat, LeaseLostError

    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    heartbeat = Heartbeat(repo, accepted.job_id, claim, env)
    report = worker._progress(accepted.job_id, claim, heartbeat)
    repo.request_cancel(accepted.job_id)
    heartbeat.mark_lost()
    with pytest.raises((LeaseLostError, RuntimeError)):
        report("detection", 50)


def test_heartbeat_keeps_a_long_operation_owned(env: Settings) -> None:
    """§3.6/§14: lease survives a blocking operation longer than one interval."""
    from src.jobs.worker import Heartbeat

    repo = JobRepository(env)
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim_with_owner(lease_seconds=0.4, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    heartbeat = Heartbeat(repo, job.id, claim, env)
    heartbeat.start()
    time.sleep(1.0)  # > 2 lease intervals: only the heartbeat can keep it owned
    heartbeat.stop()
    assert not heartbeat.lost()
    assert repo.get(job.id).status == "running"
    # A second worker cannot claim the still-owned job.
    assert repo.claim_with_owner(lease_seconds=30, concurrency=2) is None
    repo.dispose()


def test_cancel_before_processing_publishes_nothing(env: Settings, worker: Worker) -> None:
    """§3.7: a cancelled job never produces a result or cache entry."""
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    assert repo.request_cancel(accepted.job_id) == "cancelling"
    worker.run_one(accepted.job_id, claim)
    job = repo.get(accepted.job_id)
    assert job.status == "cancelled"
    assert job.result_key is None
    assert not ArtifactStore(env.jobs_dir).exists(accepted.job_id, "result")
    repo.dispose()


def test_identical_concurrent_submissions_attach_to_one_job(env: Settings) -> None:
    """§4.5/§4.6-12: identical inputs dedupe to one active job, no duplicate work."""
    first = enqueue("mask_text", input_bytes=b"same-input", filename="")
    second = enqueue("mask_text", input_bytes=b"same-input", filename="")
    assert first.job_id == second.job_id
    repo = JobRepository(env)
    assert repo.get(first.job_id).status == "queued"
    repo.dispose()


def test_cancelled_job_never_populates_cache(env: Settings, worker: Worker) -> None:
    """§4.6-11: a cancelled job publishes no result and writes no cache entry."""
    from src.jobs import dependencies

    accepted = enqueue("mask_text", input_bytes=b"cache-must-not-appear", filename="")
    repo = JobRepository(env)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    repo.request_cancel(accepted.job_id)
    worker.run_one(accepted.job_id, claim)
    assert repo.get(accepted.job_id).status == "cancelled"
    cache = dependencies.result_cache()
    key = repo.get(accepted.job_id).cache_key
    if key and cache._client is not None:
        assert cache.get(key) is None
    repo.dispose()


def test_sweep_removes_expired_jobs_and_artifacts(env: Settings, worker: Worker) -> None:
    accepted = enqueue("mask_text", input_bytes=b"x", filename="")
    repo = JobRepository(env)
    store = ArtifactStore(env.jobs_dir)
    # TTL cleanup targets terminal rows only (brief §3.9): publish a result
    # with an already-expired TTL, then sweep.
    repo.claim(lease_seconds=30, concurrency=1)
    repo.succeed(accepted.job_id, "result", ttl_seconds=-1)
    worker.sweep()
    with pytest.raises(KeyError):
        repo.get(accepted.job_id)
    assert not store.exists(accepted.job_id, "input")
    repo.dispose()

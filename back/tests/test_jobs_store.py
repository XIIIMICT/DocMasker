"""Tests for the durable job store (ADR-0002): claims, leases, restart, TTL."""

import time
from pathlib import Path

import pytest

from src.config import Settings
from src.jobs.repository import JobNotFoundError, JobQueueFullError, JobRepository
from src.jobs.storage import ArtifactStore, cleanup_artifacts


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "jobs_db_path": str(tmp_path / "jobs.sqlite3"),
            "jobs_dir": str(tmp_path),
            "jobs_concurrency": 2,
            "jobs_max_attempts": 3,
            "jobs_lease_seconds": 30.0,
            "jobs_ttl_seconds": 3600.0,
        }
    )


@pytest.fixture
def repo(settings: Settings) -> JobRepository:
    return JobRepository(settings)


@pytest.fixture
def store(settings: Settings) -> ArtifactStore:
    return ArtifactStore(settings.jobs_dir)


def test_create_and_get(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    fetched = repo.get(job.id)
    assert fetched.id == job.id
    assert fetched.status == "queued"
    assert fetched.kind == "mask_text"
    assert len(job.id) == 36  # opaque uuid4


def test_unknown_job_raises(repo: JobRepository) -> None:
    with pytest.raises(JobNotFoundError):
        repo.get("00000000-0000-0000-0000-000000000000")


def test_queue_full_is_bounded(repo: JobRepository) -> None:
    for _ in range(3):
        repo.create("mask_text", max_active=3, ttl_seconds=3600)
    with pytest.raises(JobQueueFullError, match="переполнена"):
        repo.create("mask_text", max_active=3, ttl_seconds=3600)


def test_claim_is_exclusive_and_bounded(repo: JobRepository) -> None:
    first = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    second = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim(lease_seconds=30, concurrency=1)
    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status == "running"
    assert claimed.attempt == 1
    # concurrency=1: no second running job can be taken.
    assert repo.claim(lease_seconds=30, concurrency=1) is None
    assert repo.get(second.id).status == "queued"


def test_stale_lease_is_reclaimed(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim(lease_seconds=-1, concurrency=1)
    assert claimed is not None
    # A new worker (new repository instance over the same DB file) reclaims it.
    again = repo.claim(lease_seconds=30, concurrency=1)
    assert again is not None
    assert again.id == job.id
    assert again.attempt == 2


def test_state_survives_repository_restart(settings: Settings) -> None:
    first = JobRepository(settings)
    job = first.create("mask_file", max_active=16, ttl_seconds=3600)
    first.claim(lease_seconds=30, concurrency=1)
    first.succeed(job.id, "result", ttl_seconds=3600)
    second = JobRepository(settings)  # simulates a process restart
    restored = second.get(job.id)
    assert restored.status == "succeeded"
    assert restored.result_key == "result"


def test_heartbeat_updates_progress_and_lease(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    repo.claim(lease_seconds=1, concurrency=1)
    assert repo.heartbeat(job.id, "detection", 40, lease_seconds=30) is True
    updated = repo.get(job.id)
    assert updated.phase == "detection"
    assert updated.progress == 40
    assert updated.lease_until is not None
    assert updated.lease_until > time.time()


def test_heartbeat_fails_without_ownership(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    # Never claimed: no ownership.
    assert repo.heartbeat(job.id, "detection", 10, lease_seconds=30) is False
    repo.claim(lease_seconds=-1, concurrency=1)
    assert repo.heartbeat(job.id, "detection", 10, lease_seconds=30) is False


def test_retryable_failure_requeues_then_fails(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    for attempt in range(1, 4):
        repo.claim(lease_seconds=30, concurrency=1)
        status = repo.fail(job.id, "dependency_unavailable", retryable=True, max_attempts=3)
        if attempt < 3:
            assert status == "queued"
    assert status == "failed"
    assert repo.get(job.id).status == "failed"
    assert repo.get(job.id).attempt == 3


def test_non_retryable_failure_is_final(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    repo.claim(lease_seconds=30, concurrency=1)
    assert repo.fail(job.id, "invalid_input", retryable=False, max_attempts=3) == "failed"


def test_cancel_only_affects_active_jobs(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    assert repo.cancel(job.id) is True
    assert repo.get(job.id).status == "cancelled"
    assert repo.cancel(job.id) is False
    with pytest.raises(JobNotFoundError):
        repo.cancel("00000000-0000-0000-0000-000000000000")


def test_ttl_sweep_removes_rows_and_artifacts(repo: JobRepository, store: ArtifactStore) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=-1)
    store.write(job.id, "input", b"payload")
    expired = repo.expired_jobs(time.time())
    assert [j.id for j in expired] == [job.id]
    assert cleanup_artifacts(store, expired) == 1
    assert not store.exists(job.id, "input")
    repo.remove_expired([j.id for j in expired])
    with pytest.raises(JobNotFoundError):
        repo.get(job.id)


def test_no_pii_columns_in_schema(repo: JobRepository) -> None:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(repo._db_path)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(jobs)")]
    assert set(columns) == {
        "id",
        "kind",
        "status",
        "phase",
        "progress",
        "attempt",
        "error_code",
        "created_at",
        "updated_at",
        "lease_until",
        "expires_at",
        "result_key",
    }
    for forbidden in ("text", "keys", "input", "filename", "pii"):
        assert forbidden not in columns


def test_artifact_paths_are_id_scoped(store: ArtifactStore) -> None:
    from src.jobs.storage import ArtifactError

    with pytest.raises(ArtifactError):
        store.write("../../etc/passwd", "input", b"x")
    with pytest.raises(ArtifactError):
        store.write("short", "input", b"x")


def test_artifact_write_is_atomic_and_readable(store: ArtifactStore) -> None:
    job_id = "12345678-1234-4234-8234-123456789abc"
    store.write(job_id, "input", b"hello")
    assert store.read(job_id, "input") == b"hello"
    assert store.size(job_id, "input") == 5
    store.write(job_id, "input", b"replaced")
    assert store.read(job_id, "input") == b"replaced"
    store.delete(job_id)
    assert not store.exists(job_id, "input")
    assert not any(path.name.startswith(job_id) for path in Path(store._root).iterdir())

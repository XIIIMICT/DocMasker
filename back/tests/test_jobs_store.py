"""Tests for the durable job store (ADR-0003, PostgreSQL).

Covers claims, leases, restart, TTL, staging and a PII-free schema. Requires
TEST_DATABASE_URL.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from src.config import Settings
from src.jobs.repository import JobNotFoundError, JobQueueFullError, JobRepository
from src.jobs.storage import ArtifactStore, cleanup_artifacts


@pytest.fixture
def settings(tmp_path: Path, pg_url: str, clean_jobs: None) -> Settings:
    return Settings.model_validate(
        {
            "database_url": pg_url,
            "jobs_dir": str(tmp_path),
            "jobs_concurrency": 2,
            "jobs_max_attempts": 3,
            "jobs_lease_seconds": 30.0,
            "jobs_ttl_seconds": 3600.0,
        }
    )


@pytest.fixture
def repo(settings: Settings) -> Iterator[JobRepository]:
    repository = JobRepository(settings)
    try:
        yield repository
    finally:
        repository.dispose()


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
    # Queued job: cancellation is terminal immediately and idempotent.
    assert repo.request_cancel(job.id) == "cancelled"
    assert repo.get(job.id).status == "cancelled"
    assert repo.request_cancel(job.id) == "cancelled"
    with pytest.raises(JobNotFoundError):
        repo.request_cancel("00000000-0000-0000-0000-000000000000")


def test_running_cancel_is_a_request_acknowledged_by_the_worker(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    # Running job: record `cancelling`, do not pretend the thread stopped.
    assert repo.request_cancel(job.id) == "cancelling"
    assert repo.get(job.id).status == "cancelling"
    assert repo.get(job.id).cancel_requested is True
    assert repo.heartbeat(job.id, "detection", 50, lease_seconds=30, claim=claim) is False
    repo.acknowledge_cancel(job.id, claim)
    assert repo.get(job.id).status == "cancelled"


def test_ttl_sweep_removes_rows_and_artifacts(repo: JobRepository, store: ArtifactStore) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=-1)
    store.write(job.id, "input", b"payload")
    # TTL cleanup only targets terminal rows (brief §3.9): a live queued job
    # must never be swept, even when its legacy expires_at stamp has passed.
    repo.claim(lease_seconds=30, concurrency=1)
    repo.succeed(job.id, "result", ttl_seconds=-1)
    expired = repo.expired_jobs(time.time())
    assert [j.id for j in expired] == [job.id]
    assert cleanup_artifacts(store, expired) == 1
    assert not store.exists(job.id, "input")
    repo.remove_expired([j.id for j in expired])
    with pytest.raises(JobNotFoundError):
        repo.get(job.id)


def test_no_pii_columns_in_schema(settings: Settings) -> None:
    inspector = inspect(create_engine(settings.sqlalchemy_database_url))
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert {
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
        "result_artifact",
        "lease_token",
        "worker_id",
        "cancel_requested_at",
    } <= columns
    for forbidden in ("text", "keys", "input", "filename", "pii", "payload"):
        assert forbidden not in columns


def test_staging_is_not_claimable(settings: Settings) -> None:
    repo = JobRepository(settings)
    staged, created = repo.create_staging("mask_text", max_active=16, max_attempts=3, cache_key="k")
    assert created is True
    assert staged.status == "staging"
    assert repo.claim(lease_seconds=30, concurrency=1) is None
    assert staged.id in repo.reconcile_staging()
    published = repo.publish_staging(staged.id, input_artifact="in", keys_artifact=None, ttl_seconds=3600)
    assert published.status == "queued"
    claimed = repo.claim(lease_seconds=30, concurrency=1)
    assert claimed is not None
    assert claimed.id == staged.id


def test_active_jobs_are_never_swept(settings: Settings) -> None:
    """§3.9: result TTL cleanup must never touch staging/queued/running rows."""
    repo = JobRepository(settings)
    job = repo.create("mask_text", max_active=16, ttl_seconds=-1)
    assert repo.expired_jobs(time.time()) == []
    repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert repo.expired_jobs(time.time()) == []
    assert repo.get(job.id).status == "running"
    repo.dispose()


def test_result_ttl_starts_on_completion(settings: Settings) -> None:
    """§3.9: TTL is set when the result is committed, not at enqueue."""
    repo = JobRepository(settings)
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    _, claim = claimed
    assert repo.get(job.id).status == "running"
    repo.succeed(job.id, "result", ttl_seconds=60, claim=claim)
    assert repo.expired_jobs(time.time()) == []
    assert repo.expired_jobs(time.time() + 120) != []
    repo.dispose()


def test_queue_full_leaves_no_staging_row(settings: Settings) -> None:
    """§14: a 429 admission failure leaves no orphan staging row."""
    repo = JobRepository(settings)
    for _ in range(2):
        repo.create("mask_text", max_active=2, ttl_seconds=3600)
    with pytest.raises(JobQueueFullError):
        repo.create("mask_text", max_active=2, ttl_seconds=3600)
    assert repo.reconcile_staging() == []


def test_concurrent_claims_never_share_a_lease(settings: Settings) -> None:
    from concurrent.futures import ThreadPoolExecutor

    repo = JobRepository(settings)
    first = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    second = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: repo.claim(lease_seconds=30, concurrency=2), range(2)))
    ids = [claim.id for claim in claims if claim is not None]
    assert sorted(ids) == sorted([first.id, second.id])
    assert len(ids) == len(set(ids))


def test_artifact_paths_are_id_scoped(store: ArtifactStore) -> None:
    from src.jobs.storage import ArtifactError

    with pytest.raises(ArtifactError):
        store.write("../../etc/passwd", "input", b"x")
    with pytest.raises(ArtifactError):
        store.write("short", "input", b"x")


def test_path_for_rejects_symlink_escape(store: ArtifactStore, tmp_path: Path) -> None:
    """A symlinked artifact must not resolve outside the private spool root."""
    from src.jobs.storage import ArtifactError

    job_id = "12345678-1234-4234-8234-123456789abc"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = store.root / f"{job_id}.result"
    try:
        link.symlink_to(outside)
    except OSError as error:  # Windows may require developer mode for symlinks
        pytest.skip(f"symlinks unavailable: {type(error).__name__}")
    with pytest.raises(ArtifactError):
        store.path_for(job_id, "result")


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

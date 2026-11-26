"""Tests for the durable job store (ADR-0003, PostgreSQL).

Covers claims, leases, restart, TTL, staging and a PII-free schema. Requires
TEST_DATABASE_URL.
"""

import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from src.config import Settings
from src.jobs.repository import (
    OWNERSHIP_LOST,
    IllegalTransitionError,
    JobNotFoundError,
    JobQueueFullError,
    JobRepository,
)
from src.jobs.routing import enqueue
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


def test_renew_lease_does_not_touch_progress(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    repo.claim(lease_seconds=1, concurrency=1)
    assert repo.report_progress(job.id, "validation", 10) == "ok"
    before = repo.get(job.id)
    assert repo.renew_lease(job.id, lease_seconds=30) is True
    after = repo.get(job.id)
    assert (after.phase, after.progress) == (before.phase, before.progress) == ("validation", 10)
    assert after.lease_until is not None
    assert after.lease_until > time.time()


def test_renew_lease_fails_without_ownership(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    # Never claimed: no ownership.
    assert repo.renew_lease(job.id, lease_seconds=30) is False
    repo.claim(lease_seconds=-1, concurrency=1)
    assert repo.renew_lease(job.id, lease_seconds=30) is False


def test_progress_is_monotonic_and_distinguishes_cancellation(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    repo.claim(lease_seconds=30, concurrency=1)
    for phase, progress in (("validation", 10), ("detection", 40), ("rewrite", 85), ("archive", 95)):
        assert repo.report_progress(job.id, phase, progress) == "ok"
        assert (repo.get(job.id).phase, repo.get(job.id).progress) == (phase, progress)
    # Out-of-order callbacks are ignored, not applied as ownership loss.
    assert repo.report_progress(job.id, "detection", 0) == "ok"
    assert repo.report_progress(job.id, "archive", 90) == "ok"
    assert repo.report_progress(job.id, "completed", 100) == "ok"
    assert (repo.get(job.id).phase, repo.get(job.id).progress) == ("completed", 100)
    repo.request_cancel(job.id)
    assert repo.report_progress(job.id, "archive", 99) == "cancelled"


def test_progress_requires_ownership(repo: JobRepository) -> None:
    job = repo.create("mask_text", max_active=16, ttl_seconds=3600)
    assert repo.report_progress(job.id, "detection", 10) == "ownership_lost"


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
    # The lease keeps renewing while the owner drains; progress reports the
    # cancellation distinctly instead of misreporting ownership loss.
    assert repo.renew_lease(job.id, lease_seconds=30, claim=claim) is True
    assert repo.report_progress(job.id, "detection", 50, claim=claim) == "cancelled"
    # The live claim can still acknowledge it exactly once.
    assert repo.acknowledge_cancel(job.id, claim) is True
    assert repo.get(job.id).status == "cancelled"
    assert repo.acknowledge_cancel(job.id, claim) is False  # idempotent, already terminal
    assert repo.get(job.id).status == "cancelled"


def test_stale_worker_cannot_overwrite_a_newer_result(
    settings: Settings, repo: JobRepository, store: ArtifactStore
) -> None:
    """A/B lease race: the old claim must not mutate state or artifacts (P0.1)."""
    import uuid

    enqueue("mask_text", input_bytes=b"old", filename="")
    first = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert first is not None
    job_a, claim_a = first

    # Deterministically expire A's lease in the database (no sleeps).
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
            {"id": uuid.UUID(job_a.id)},
        )

    second = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert second is not None
    job_b, claim_b = second
    assert claim_b.lease_token != claim_a.lease_token

    # A writes first with its own attempt key, then tries to publish/fail/ack.
    stale_key = store.write_result(job_b.id, claim_a.lease_token, b"stale")
    with pytest.raises(IllegalTransitionError):
        repo.succeed(job_b.id, stale_key, ttl_seconds=60, claim=claim_a)
    assert repo.fail(job_b.id, "internal_error", retryable=True, max_attempts=3, claim=claim_a) == OWNERSHIP_LOST
    assert repo.acknowledge_cancel(job_b.id, claim_a) is False

    # B publishes with a different immutable key; A's file can never replace it.
    fresh_key = store.write_result(job_b.id, claim_b.lease_token, b"fresh")
    repo.succeed(job_b.id, fresh_key, ttl_seconds=60, claim=claim_b)
    store.delete_attempt_artifacts(job_b.id, keep_key=fresh_key)

    final = repo.get(job_b.id)
    assert final.status == "succeeded"
    assert final.result_key == fresh_key
    assert stale_key != fresh_key
    assert not store.path_for_result(stale_key).exists()
    assert store.path_for_result(fresh_key).read_bytes() == b"fresh"


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
    # A live staging lease is never recovered; only an abandoned one is.
    assert staged.id not in repo.reconcile_staging()
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


def test_abandoned_staging_is_recovered_not_claimed(repo: JobRepository) -> None:
    """A crash between staging insert and artifact publication must not hang."""
    staged, created = repo.create_staging("mask_text", max_active=16, max_attempts=3, cache_key=str(uuid.uuid4()))
    assert created
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET staging_until = now() - interval '1 second' WHERE id = :id"),
            {"id": uuid.UUID(staged.id)},
        )
    assert staged.id in repo.recover_abandoned_staging()
    job = repo.get(staged.id)
    assert job.status == "failed"
    assert job.error_code == "staging_abandoned"
    assert job.phase == "failed"
    # Terminal staging failures are never claimable work.
    assert repo.claim_with_owner(lease_seconds=30, concurrency=1) is None


def test_expired_cancelling_is_terminally_cancelled(repo: JobRepository) -> None:
    """Worker death after request_cancel() must release queue capacity."""
    repo.create("mask_text", max_active=16, ttl_seconds=3600)
    claimed = repo.claim_with_owner(lease_seconds=30, concurrency=1)
    assert claimed is not None
    job, claim = claimed
    assert repo.request_cancel(job.id) == "cancelling"
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
            {"id": uuid.UUID(job.id)},
        )
    assert job.id in repo.recover_expired_cancelling()
    final = repo.get(job.id)
    assert final.status == "cancelled"
    assert final.cancel_requested is True
    assert final.lease_token is None
    assert final.lease_until is None
    # Capacity is released and the old owner cannot revive the row.
    assert repo.acknowledge_cancel(job.id, claim) is False
    assert repo.fail(job.id, "internal_error", retryable=True, max_attempts=3, claim=claim) == OWNERSHIP_LOST


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
    # store.root == tmp_path, so the target must be a sibling of tmp_path.
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
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


# --- P1/P2: real concurrent admission (independent connections + barrier) -----


def _concurrent_admission(
    settings: Settings,
    keys: list[str],
    *,
    max_active: int,
) -> list[tuple[str, str | None]]:
    barrier = threading.Barrier(len(keys))
    by_index: dict[int, tuple[str, str | None]] = {}
    guard = threading.Lock()
    repos = [JobRepository(settings) for _ in keys]

    def submit(index: int) -> None:
        barrier.wait()
        try:
            job, _created = repos[index].create_staging(
                "mask_text",
                max_active=max_active,
                max_attempts=3,
                cache_key=keys[index],
            )
            outcome = ("ok", job.id)
        except JobQueueFullError:
            outcome = ("full", None)
        with guard:
            by_index[index] = outcome

    try:
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            list(pool.map(submit, range(len(keys))))
    finally:
        for repository in repos:
            repository.dispose()
    return [by_index[index] for index in range(len(keys))]


def test_concurrent_identical_submissions_attach_to_one_job(settings: Settings) -> None:
    results = _concurrent_admission(settings, ["same-key"] * 12, max_active=12)
    assert all(outcome == "ok" for outcome, _ in results)
    assert len({job_id for _, job_id in results}) == 1
    observer = JobRepository(settings)
    try:
        assert observer.count_active() == 1
    finally:
        observer.dispose()


def test_concurrent_distinct_submissions_respect_capacity(settings: Settings) -> None:
    results = _concurrent_admission(settings, [f"key-{index}" for index in range(12)], max_active=3)
    assert sum(outcome == "ok" for outcome, _ in results) == 3
    assert sum(outcome == "full" for outcome, _ in results) == 9
    observer = JobRepository(settings)
    try:
        assert observer.count_active() == 3
    finally:
        observer.dispose()


def test_concurrent_mixed_submissions_never_exceed_capacity(settings: Settings) -> None:
    keys = ["shared", "shared", "shared", "a", "b", "c", "d", "e"]
    results = _concurrent_admission(settings, keys, max_active=3)
    accepted = [(key, job_id) for key, (outcome, job_id) in zip(keys, results, strict=True) if outcome == "ok"]
    # Any repeated key maps to one row; which submissions win capacity is
    # nondeterministic, but no duplicate active row can be created.
    assert len({job_id for _, job_id in accepted}) == 3
    for key in set(keys):
        assert len({job_id for k, job_id in accepted if k == key}) <= 1
    observer = JobRepository(settings)
    try:
        assert observer.count_active() == 3
    finally:
        observer.dispose()


def test_staging_failure_can_be_aborted_and_retried(repo: JobRepository) -> None:
    job, created = repo.create_staging("mask_text", max_active=1, max_attempts=3, cache_key="retry-key")
    assert created is True
    repo.abort_staging(job.id, "artifact_io")
    assert repo.get(job.id).status == "failed"
    assert repo.count_active() == 0
    retry, created_again = repo.create_staging("mask_text", max_active=1, max_attempts=3, cache_key="retry-key")
    assert created_again is True
    assert retry.id != job.id
    assert repo.count_active() == 1

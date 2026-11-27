"""Disposable-only operational drill harness for DocMasker jobs.

Every helper here takes an explicit PostgreSQL URL and artifact directory. It
never reads the root ``.env``, never mounts a shared volume and never deletes
anything outside the caller-provided spool. Production code is exercised as-is;
failures are injected by swapping module attributes inside the harness only.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import platform
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.config import Settings
from src.jobs.repository import OWNERSHIP_LOST, IllegalTransitionError, JobNotFoundError, JobRepository
from src.jobs.storage import ArtifactStore
from src.jobs.worker import Worker
from src.masking.archives import write_archive_to_path

if TYPE_CHECKING:
    from src.jobs.models import Claim, Job, JobKind


@dataclass(slots=True)
class DrillReport:
    """JSON-safe collection of measured drill outcomes (no PII, no URLs)."""

    cases: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, **facts: object) -> None:
        self.cases.append({"name": name, **facts})

    def as_dict(self) -> dict[str, object]:
        return {"cases": self.cases}


class NoCache:
    """Offline cache double: the drills never contact Valkey."""

    def get_to_path(self, _key: str, _target: Path) -> bool:
        return False

    def put_from_path(self, _key: str, _source: Path) -> None:
        return None


def drill_settings(pg_url: str, jobs_dir: Path, **overrides: object) -> Settings:
    """Build settings pinned to a disposable database and spool."""
    values: dict[str, object] = {
        "database_url": pg_url,
        "jobs_dir": str(jobs_dir),
        "jobs_concurrency": 2,
        "jobs_max_attempts": 3,
        "jobs_lease_seconds": 30.0,
        "jobs_poll_seconds": 0.01,
        "jobs_ttl_seconds": 3600.0,
        "cache_enabled": False,
        "cache_key_secret": "",
        "worker_metrics_host": "127.0.0.1",
        "worker_metrics_port": 18002,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _cache_key(kind: str, data: bytes, filename: str) -> str:
    """Synthetic opaque key; caching is disabled in the drills."""
    return f"drill:{hashlib.sha256(data + filename.encode('utf-8') + uuid.uuid4().bytes).hexdigest()}:{kind}"


def enqueue_direct(
    settings: Settings,
    repo: JobRepository,
    store: ArtifactStore,
    kind: JobKind,
    *,
    data: bytes,
    filename: str = "",
    keys: bytes | None = None,
) -> str:
    """Drive the production staging -> queued repository methods explicitly."""
    staged, created = repo.create_staging(
        kind,
        max_active=settings.jobs_max_active,
        max_attempts=settings.jobs_max_attempts,
        cache_key=_cache_key(kind, data, filename),
    )
    if not created:
        msg = "Drill inputs must be unique; staging dedupe attached to an existing job."
        raise RuntimeError(msg)
    try:
        store.write(staged.id, "name", filename.encode("utf-8"))
        store.write(staged.id, "kind", kind.encode("ascii"))
        store.write(staged.id, "input", data)
        if keys is not None:
            store.write(staged.id, "keys", keys)
    except Exception:
        repo.abort_staging(staged.id, "staging_failed")
        store.delete(staged.id)
        raise
    repo.publish_staging(
        staged.id,
        input_artifact="input",
        keys_artifact="keys" if keys is not None else None,
        ttl_seconds=settings.jobs_ttl_seconds,
    )
    return staged.id


def build_docx(paragraphs: int, marker: str) -> bytes:
    """Deterministic synthetic DOCX with PII-shaped patterns (no real data)."""
    import io as _io

    from docx import Document

    doc = Document()
    for index in range(paragraphs):
        doc.add_paragraph(
            f"Подписант {index}: Иван Петров, телефон +7 (900) 123-45-{index % 100:02d}, "
            f"почта user{index}@example.com,marker={marker}."
        )
    buffer = _io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def synthetic_text(repeats: int, marker: str) -> bytes:
    """Synthetic Russian text containing only fabricated PII-shaped values."""
    return " ".join(f"Иван Петров, ivan{i}@example.com marker={marker}" for i in range(repeats)).encode("utf-8")


def result_integrity(store: ArtifactStore, job_id: str, key: str) -> dict[str, Any]:
    """Verify the exact DB-referenced artifact: bytes, ZIP structure, hash."""
    path = store.path_for_result(key)
    raw = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        corrupt = archive.testzip()
        entry_count = len(archive.namelist())
    return {
        "job_id": job_id,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "zip_ok": corrupt is None and entry_count > 0,
        "entry_count": entry_count,
    }


def _sql_expire_staging(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET staging_until = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _sql_expire_lease(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _sql_expire_terminal(repo: JobRepository, job_id: str) -> None:
    with repo._engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET result_expires_at = clock_timestamp() - interval '2 seconds' WHERE id = :id"),
            {"id": job_id},
        )


def _spool_audit(store: ArtifactStore, repo: JobRepository) -> dict[str, int]:
    """Count referenced-but-missing result bytes and unreferenced result files."""
    with repo._engine.connect() as connection:
        rows = connection.execute(text("SELECT result_artifact FROM jobs WHERE result_artifact IS NOT NULL")).all()
    referenced = {str(row[0]) for row in rows}
    files = {path.name for path in store.root.glob("*.result.*") if path.is_file()}
    return {
        "missing_artifacts": len(referenced - files),
        "orphan_artifacts": len(files - referenced),
        "referenced_artifacts": len(referenced),
        "result_files": len(files),
    }


def _claim(repo: JobRepository, settings: Settings) -> tuple[str, Claim]:
    claimed = repo.claim_with_owner(
        lease_seconds=settings.jobs_lease_seconds,
        concurrency=settings.jobs_concurrency,
        worker_id=str(uuid.uuid4()),
    )
    if claimed is None:
        msg = "Expected a claimable queued job in the drill."
        raise RuntimeError(msg)
    return claimed[0].id, claimed[1]


def crash_staging(settings: Settings) -> dict[str, Any]:
    """Crash after staging artifacts are written but before publish; restart + sweep."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    staged, created = repo.create_staging(
        "mask_text",
        max_active=settings.jobs_max_active,
        max_attempts=settings.jobs_max_attempts,
        cache_key=_cache_key("mask_text", b"staging-crash", ""),
    )
    assert created
    store.write(staged.id, "name", b"")
    store.write(staged.id, "kind", b"mask_text")
    store.write(staged.id, "input", synthetic_text(1, "staging"))
    _sql_expire_staging(repo, staged.id)
    repo.dispose()

    restarted = JobRepository(settings)
    Worker(settings, restarted, store).sweep()
    job = restarted.get(staged.id)
    leftovers = [path.name for path in store.root.glob(f"{staged.id}.*") if path.is_file()]
    outcome = {
        "status": job.status,
        "error_code": job.error_code,
        "published": job.result_key is not None,
        "artifact_files_after_sweep": len(leftovers),
    }
    restarted.dispose()
    assert job.status == "failed"
    assert job.error_code == "staging_abandoned"
    assert outcome["published"] is False
    assert leftovers == []
    return outcome


def crash_after_db_commit(settings: Settings) -> dict[str, Any]:
    """Crash after the staging -> queued commit; a restarted worker finishes it."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    job_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(2, "db-commit"))
    repo.dispose()

    restarted = JobRepository(settings)
    worker = Worker(settings, restarted, store)
    claimed_id, claim = _claim(restarted, settings)
    assert claimed_id == job_id
    worker.run_one(job_id, claim)
    job = restarted.get(job_id)
    integrity = result_integrity(store, job_id, job.result_key or "")
    outcome = {"status": job.status, "phase": job.phase, "integrity": integrity}
    restarted.dispose()
    assert job.status == "succeeded"
    assert integrity["zip_ok"]
    return outcome


def crash_during_processing(settings: Settings) -> dict[str, Any]:
    """Kill an unheartbeated claim, reclaim it, then prove the stale one is fenced."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    job_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(2, "processing"))
    claimed = repo.claim_with_owner(
        lease_seconds=0.05,
        concurrency=settings.jobs_concurrency,
        worker_id=str(uuid.uuid4()),
    )
    assert claimed is not None
    claimed_id, stale = claimed[0].id, claimed[1]
    assert claimed_id == job_id
    time.sleep(0.1)  # short lease, no heartbeat: simulate an abrupt worker death
    repo.dispose()

    restarted = JobRepository(settings)
    _, current = _claim(restarted, settings)
    assert current.lease_token != stale.lease_token
    stale_publish_accepted = False
    with contextlib.suppress(IllegalTransitionError):
        restarted.succeed(job_id, "stale-key", ttl_seconds=3600, claim=stale)
        stale_publish_accepted = True
    stale_failure = restarted.fail(job_id, "worker_lost", retryable=True, max_attempts=3, claim=stale)
    stale_heartbeat = restarted.renew_lease(job_id, lease_seconds=30, claim=stale)
    assert stale_publish_accepted is False
    assert stale_failure == OWNERSHIP_LOST
    assert stale_heartbeat is False

    Worker(settings, restarted, store).run_one(job_id, current)
    job = restarted.get(job_id)
    integrity = result_integrity(store, job_id, job.result_key or "")
    assert job.result_key != "stale-key"
    assert not (store.root / "stale-key").exists()
    outcome = {
        "status": job.status,
        "attempt": job.attempt,
        "stale_publish_accepted": stale_publish_accepted,
        "stale_failure": stale_failure,
        "stale_heartbeat": stale_heartbeat,
        "integrity": integrity,
    }
    restarted.dispose()
    assert job.status == "succeeded"
    assert job.attempt == 2
    return outcome


def crash_during_zip(settings: Settings) -> dict[str, Any]:
    """Inject ENOSPC at ZIP creation; retry after restart must publish cleanly."""
    from src.jobs import worker as worker_module

    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    job_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(2, "zip-crash"))
    _, claim = _claim(repo, settings)
    worker = Worker(settings, repo, store)
    original = worker_module.write_archive_to_path

    def fail_zip(target: Path, entries: dict[str, bytes | Path]) -> None:
        raise OSError(28, "simulated ENOSPC")

    setattr(worker_module, "write_archive_to_path", fail_zip)  # noqa: B010 - harness-only injection
    try:
        worker.run_one(job_id, claim)
    finally:
        setattr(worker_module, "write_archive_to_path", original)  # noqa: B010
    first = repo.get(job_id)
    assert first.status in ("queued", "running")
    assert first.result_key is None
    assert list(store.root.glob(f"{job_id}.result.*")) == []

    restarted = JobRepository(settings)
    _, next_claim = _claim(restarted, settings)
    Worker(settings, restarted, store).run_one(job_id, next_claim)
    recovered = restarted.get(job_id)
    integrity = result_integrity(store, job_id, recovered.result_key or "")
    outcome = {
        "attempt_one_status": first.status,
        "partial_result_published": first.result_key is not None,
        "recovered_status": recovered.status,
        "integrity": integrity,
    }
    restarted.dispose()
    assert recovered.status == "succeeded"
    assert integrity["zip_ok"]
    return outcome


def crash_before_result_commit(settings: Settings) -> dict[str, Any]:
    """Write one attempt-scoped result but crash before the fenced DB commit."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    job_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(2, "pre-commit"))
    _, stale = _claim(repo, settings)
    source = store.root / f".dm-drill-source-{uuid.uuid4()}.zip"
    write_archive_to_path(source, {"result.json": b'{"drill":true}'})
    stale_key = store.write_result_from_path(job_id, stale.lease_token, source)
    source.unlink()
    assert (store.root / stale_key).is_file()
    _sql_expire_lease(repo, job_id)
    repo.dispose()

    restarted = JobRepository(settings)
    _, current = _claim(restarted, settings)
    stale_publish = False
    with contextlib.suppress(IllegalTransitionError):
        restarted.succeed(job_id, stale_key, ttl_seconds=3600, claim=stale)
        stale_publish = True
    Worker(settings, restarted, store).run_one(job_id, current)
    job = restarted.get(job_id)
    integrity = result_integrity(store, job_id, job.result_key or "")
    outcome = {
        "stale_publish_accepted": stale_publish,
        "published_key_is_current": job.result_key == store.result_key(job_id, current.lease_token),
        "stale_artifact_still_present": (store.root / stale_key).exists(),
        "status": job.status,
        "integrity": integrity,
    }
    restarted.dispose()
    assert stale_publish is False
    assert outcome["published_key_is_current"]
    assert outcome["stale_artifact_still_present"] is False
    assert job.status == "succeeded"
    return outcome


def cross_job_artifact_isolation(settings: Settings) -> dict[str, Any]:
    """Verify a stale/failing worker for job B cannot remove job A's bytes."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    first_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "job-a"))
    _, first_claim = _claim(repo, settings)
    Worker(settings, repo, store).run_one(first_id, first_claim)
    first_job = repo.get(first_id)
    assert first_job.status == "succeeded"
    protected = store.path_for_result(first_job.result_key or "")
    before = protected.read_bytes()

    second_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "job-b"))
    _, second_claim = _claim(repo, settings)
    assert second_claim.lease_token != first_claim.lease_token
    _sql_expire_lease(repo, second_id)
    restarted = JobRepository(settings)
    outcome = restarted.fail(second_id, "worker_lost", retryable=True, max_attempts=3, claim=second_claim)
    assert outcome == OWNERSHIP_LOST
    # Even an explicit delete by the stale worker is id-scoped by ArtifactStore.
    store.delete(second_id)
    after = protected.read_bytes()
    result = {
        "stale_failure_outcome": outcome,
        "other_job_artifact_unchanged": before == after,
        "other_job_sha256": hashlib.sha256(after).hexdigest(),
    }
    restarted.dispose()
    assert result["other_job_artifact_unchanged"]
    return result


def simulate_sigterm(settings: Settings) -> dict[str, Any]:
    """Equivalent of the worker's SIGTERM handler: stop claims, drain in-flight."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    in_flight = enqueue_direct(
        settings, repo, store, "mask_file", data=build_docx(40, "sigterm"), filename="документ.docx"
    )
    queued_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "after-sigterm"))
    worker = Worker(settings, repo, store)
    original_claim = repo.claim_with_owner
    claims = 0

    def claim_then_sigterm(
        *, lease_seconds: float, concurrency: int, worker_id: str | None = None
    ) -> tuple[Job, Claim] | None:
        nonlocal claims
        claimed = original_claim(lease_seconds=lease_seconds, concurrency=concurrency, worker_id=worker_id)
        if claimed is not None:
            claims += 1
            worker._stop.set()
        return claimed

    repo.claim_with_owner = claim_then_sigterm  # type: ignore[method-assign]
    worker.run()
    running_job = repo.get(in_flight)
    idle_job = repo.get(queued_id)
    integrity = result_integrity(store, in_flight, running_job.result_key or "")
    outcome = {
        "claims_after_handler": claims,
        "in_flight_status": running_job.status,
        "queued_status": idle_job.status,
        "queued_job_has_result": idle_job.result_key is not None,
        "integrity": integrity,
    }
    repo.dispose()
    assert claims == 1
    assert running_job.status == "succeeded"
    assert idle_job.status == "queued"
    assert outcome["queued_job_has_result"] is False
    assert integrity["zip_ok"]
    return outcome


def simulate_disk_full(settings: Settings) -> dict[str, Any]:
    """Simulate ENOSPC at staging write and at ZIP creation (no real disk fill)."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    original_write = store.write

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "simulated ENOSPC")

    store.write = fail_write  # type: ignore[method-assign]
    try:
        with contextlib.suppress(OSError):
            enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "disk-full"))
    finally:
        store.write = original_write  # type: ignore[method-assign]
    rows = []
    with repo._engine.connect() as connection:
        rows = connection.execute(text("SELECT status, error_code FROM jobs ORDER BY created_at DESC LIMIT 1")).all()
    status, error_code = (str(rows[0][0]), str(rows[0][1])) if rows else ("none", "none")
    leftovers = [p for p in store.root.glob("*.input") if p.is_file()]
    outcome = {
        "staging_status": status,
        "staging_error_code": error_code,
        "staging_artifact_files": len(leftovers),
        "zip_window": crash_during_zip(settings),
    }
    repo.dispose()
    assert status == "failed"
    assert error_code == "staging_failed"
    assert leftovers == []
    return outcome


def simulate_pool_exhaustion(settings: Settings) -> dict[str, Any]:
    """Hold the single pool slot and measure bounded checkout/poll behaviour."""
    tight = settings.model_copy(update={"db_pool_size": 1, "db_max_overflow": 0})
    repo = JobRepository(tight)
    engine: Engine = repo._engine
    setattr(engine.pool, "_timeout", 0.2)  # noqa: B010 - harness-only short pool timeout
    worker = Worker(tight, repo, ArtifactStore(tight.jobs_dir))
    setup = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    job_id = enqueue_direct(settings, setup, store, "mask_text", data=synthetic_text(1, "pool"))
    setup.dispose()

    def timed_checkout() -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            ok = True
            error_type = "none"
        except Exception as error:  # noqa: BLE001 - type only, never logged as text
            ok = False
            error_type = type(error).__name__
        return {"seconds": round(time.perf_counter() - started, 4), "ok": ok, "error_type": error_type}

    held = engine.connect()
    exhausted = timed_checkout()
    poll_started = time.perf_counter()
    assert worker._claim_safe() is None
    poll_seconds = round(time.perf_counter() - poll_started, 4)
    held.close()
    recovered = timed_checkout()

    fresh = JobRepository(settings)
    _, claim = _claim(fresh, settings)
    Worker(settings, fresh, store).run_one(job_id, claim)
    job = fresh.get(job_id)
    outcome = {
        "pool_size": 1,
        "max_overflow": 0,
        "exhausted_checkout": exhausted,
        "worker_poll_seconds": poll_seconds,
        "recovered_checkout": recovered,
        "job_status_after_recovery": job.status,
        "integrity": result_integrity(store, job_id, job.result_key or ""),
    }
    fresh.dispose()
    assert exhausted["ok"] is False
    assert 0.2 <= exhausted["seconds"] < 2.0
    assert recovered["ok"] is True
    assert job.status == "succeeded"
    return outcome


def expiry_and_cleanup(settings: Settings) -> dict[str, Any]:
    """Sweep terminal metadata and artifact bytes; report orphans and misses."""
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    worker = Worker(settings, repo, store)

    first_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "expire-1"))
    _, claim = _claim(repo, settings)
    worker.run_one(first_id, claim)
    first = repo.get(first_id)
    assert first.result_key is not None
    _sql_expire_terminal(repo, first_id)

    second_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "expire-2"))
    _, second_claim = _claim(repo, settings)
    worker.run_one(second_id, second_claim)
    second = repo.get(second_id)
    assert second.result_key is not None
    (store.root / second.result_key).unlink()  # deliberately lose the bytes
    _sql_expire_terminal(repo, second_id)

    orphan = store.root / f"{uuid.uuid4()}.result.{uuid.uuid4()}"
    orphan.write_bytes(b"unreferenced-drill-bytes")
    active_id = enqueue_direct(settings, repo, store, "mask_text", data=synthetic_text(1, "active"))

    before = _spool_audit(store, repo)
    expired_selected = len(repo.expired_jobs(time.time()))
    worker.sweep()
    after = _spool_audit(store, repo)
    missing_rows: list[str] = []
    for job_id in (first_id, second_id):
        try:
            repo.get(job_id)
        except JobNotFoundError:
            missing_rows.append(job_id)
    active_status = repo.get(active_id).status
    with repo._engine.connect() as connection:
        remaining = [str(row[0]) for row in connection.execute(text("SELECT status FROM jobs")).all()]
    outcome = {
        "expired_rows_selected": expired_selected,
        "remaining_statuses": remaining,
        "rows_removed": len(missing_rows),
        "active_rows_swept": active_status != "queued",
        "swept_result_file_exists": (store.root / first.result_key).exists(),
        "missing_before_sweep": before["missing_artifacts"],
        "orphans_before_sweep": before["orphan_artifacts"],
        "missing_after_sweep": after["missing_artifacts"],
        "orphans_after_sweep": after["orphan_artifacts"],
        "active_status": active_status,
    }
    repo.dispose()
    assert outcome["rows_removed"] == 2
    assert outcome["active_rows_swept"] is False
    assert outcome["swept_result_file_exists"] is False
    assert before["missing_artifacts"] == 1
    assert before["orphan_artifacts"] == 1
    assert after["orphan_artifacts"] == 1  # row-driven GC: orphans are reported, not deleted
    return outcome


def _peak_rss_bytes() -> int | None:
    """Peak RSS via POSIX getrusage, or PeakWorkingSetSize on Windows."""
    if sys.platform != "win32":
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1 if platform.system() == "Darwin" else 1024))
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None

    class MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    get_handle = windll.kernel32.GetCurrentProcess
    get_handle.restype = ctypes.c_void_p
    info = windll.psapi.GetProcessMemoryInfo
    info.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryCounters), ctypes.c_ulong]
    info.restype = ctypes.c_int
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(MemoryCounters)
    ok = info(get_handle(), ctypes.byref(counters), counters.cb)
    return int(counters.PeakWorkingSetSize) if ok else None


def _pool_checkout_latency(repo: JobRepository) -> float:
    started = time.perf_counter()
    with repo._engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    return round(time.perf_counter() - started, 6)


def concurrency_batch(settings: Settings, sizes: tuple[int, ...], concurrency: int) -> dict[str, Any]:
    """Run 1/2/N synthetic DOCX jobs and measure resources without thresholds."""
    local = settings.model_copy(update={"jobs_concurrency": concurrency})
    repo = JobRepository(local)
    store = ArtifactStore(local.jobs_dir)
    worker = Worker(local, repo, store)
    ids = [
        enqueue_direct(
            local,
            repo,
            store,
            "mask_file",
            data=build_docx(size, f"batch-{size}-{index}"),
            filename=f"drill-{size}.docx",
        )
        for index, size in enumerate(sizes)
    ]
    queue_waits: list[float] = []
    original_claim = repo.claim_with_owner

    def timed_claim(
        *, lease_seconds: float, concurrency: int, worker_id: str | None = None
    ) -> tuple[Job, Claim] | None:
        claimed = original_claim(lease_seconds=lease_seconds, concurrency=concurrency, worker_id=worker_id)
        if claimed is not None:
            queue_waits.append(round(time.time() - claimed[0].created_at, 6))
        return claimed

    repo.claim_with_owner = timed_claim  # type: ignore[method-assign]
    pool_waits = [_pool_checkout_latency(repo)]
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    outcomes: dict[str, str] = {}
    pending = set(ids)
    from concurrent.futures import ThreadPoolExecutor

    while pending:
        batch: list[tuple[str, Claim]] = []
        for _ in range(concurrency):
            claimed = repo.claim_with_owner(
                lease_seconds=local.jobs_lease_seconds,
                concurrency=concurrency,
                worker_id=worker._worker_id,
            )
            if claimed is None:
                break
            batch.append((claimed[0].id, claimed[1]))
        if not batch:
            msg = "Queued drill jobs were not claimable."
            raise RuntimeError(msg)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker.run_one, job_id, claim) for job_id, claim in batch]
            for (job_id, _claim_item), future in zip(batch, futures, strict=True):
                future.result()
                pending.discard(job_id)
                outcomes[job_id] = repo.get(job_id).status
    wall = round(time.perf_counter() - wall_started, 6)
    cpu = round(time.process_time() - cpu_started, 6)
    pool_waits.append(_pool_checkout_latency(repo))
    integrity = [result_integrity(store, job_id, repo.get(job_id).result_key or "") for job_id in ids]
    outcome = {
        "documents": len(ids),
        "concurrency": concurrency,
        "synthetic_paragraphs": list(sizes),
        "wall_seconds": wall,
        "process_cpu_seconds": cpu,
        "peak_rss_bytes": _peak_rss_bytes(),
        "queue_wait_max_seconds": max(queue_waits, default=0.0),
        "db_pool_checkout_max_seconds": max(pool_waits),
        "outcomes": list(outcomes.values()),
        "artifact_integrity": integrity,
    }
    repo.dispose()
    assert set(outcomes.values()) == {"succeeded"}
    assert all(item["zip_ok"] for item in integrity)
    return outcome


def _config_digest_from_committed_files(config_dir: Path, destination: Path) -> str:
    """Digest only the committed synthetic config; never read private exceptions.yaml."""
    from src.masking.config import load_config

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_dir / "labels.yaml", destination / "labels.yaml")
    shutil.copy2(config_dir / "exceptions.example.yaml", destination / "exceptions.example.yaml")
    shutil.copytree(config_dir / "lexicons", destination / "lexicons", dirs_exist_ok=True)
    return load_config(destination).digest


def backup_restore_rehearsal(
    settings: Settings,
    *,
    container: str,
    config_dir: Path,
    workspace: Path,
) -> dict[str, Any]:
    """Disposable pg_dump / artifact copy / restore consistency rehearsal."""
    from sqlalchemy.engine import make_url

    if not container.startswith("dm-drill-"):
        msg = "Backup rehearsal requires a dm-drill-* disposable container."
        raise RuntimeError(msg)
    database = make_url(settings.sqlalchemy_database_url).database
    if not database or "drill" not in database:
        msg = "Backup rehearsal requires a disposable drill database."
        raise RuntimeError(msg)
    repo = JobRepository(settings)
    store = ArtifactStore(settings.jobs_dir)
    dump_name = "dm-drill.dump"

    def before_rows() -> list[tuple[str, str, str | None]]:
        with repo._engine.connect() as connection:
            rows = connection.execute(text("SELECT id::text, status, result_artifact FROM jobs ORDER BY id")).all()
        return [(str(row[0]), str(row[1]), None if row[2] is None else str(row[2])) for row in rows]

    rows_before = before_rows()
    hashes_before = {
        key: hashlib.sha256((store.root / key).read_bytes()).hexdigest()
        for _job_id, _status, key in rows_before
        if key is not None
    }
    config_before = _config_digest_from_committed_files(config_dir, workspace / "config-snapshot")
    repo.dispose()

    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"pg_dump -U dm -d {database} -Fc -f /tmp/{dump_name}",
        ],
        check=True,
        capture_output=True,
    )
    local_dump = workspace / dump_name
    subprocess.run(  # noqa: S603
        ["docker", "cp", f"{container}:/tmp/{dump_name}", str(local_dump)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    artifact_backup = workspace / "artifacts-backup"
    shutil.copytree(store.root, artifact_backup, dirs_exist_ok=True)

    engine = create_engine(settings.sqlalchemy_database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE jobs"))
    engine.dispose()
    for path in store.root.iterdir():
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    subprocess.run(  # noqa: S603
        ["docker", "cp", str(local_dump), f"{container}:/tmp/dm-drill-restore.dump"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"pg_restore -U dm -d {database} --clean --if-exists /tmp/dm-drill-restore.dump",
        ],
        check=True,
        capture_output=True,
    )
    shutil.copytree(artifact_backup, store.root, dirs_exist_ok=True)

    restored = JobRepository(settings)
    with restored._engine.connect() as connection:
        rows_after = [
            (str(row[0]), str(row[1]), None if row[2] is None else str(row[2]))
            for row in connection.execute(text("SELECT id::text, status, result_artifact FROM jobs ORDER BY id")).all()
        ]
    hashes_after = {
        key: hashlib.sha256((store.root / key).read_bytes()).hexdigest()
        for _job_id, _status, key in rows_after
        if key is not None
    }
    config_after = _config_digest_from_committed_files(config_dir, workspace / "config-snapshot-restored")
    outcome = {
        "environment": "local-disposable",
        "rows_before": len(rows_before),
        "rows_after": len(rows_after),
        "rows_identical": rows_before == rows_after,
        "artifacts_checked": len(hashes_before),
        "artifact_checksums_identical": hashes_before == hashes_after,
        "exception_config_digest_identical": config_before == config_after,
        "config_digest": config_after,
        "private_exception_values_touched": False,
    }
    restored.dispose()
    assert outcome["rows_identical"]
    assert outcome["artifact_checksums_identical"]
    assert outcome["exception_config_digest_identical"]
    return outcome

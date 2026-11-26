"""
Long-lived PostgreSQL worker (ADR-0003, Phase 2).

A bounded thread pool of warm maskers executes claimed jobs outside the web
event loop. Each claim carries a fresh fencing token; every heartbeat, phase
report, success, failure and cancellation acknowledgement is guarded by it.
A dedicated heartbeat thread renews the lease independently of the blocking
masking operation (§3.6), so a two-minute DOCX job cannot lose its lease.
SIGTERM stops new claims but keeps heartbeating in-flight jobs while they
drain; forced termination is recovered by lease expiry with bounded attempts.

Structured logs carry the opaque job id for every job being processed, while
metrics carry only bounded labels (kind and safe error code). Metrics emission
is best-effort: it never affects job execution or fencing.
"""

import contextlib
import logging
import os
import shutil
import signal
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app_logging import configure_application_logging, log_event, log_warning
from src.config import Settings, get_settings
from src.masking.archives import mark_cache_hit_file, write_archive_to_path
from src.metrics import (
    inc_lease_expired,
    inc_lease_renew_failure,
    mark_worker_heartbeat,
    observe_job_duration,
    observe_job_failed,
    observe_job_queue_age,
    observe_job_retried,
    observe_job_succeeded,
    safe_error_code,
    safe_kind,
    set_artifact_disk_free,
    set_job_running,
)

from . import dependencies
from .dependencies import artifact_store, job_repository
from .metrics import start_metrics_server, stop_metrics_server
from .models import Claim, Job, JobKind, JobPhase
from .operations import JobExecutionError, execute
from .repository import OWNERSHIP_LOST, JobRepository
from .storage import ArtifactStore, cleanup_artifacts

LOGGER = logging.getLogger("docmasker.worker")


class LeaseLostError(RuntimeError):
    """The job no longer belongs to this worker (cancelled / lease expired)."""


class CancelledError(RuntimeError):
    """A cancellation request was observed at a cooperative checkpoint."""


@dataclass(frozen=True, slots=True)
class ErrorClass:
    """Classified failure: safe code, retry policy and backoff (§3.8)."""

    code: str
    retryable: bool


#: Deterministic input/format/config errors are never retried; transient I/O
#: and worker-loss errors are retried with bounded exponential backoff.
RETRYABLE_CODES = frozenset(
    {
        "dependency_unavailable",
        "artifact_io",
        "worker_lost",
        "internal_error",
    }
)
NON_RETRYABLE_CODES = frozenset(
    {
        "invalid_input",
        "invalid_keys",
        "invalid_package",
        "unsupported_operation",
        "unknown_kind",
        "invalid_configuration",
        "cancelled",
        "queue_timeout",
    }
)


def classify(error: Exception) -> ErrorClass:
    """Map an exception to a safe, bounded retry policy (never leak text)."""
    if isinstance(error, JobExecutionError):
        code = error.code
        retryable = error.retryable and code in RETRYABLE_CODES
        return ErrorClass(code, retryable=retryable)
    if isinstance(error, (CancelledError, LeaseLostError)):
        return ErrorClass("cancelled", retryable=False)
    # Unexpected internal errors get at most one automatic retry (§3.8).
    return ErrorClass("internal_error", retryable=True)


class Heartbeat:
    """Independent lease renewal on a lightweight thread (§3.6)."""

    def __init__(self, repo: JobRepository, job_id: str, claim: Claim, settings: Settings) -> None:
        self._repo = repo
        self._job_id = job_id
        self._claim = claim
        self._interval = max(0.1, settings.jobs_lease_seconds / 3)
        self._lease_seconds = settings.jobs_lease_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"dm-heartbeat-{job_id[:8]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def lost(self) -> bool:
        return self._lost.is_set()

    def mark_lost(self) -> None:
        self._lost.set()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval * 2 + 1)

    def _run(self) -> None:
        # Renew immediately: a slow first claim must not start with a stale lease.
        # A transient DB outage must not silently kill the heartbeat thread —
        # retry on the same interval; fencing will reject a stale worker.
        while not self._stop.is_set():
            try:
                ok = self._repo.renew_lease(
                    self._job_id,
                    lease_seconds=self._lease_seconds,
                    claim=self._claim,
                )
            except Exception as error:  # noqa: BLE001 - log only the exception type
                inc_lease_renew_failure()
                log_warning(
                    LOGGER,
                    "lease_renew_failed",
                    "Lease renewal failed.",
                    job_id=self._job_id,
                    operation="heartbeat",
                    error_type=type(error).__name__,
                )
                ok = True  # a transient DB failure is not proof of lost ownership
            if not ok:
                inc_lease_expired()
                log_warning(
                    LOGGER,
                    "lease_expired",
                    "Lease renewal rejected.",
                    job_id=self._job_id,
                    operation="heartbeat",
                    outcome="error",
                )
                self._lost.set()
                return
            mark_worker_heartbeat()
            self._stop.wait(self._interval)


class Worker:
    """Poll → claim → execute (fenced) → publish → sweep, with graceful drain."""

    def __init__(self, settings: Settings, repo: JobRepository, store: ArtifactStore) -> None:
        self._settings = settings
        self._repo = repo
        self._store = store
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(
            max_workers=settings.jobs_concurrency,
            thread_name_prefix="dm-job",
        )
        self._futures: set[Future[None]] = set()
        self._futures_lock = threading.Lock()
        self._worker_id = str(uuid.uuid4())

    def _checkpoint(self, job_id: str, claim: Claim, heartbeat: Heartbeat) -> None:
        """Cooperative cancellation / ownership checkpoint (§3.7)."""
        if heartbeat.lost():
            # Lease/cancellation audit is a distinct path: the old worker must
            # not acknowledge cancellation or clean up shared artifacts.
            raise LeaseLostError(job_id)
        if self._repo.cancellation_requested(job_id, claim):
            raise CancelledError

    def _progress(
        self,
        job_id: str,
        claim: Claim,
        heartbeat: Heartbeat,
    ) -> Callable[[JobPhase, int], None]:
        """Monotonic phase/progress reporting (separate from lease renewal), fenced."""

        def report(phase: JobPhase, progress: int) -> None:
            self._checkpoint(job_id, claim, heartbeat)
            outcome = self._repo.report_progress(job_id, phase, progress, claim=claim)
            if outcome == "cancelled":
                raise CancelledError
            if outcome == OWNERSHIP_LOST:
                inc_lease_renew_failure()
                heartbeat.mark_lost()
                log_warning(
                    LOGGER,
                    "lease_renew_failed",
                    "Phase renewal rejected.",
                    job_id=job_id,
                    operation="heartbeat",
                    outcome="error",
                )
                raise LeaseLostError(job_id)
            mark_worker_heartbeat()

        return report

    def run_one(self, job_id: str, claim: Claim, kind: JobKind | None = None) -> None:
        """Execute one claimed job end-to-end (runs inside a worker thread)."""
        kind_label = safe_kind(kind) if kind is not None else "unmatched"
        started = time.perf_counter()
        running = False
        result_path: Path | None = None
        entries: dict[str, Any] = {}
        heartbeat = Heartbeat(self._repo, job_id, claim, self._settings)
        heartbeat.start()
        try:
            self._checkpoint(job_id, claim, heartbeat)
            cache = dependencies.result_cache()
            current = self._repo.get(job_id)
            kind_label = safe_kind(current.kind)
            set_job_running(kind=kind_label, running=True)
            running = True
            log_event(
                LOGGER,
                "job_started",
                "Job started.",
                job_id=job_id,
                worker_id=self._worker_id,
                kind=kind_label,
                operation="execute",
                attempt=current.attempt,
                outcome="ok",
            )
            cache_key = current.cache_key
            result_path = _temp_result_path(self._store.root)
            from_cache = bool(cache_key and cache.get_to_path(cache_key, result_path))
            if from_cache:
                # A verified cache hit still goes through the fenced publish:
                # no cancelled/stale worker can expose it.
                self._checkpoint(job_id, claim, heartbeat)
                mark_cache_hit_file(result_path)
            else:
                entries = execute(
                    self._store,
                    job_id,
                    progress=self._progress(job_id, claim, heartbeat),
                    checkpoint=lambda: self._checkpoint(job_id, claim, heartbeat),
                )
                self._checkpoint(job_id, claim, heartbeat)
                write_archive_to_path(result_path, entries)
            # Publish to an attempt-scoped immutable key from a streamed file:
            # a stale worker cannot overwrite a newer claim's result.
            result_key = self._store.write_result_from_path(job_id, claim.lease_token, result_path)
            self._repo.succeed(
                job_id,
                result_key,
                ttl_seconds=self._settings.jobs_ttl_seconds,
                claim=claim,
            )
            self._store.delete_attempt_artifacts(job_id, keep_key=result_key)
            observe_job_succeeded(kind_label)
            # Populate the cache only after the fenced succeeded transition
            # was accepted (§4.5); a stale/cancelled job never caches.
            if cache_key and not from_cache:
                cache.put_from_path(cache_key, result_path)
            log_event(
                LOGGER,
                "job_succeeded",
                "Job succeeded from cache." if from_cache else "Job succeeded.",
                job_id=job_id,
                kind=kind_label,
                operation="execute",
                outcome="ok",
            )
        except CancelledError:
            # Only after PostgreSQL confirms the terminal cancelled state may
            # this owner schedule cleanup. Otherwise leave every artifact for
            # the current owner or GC.
            confirmed = self._repo.acknowledge_cancel(job_id, claim)
            if confirmed:
                self._store.delete(job_id)
            log_event(
                LOGGER,
                "job_cancelled",
                "Job cancellation acknowledged." if confirmed else "Cancellation not owned; no cleanup.",
                job_id=job_id,
                kind=kind_label,
                worker_id=self._worker_id,
                operation="execute",
                outcome="cancelled" if confirmed else "ownership_lost",
            )
        except LeaseLostError:
            # A new owner (or lease recovery) owns the job now. Never delete,
            # acknowledge cancellation, retry or publish from this worker.
            log_event(
                LOGGER,
                "lease_lost",
                "Lease lost; discarding work without touching artifacts.",
                job_id=job_id,
                kind=kind_label,
                worker_id=self._worker_id,
                operation="execute",
                outcome="ownership_lost",
            )
        except JobExecutionError as error:
            self._fail(job_id, claim, classify(error), kind_label)
        except Exception:  # noqa: BLE001 - never leak exception text / PII to logs
            log_warning(
                LOGGER,
                "job_failed",
                "Job failed.",
                job_id=job_id,
                kind=kind_label,
                operation="execute",
                error_code="internal_error",
                outcome="error",
            )
            self._fail(job_id, claim, ErrorClass("internal_error", retryable=True), kind_label)
        finally:
            heartbeat.stop()
            for payload in entries.values():
                if isinstance(payload, Path):
                    with contextlib.suppress(OSError):
                        payload.unlink()
            if result_path is not None:
                with contextlib.suppress(OSError):
                    result_path.unlink()
            if running:
                set_job_running(kind=kind_label, running=False)
            observe_job_duration(kind_label, time.perf_counter() - started)

    def _fail(self, job_id: str, claim: Claim, error: ErrorClass, kind: object = None) -> None:
        kind_label = safe_kind(kind)
        attempt = self._repo.get(job_id).attempt
        # First retry is immediate; subsequent ones use bounded exponential
        # backoff with a hard cap (§3.8). Jitter is omitted for determinism.
        backoff = min(2 ** (attempt - 2), 30.0) if error.retryable and attempt >= 2 else 0.0
        status = self._repo.fail(
            job_id,
            error.code,
            retryable=error.retryable,
            max_attempts=self._settings.jobs_max_attempts,
            claim=claim,
            backoff_seconds=backoff,
        )
        if status == OWNERSHIP_LOST:
            log_warning(
                LOGGER,
                "ownership_lost",
                "Failure rejected: ownership lost.",
                job_id=job_id,
                operation="execute",
                error_code=safe_error_code(error.code),
                outcome="ownership_lost",
            )
            return  # no retry, no artifact deletion, no cache write
        if status == "queued":
            observe_job_retried(kind_label)
            log_event(
                LOGGER,
                "job_retried",
                "Job scheduled for retry.",
                job_id=job_id,
                kind=kind_label,
                worker_id=self._worker_id,
                operation="execute",
                error_code=safe_error_code(error.code),
                attempt=attempt,
                outcome="retry",
            )
        if status == "failed":
            observe_job_failed(kind=kind_label, error_code=error.code)
            log_warning(
                LOGGER,
                "job_failed",
                "Job reached terminal failure.",
                job_id=job_id,
                kind=kind_label,
                worker_id=self._worker_id,
                operation="execute",
                error_code=safe_error_code(error.code),
                attempt=attempt,
                outcome="error",
            )
            # Failed rows keep metadata for diagnostics, but never input/keys.
            self._store.delete(job_id)

    def _refresh_runtime_metrics(self) -> None:
        """Queue depth and free artifact space; never fatal on a DB/disk outage."""
        try:
            self._repo.count_active()
        except Exception as error:  # noqa: BLE001 - log only the exception type
            log_warning(
                LOGGER,
                "queue_sample_failed",
                "Queue sample unavailable.",
                operation="sampler",
                error_type=type(error).__name__,
            )
        try:
            usage = shutil.disk_usage(self._settings.jobs_dir)
        except OSError as error:
            log_warning(
                LOGGER,
                "artifact_disk_sample_failed",
                "Artifact disk sample unavailable.",
                operation="sampler",
                error_type=type(error).__name__,
            )
        else:
            set_artifact_disk_free(usage.free)

    def sweep(self) -> None:
        """Recover abandoned work, then remove eligible terminal artifacts (§3.9)."""
        # P0.2: crash recovery is part of worker maintenance, not an unused
        # repository helper. Both methods atomically claim only stale rows.
        abandoned = self._repo.recover_abandoned_staging()
        cancelled = self._repo.recover_expired_cancelling()
        recovered = [*abandoned, *cancelled]
        if recovered:
            confirmed = []
            for job_id in recovered:
                try:
                    job = self._repo.get(job_id)
                except Exception as error:  # noqa: BLE001 - row may have been removed by GC
                    log_warning(
                        LOGGER,
                        "recovery_row_missing",
                        "Recovered job row disappeared before cleanup.",
                        operation="gc",
                        error_type=type(error).__name__,
                    )
                    continue
                if job.status in ("failed", "cancelled"):
                    confirmed.append(job)
            if confirmed:
                cleanup_artifacts(self._store, confirmed)
                log_event(
                    LOGGER,
                    "abandoned_jobs_recovered",
                    "Abandoned staging/cancelling jobs recovered.",
                    operation="gc",
                    outcome="ok",
                    recovered=len(confirmed),
                )
        self._repo.expire_queued(queue_wait_seconds=self._settings.jobs_ttl_seconds)
        self._repo.mark_terminal_retention(retention_seconds=self._settings.jobs_ttl_seconds)
        expired = self._repo.expired_jobs(time.time())
        if expired:
            cleanup_artifacts(self._store, expired)
            self._repo.remove_expired([job.id for job in expired])
            log_event(
                LOGGER,
                "expired_jobs_swept",
                "Expired jobs swept.",
                operation="gc",
                outcome="ok",
            )
        self._refresh_runtime_metrics()

    def _sweep_safe(self) -> None:
        """GC must never kill the worker on a transient DB outage (§3.9)."""
        try:
            self.sweep()
        except Exception as error:  # noqa: BLE001 - log only the exception type
            log_warning(
                LOGGER,
                "worker_sweep_failed",
                "Worker sweep failed.",
                operation="gc",
                error_type=type(error).__name__,
            )

    def _cache_gc_safe(self) -> None:
        """Periodic cache-artifact GC; outages are harmless no-ops."""
        try:
            report = dependencies.result_cache().gc()
            if report.deleted:
                log_event(
                    LOGGER,
                    "cache_artifacts_collected",
                    "Stale cache artifacts collected.",
                    operation="gc",
                    outcome="ok",
                    deleted=report.deleted,
                )
        except Exception as error:  # noqa: BLE001 - log only the exception type
            log_warning(
                LOGGER,
                "worker_cache_gc_failed",
                "Cache artifact GC failed.",
                operation="gc",
                error_type=type(error).__name__,
            )

    def _claim_safe(self) -> tuple[Job, Claim] | None:
        """One poll attempt; transient PostgreSQL errors become an empty poll."""
        try:
            return self._repo.claim_with_owner(
                lease_seconds=self._settings.jobs_lease_seconds,
                concurrency=self._settings.jobs_concurrency,
                worker_id=self._worker_id,
            )
        except Exception as error:  # noqa: BLE001 - never log DB messages or PII
            log_warning(
                LOGGER,
                "worker_claim_failed",
                "Worker claim failed.",
                operation="poll",
                error_type=type(error).__name__,
            )
            return None

    def run(self) -> None:
        """Poll loop until SIGTERM; bounded by JOBS_CONCURRENCY."""
        log_event(
            LOGGER,
            "worker_started",
            "Worker started.",
            worker_id=self._worker_id,
            operation="poll",
            outcome="ok",
        )
        next_sweep = time.monotonic() + min(self._settings.jobs_ttl_seconds, 60.0)
        next_cache_gc = time.monotonic() + self._settings.cache_gc_interval_seconds
        while not self._stop.is_set():
            mark_worker_heartbeat()  # liveness even while the queue is empty
            if time.monotonic() >= next_sweep:
                self._sweep_safe()
                next_sweep = time.monotonic() + min(self._settings.jobs_ttl_seconds, 60.0)
            if time.monotonic() >= next_cache_gc:
                self._cache_gc_safe()
                next_cache_gc = time.monotonic() + self._settings.cache_gc_interval_seconds
            claimed = self._claim_safe()
            if claimed is None:
                # Also the backoff after a DB outage: no tight hot loop, and a
                # database restart is recovered by the next poll (fencing
                # protects jobs already running).
                self._stop.wait(self._settings.jobs_poll_seconds)
                continue
            job, claim = claimed
            age = max(0.0, time.time() - job.created_at)
            observe_job_queue_age(job.kind, age)
            future = self._pool.submit(self.run_one, job.id, claim, job.kind)
            with self._futures_lock:
                self._futures = {f for f in self._futures if not f.done()}
                self._futures.add(future)
        # SIGTERM: stop claiming, keep heartbeats alive while in-flight work
        # drains (never cancel CPU threads and pretend they were stopped).
        log_event(
            LOGGER,
            "worker_draining",
            "Draining worker pool.",
            worker_id=self._worker_id,
            operation="drain",
        )
        self._pool.shutdown(wait=True, cancel_futures=False)
        for future in self._futures:
            try:
                future.result(timeout=0)
            except Exception:  # noqa: BLE001 - outcome already recorded in run_one
                log_warning(LOGGER, "drain_failed", "In-flight job finished with an error.", operation="drain")
        self._sweep_safe()
        self._cache_gc_safe()
        log_event(LOGGER, "worker_stopped", "Worker stopped.", worker_id=self._worker_id, operation="poll")


def _temp_result_path(root: Path) -> Path:
    """Create a free path inside the private spool for a streamed result ZIP."""
    handle, name = tempfile.mkstemp(prefix=".dm-result-", suffix=".zip", dir=str(root))
    os.close(handle)
    Path(name).unlink()
    return Path(name)


def _zip_entries(entries: dict[str, Any]) -> bytes:
    """Bundle every result artifact into one flat ZIP (never nested archives)."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def main() -> None:
    """Worker entrypoint (module run: python -m src.jobs.worker)."""
    configure_application_logging()
    settings = get_settings()
    worker = Worker(settings, job_repository(), artifact_store())
    server = start_metrics_server(host=settings.worker_metrics_host, port=settings.worker_metrics_port)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: worker._stop.set())  # noqa: SLF001
    signal.signal(signal.SIGINT, lambda _signum, _frame: worker._stop.set())  # noqa: SLF001
    try:
        worker.run()
    finally:
        stop_metrics_server(server)


if __name__ == "__main__":
    main()

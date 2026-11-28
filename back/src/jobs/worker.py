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
import signal
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app_logging import configure_application_logging, log_error, log_event, log_warning
from src.config import Settings, get_settings
from src.masking.archives import mark_cache_hit_file, write_archive_to_path
from src.metrics import (
    inc_lease_renew_failure,
    mark_worker_heartbeat,
    observe_job_duration,
    observe_job_failed,
    observe_job_queue_age,
    observe_job_retried,
    observe_job_succeeded,
    safe_error_code,
    safe_kind,
    set_job_running,
)

from . import dependencies
from .dependencies import artifact_store, job_repository
from .heartbeat import Heartbeat
from .maintenance import WorkerMaintenanceMixin, _temp_result_path
from .metrics import start_metrics_server, stop_metrics_server
from .models import Claim, JobKind, JobPhase
from .operations import JobExecutionError, execute
from .readiness import ArtifactStorageError, check_artifact_storage
from .repository import OWNERSHIP_LOST, JobRepository
from .storage import ArtifactStore

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


class Worker(WorkerMaintenanceMixin):
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

    def run(self) -> None:
        """Poll loop until SIGTERM; bounded by JOBS_CONCURRENCY."""
        # Startup gate: an unwritable or full spool cannot publish any result,
        # so fail fast before the poll loop or any job claim.
        check_artifact_storage(self._settings.jobs_dir, self._settings.jobs_min_free_bytes)
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
    except ArtifactStorageError as error:
        # Typed startup failure: log the safe code only, then exit non-zero.
        # The orchestrator restarts after the volume is fixed; no traceback
        # with paths or exception text is emitted.
        log_error(
            LOGGER,
            "worker_startup_failed",
            "Worker startup aborted: artifact storage not ready.",
            error_code=error.code,
            outcome="error",
        )
        raise SystemExit(1) from error
    finally:
        stop_metrics_server(server)


if __name__ == "__main__":
    main()

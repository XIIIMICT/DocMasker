"""
Long-lived PostgreSQL worker (ADR-0003, Phase 2).

A bounded thread pool of warm maskers executes claimed jobs outside the web
event loop. Each claim carries a fresh fencing token; every heartbeat, phase
report, success, failure and cancellation acknowledgement is guarded by it.
A dedicated heartbeat thread renews the lease independently of the blocking
masking operation (§3.6), so a two-minute DOCX job cannot lose its lease.
SIGTERM stops new claims but keeps heartbeating in-flight jobs while they
drain; forced termination is recovered by lease expiry with bounded attempts.
"""

import logging
import signal
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from src.config import Settings, get_settings

from . import dependencies
from .dependencies import artifact_store, job_repository
from .models import Claim, JobPhase
from .operations import JobExecutionError, execute
from .repository import JobRepository
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
        while not self._stop.is_set():
            ok = self._repo.heartbeat(
                self._job_id,
                "detection",
                0,
                lease_seconds=self._lease_seconds,
                claim=self._claim,
            )
            if not ok:
                self._lost.set()
                return
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
        if heartbeat.lost() or self._repo.cancellation_requested(job_id, claim):
            raise CancelledError

    def _progress(
        self,
        job_id: str,
        claim: Claim,
        heartbeat: Heartbeat,
    ) -> Callable[[JobPhase, int], None]:
        """Phase/progress reporting (separate from lease renewal), fenced."""

        def report(phase: JobPhase, progress: int) -> None:
            self._checkpoint(job_id, claim, heartbeat)
            ok = self._repo.heartbeat(
                job_id,
                phase,
                progress,
                lease_seconds=self._settings.jobs_lease_seconds,
                claim=claim,
            )
            if not ok:
                heartbeat.mark_lost()
                raise LeaseLostError(job_id)

        return report

    def run_one(self, job_id: str, claim: Claim) -> None:
        """Execute one claimed job end-to-end (runs inside a worker thread)."""
        heartbeat = Heartbeat(self._repo, job_id, claim, self._settings)
        heartbeat.start()
        try:
            self._checkpoint(job_id, claim, heartbeat)
            cache = dependencies.result_cache()
            cache_key = self._repo.get(job_id).cache_key
            cached = cache.get(cache_key) if cache_key else None
            if cached is not None:
                # A verified cache hit still goes through the fenced publish:
                # no cancelled/stale worker can expose it.
                self._checkpoint(job_id, claim, heartbeat)
                self._store.write(job_id, "result", cached)
                self._repo.succeed(
                    job_id,
                    "result",
                    ttl_seconds=self._settings.jobs_ttl_seconds,
                    claim=claim,
                )
                return
            entries = execute(
                self._store,
                job_id,
                progress=self._progress(job_id, claim, heartbeat),
                checkpoint=lambda: self._checkpoint(job_id, claim, heartbeat),
            )
            self._checkpoint(job_id, claim, heartbeat)
            result = _zip_entries(entries)
            # Immutable result: publish the artifact, then attach its reference
            # in a fenced DB transaction. A stale worker never exposes output.
            self._store.write(job_id, "result", result)
            self._repo.succeed(
                job_id,
                "result",
                ttl_seconds=self._settings.jobs_ttl_seconds,
                claim=claim,
            )
            # Populate the cache only after the fenced succeeded transition
            # was accepted (§4.5); a stale/cancelled job never caches.
            if cache_key:
                cache.put(cache_key, result)
        except CancelledError, LeaseLostError:
            LOGGER.info("job %s: cancelled or lease lost, discarding result", job_id)
            self._repo.acknowledge_cancel(job_id, claim)
            self._store.delete(job_id)
        except JobExecutionError as error:
            self._fail(job_id, claim, classify(error))
        except Exception:  # noqa: BLE001 - never leak exception text / PII to logs
            LOGGER.warning("job %s failed (error_code=internal_error)", job_id)
            self._fail(job_id, claim, ErrorClass("internal_error", retryable=True))
        finally:
            heartbeat.stop()

    def _fail(self, job_id: str, claim: Claim, error: ErrorClass) -> None:
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
        if status == "failed":
            # Failed rows keep metadata for diagnostics, but never input/keys.
            self._store.delete(job_id)

    def sweep(self) -> None:
        """Remove eligible terminal rows and their artifacts (§3.9, GC)."""
        self._repo.expire_queued(queue_wait_seconds=self._settings.jobs_ttl_seconds)
        self._repo.mark_terminal_retention(retention_seconds=self._settings.jobs_ttl_seconds)
        expired = self._repo.expired_jobs(time.time())
        if expired:
            cleanup_artifacts(self._store, expired)
            self._repo.remove_expired([job.id for job in expired])
            LOGGER.info("swept %d expired jobs", len(expired))

    def run(self) -> None:
        """Poll loop until SIGTERM; bounded by JOBS_CONCURRENCY."""
        LOGGER.info("worker %s started (concurrency=%d)", self._worker_id[:8], self._settings.jobs_concurrency)
        next_sweep = time.monotonic() + min(self._settings.jobs_ttl_seconds, 60.0)
        while not self._stop.is_set():
            if time.monotonic() >= next_sweep:
                self.sweep()
                next_sweep = time.monotonic() + min(self._settings.jobs_ttl_seconds, 60.0)
            claimed = self._repo.claim_with_owner(
                lease_seconds=self._settings.jobs_lease_seconds,
                concurrency=self._settings.jobs_concurrency,
                worker_id=self._worker_id,
            )
            if claimed is None:
                self._stop.wait(self._settings.jobs_poll_seconds)
                continue
            job, claim = claimed
            future = self._pool.submit(self.run_one, job.id, claim)
            with self._futures_lock:
                self._futures = {f for f in self._futures if not f.done()}
                self._futures.add(future)
        # SIGTERM: stop claiming, keep heartbeats alive while in-flight work
        # drains (never cancel CPU threads and pretend they were stopped).
        LOGGER.info("draining worker pool (%d futures)", len(self._futures))
        self._pool.shutdown(wait=True, cancel_futures=False)
        for future in self._futures:
            try:
                future.result(timeout=0)
            except Exception:  # noqa: BLE001 - outcome already recorded in run_one
                LOGGER.warning("in-flight job finished with an error while draining")
        self.sweep()
        LOGGER.info("worker stopped")


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    worker = Worker(settings, job_repository(), artifact_store())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: worker._stop.set())  # noqa: SLF001
    signal.signal(signal.SIGINT, lambda _signum, _frame: worker._stop.set())  # noqa: SLF001
    worker.run()


if __name__ == "__main__":
    main()

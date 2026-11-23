"""
Long-lived worker process (ADR-0002).

A bounded thread pool of warm maskers executes claimed jobs outside the web
event loop. Each thread owns its lru-cached masker (natasha's MorphVocab is
not proven thread-safe). Leases are renewed through `progress()`; if a job is
cancelled or its lease is lost, the callback raises `LeaseLost` and the thread
stops promptly. SIGTERM drains in-flight work before exit.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from src.config import Settings, get_settings

from .dependencies import artifact_store, job_repository
from .models import JobPhase
from .operations import JobExecutionError, execute
from .repository import JobRepository
from .storage import ArtifactStore, cleanup_artifacts

LOGGER = logging.getLogger("docmasker.worker")


class LeaseLostError(RuntimeError):
    """The job no longer belongs to this worker (cancelled / lease expired)."""


class Worker:
    """Poll → claim → execute → heartbeat → succeed/fail, with TTL sweep."""

    def __init__(self, settings: Settings, repo: JobRepository, store: ArtifactStore) -> None:
        self._settings = settings
        self._repo = repo
        self._store = store
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(
            max_workers=settings.jobs_concurrency,
            thread_name_prefix="dm-job",
        )

    def _progress(self, job_id: str) -> Callable[[JobPhase, int], None]:
        """Build a phase/progress callback that renews the lease (or aborts)."""
        settings = self._settings

        def report(phase: JobPhase, progress: int) -> None:
            if self._stop.is_set():
                raise LeaseLostError(job_id)
            ok = self._repo.heartbeat(
                job_id,
                phase,
                progress,
                lease_seconds=settings.jobs_lease_seconds,
            )
            if not ok:
                raise LeaseLostError(job_id)

        return report

    def run_one(self, job_id: str) -> None:
        """Execute one claimed job end-to-end (runs inside a worker thread)."""
        try:
            entries = execute(self._store, job_id, progress=self._progress(job_id))
            result = _zip_entries(entries)
            self._store.write(job_id, "result", result)
            self._repo.succeed(job_id, "result", ttl_seconds=self._settings.jobs_ttl_seconds)
        except LeaseLostError:
            LOGGER.info("job %s: lease lost, stopping", job_id)
        except JobExecutionError as error:
            self._repo.fail(
                job_id,
                error.code,
                retryable=error.retryable,
                max_attempts=self._settings.jobs_max_attempts,
            )
        except Exception:  # noqa: BLE001 - never leak exception text / PII to logs
            LOGGER.warning("job %s failed (error_code=internal_error)", job_id)
            self._repo.fail(
                job_id,
                "internal_error",
                retryable=True,
                max_attempts=self._settings.jobs_max_attempts,
            )

    def sweep(self) -> None:
        """Remove expired rows and their artifacts (crash-safe cleanup)."""
        expired = self._repo.expired_jobs(time.time())
        if expired:
            cleanup_artifacts(self._store, expired)
            self._repo.remove_expired([job.id for job in expired])
            LOGGER.info("swept %d expired jobs", len(expired))

    def run(self) -> None:
        """Run the poll loop until SIGTERM; bounded by JOBS_CONCURRENCY."""
        LOGGER.info("worker started (concurrency=%d)", self._settings.jobs_concurrency)
        next_sweep = time.monotonic() + self._settings.jobs_ttl_seconds
        while not self._stop.is_set():
            if time.monotonic() >= next_sweep:
                self.sweep()
                next_sweep = time.monotonic() + self._settings.jobs_ttl_seconds
            job = self._repo.claim(
                lease_seconds=self._settings.jobs_lease_seconds,
                concurrency=self._settings.jobs_concurrency,
            )
            if job is None:
                self._stop.wait(self._settings.jobs_poll_seconds)
                continue
            self._pool.submit(self.run_one, job.id)
        LOGGER.info("draining worker pool")
        self._pool.shutdown(wait=True, cancel_futures=True)
        self.sweep()


def _zip_entries(entries: dict[str, bytes]) -> bytes:
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

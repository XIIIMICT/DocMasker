"""
Worker maintenance: crash recovery, TTL/GC, sampling and safe polling.

These methods are a mixin over a worker-like object (``_repo``, ``_settings``,
``_store``, ``_worker_id``) so the poll loop in ``worker.py`` stays focused on
job execution. Every pass is best-effort: a transient PostgreSQL or disk outage
is logged by exception type only and never stops the worker.
"""

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from src.app_logging import log_event, log_warning
from src.config import Settings
from src.metrics import set_artifact_disk_free

from . import dependencies
from .models import Claim, Job
from .repository import JobRepository
from .storage import ArtifactStore, cleanup_artifacts

LOGGER = logging.getLogger("docmasker.worker")


class WorkerMaintenanceMixin:
    """Maintenance passes driven by the worker's poll loop."""

    _settings: Settings
    _repo: JobRepository
    _store: ArtifactStore
    _worker_id: str

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

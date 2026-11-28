"""
Independent lease renewal for an in-flight job (§3.6).

The heartbeat thread renews only the lease/heartbeat timestamp; phase and
progress are reported separately through the fenced worker callback. It keeps
renewing while a cancellation is being acknowledged, so a cancelled job is not
misreported as an ownership loss.
"""

import logging
import threading

from src.app_logging import log_warning
from src.config import Settings
from src.metrics import inc_lease_expired, inc_lease_renew_failure, mark_worker_heartbeat

from .models import Claim
from .repository import JobRepository

LOGGER = logging.getLogger("docmasker.worker")


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

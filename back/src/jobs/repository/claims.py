"""Claim, fencing, heartbeat, progress and cancellation operations."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from src.jobs.models import PHASE_ORDER, Claim, Job, JobPhase
from src.jobs.schema import jobs
from src.metrics import inc_lease_expired

from ._base import OWNERSHIP_LOST, JobNotFoundError, RepositoryBase, _affected


class ClaimsMixin(RepositoryBase):
    """Ownership operations guarded by worker id, lease token and live lease."""

    def claim(self, *, lease_seconds: float, concurrency: int) -> Job | None:
        """Atomically claim one job (legacy shape, for API/contract tests)."""
        pair = self.claim_with_owner(lease_seconds=lease_seconds, concurrency=concurrency)
        return pair[0] if pair else None

    def claim_with_owner(
        self,
        *,
        lease_seconds: float,
        concurrency: int,
        worker_id: str | None = None,
    ) -> tuple[Job, Claim] | None:
        """
        Atomically claim the oldest eligible job with `FOR UPDATE SKIP LOCKED`.

        One short transaction: reclaim expired leases (fresh token on the next
        claim), bound running jobs, then lock and claim one `queued` row with a
        new `uuid4()` fencing token + `worker_id`. `staging` rows are never
        claimable. Returns the ownership credentials every later mutation needs.
        """
        # Recovery runs before each claim so a crash leaves no permanently
        # active staging/cancelling row even when no sweep has fired yet.
        self.recover_abandoned_staging()
        self.recover_expired_cancelling()
        worker = worker_id or str(uuid.uuid4())
        with self._session() as session, session.begin():
            now = self._db_now(session)
            # Lease recovery: a crashed worker's running row returns to queued
            # (or fails when attempts are exhausted). No separate SELECT+UPDATE.
            stale = session.execute(
                select(jobs.c.id, jobs.c.attempt, jobs.c.max_attempts)
                .where(jobs.c.status == "running", jobs.c.lease_until <= now)
                .with_for_update(skip_locked=True)
            ).all()
            for record in stale:
                inc_lease_expired()
                if record.attempt >= record.max_attempts:
                    session.execute(
                        update(jobs)
                        .where(jobs.c.id == record.id, jobs.c.status == "running")
                        .values(
                            status="failed",
                            error_code="attempts_exhausted",
                            lease_token=None,
                            lease_until=None,
                            finished_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    session.execute(
                        update(jobs)
                        .where(jobs.c.id == record.id, jobs.c.status == "running")
                        .values(
                            status="queued",
                            phase="queued",
                            lease_token=None,
                            lease_until=None,
                            error_code="worker_lost",
                            updated_at=now,
                        )
                    )
            running = session.execute(
                select(func.count()).select_from(jobs).where(jobs.c.status == "running")
            ).scalar_one()
            if running >= concurrency:
                return None
            row = session.execute(
                select(jobs.c.id)
                .where(
                    jobs.c.status == "queued",
                    (jobs.c.next_attempt_at.is_(None)) | (jobs.c.next_attempt_at <= now),
                )
                .order_by(jobs.c.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).first()
            if row is None:
                return None
            token = uuid.uuid4()
            lease_until = now + timedelta(seconds=lease_seconds)
            result = session.execute(
                update(jobs)
                .where(jobs.c.id == row.id, jobs.c.status == "queued")
                .values(
                    status="running",
                    phase="validation",
                    attempt=jobs.c.attempt + 1,
                    worker_id=worker,
                    lease_token=token,
                    lease_until=lease_until,
                    heartbeat_at=now,
                    started_at=now,
                    cancel_requested_at=None,
                    error_code=None,
                    next_attempt_at=None,
                    updated_at=now,
                )
            )
            if _affected(result) != 1:
                return None
            claimed_id = str(row.id)
        job = self.get(claimed_id)
        return job, Claim(
            worker_id=worker,
            lease_token=str(token),
            lease_until=lease_until.timestamp(),
            cancel_requested=False,
        )

    def renew_lease(
        self,
        job_id: str,
        *,
        lease_seconds: float,
        claim: Claim | None = None,
    ) -> bool:
        """
        Renew the lease only — never write phase or progress.

        Fenced by worker/token and a live lease; cancellation is observed at
        cooperative checkpoints, so a `cancelling` row keeps its lease while the
        current owner drains instead of being misreported as ownership loss.
        """
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            return False
        with self._session() as session, session.begin():
            now = self._db_now(session)
            conditions = [
                jobs.c.id == key,
                jobs.c.status.in_(("running", "cancelling")),
                jobs.c.lease_until > now,
            ]
            if claim is not None:
                conditions += [
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                ]
            result = session.execute(
                update(jobs)
                .where(*conditions)
                .values(
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            return _affected(result) == 1

    def report_progress(
        self,
        job_id: str,
        phase: JobPhase,
        progress: int,
        *,
        claim: Claim | None = None,
    ) -> str:
        """
        Monotonic phase/progress update for the current owner, lease untouched.

        Returns `ok` (applied or harmlessly ignored), `cancelled` or
        `OWNERSHIP_LOST`. Stale out-of-order callbacks cannot regress a later
        phase; cancellation is distinct from losing the lease.
        """
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            return OWNERSHIP_LOST
        with self._session() as session, session.begin():
            now = self._db_now(session)
            conditions = [
                jobs.c.id == key,
                jobs.c.status.in_(("running", "cancelling")),
                jobs.c.lease_until > now,
            ]
            if claim is not None:
                conditions += [
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                ]
            row = session.execute(
                select(jobs.c.phase, jobs.c.progress, jobs.c.cancel_requested_at).where(*conditions).with_for_update()
            ).one_or_none()
            if row is None:
                return OWNERSHIP_LOST
            if row.cancel_requested_at is not None:
                return "cancelled"
            old_rank = PHASE_ORDER.index(row.phase) if row.phase in PHASE_ORDER else -1
            new_rank = PHASE_ORDER.index(phase) if phase in PHASE_ORDER else -1
            if new_rank < old_rank or (new_rank == old_rank and progress <= row.progress):
                return "ok"  # stale callback: owner confirmed, no regression
            result = session.execute(
                update(jobs)
                .where(jobs.c.id == key, jobs.c.status == "running")
                .values(phase=phase, progress=progress, updated_at=now)
            )
            return "ok" if _affected(result) == 1 else OWNERSHIP_LOST

    def cancellation_requested(self, job_id: str, claim: Claim) -> bool:
        """Whether a cancel request was recorded for this owner (checkpoint)."""
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            return True
        with self._session() as session:
            value = session.execute(
                select(jobs.c.cancel_requested_at).where(
                    jobs.c.id == key,
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                )
            ).scalar_one_or_none()
        return value is not None

    def request_cancel(self, job_id: str) -> str:
        """
        Idempotent cancellation request (§3.7).

        `queued` -> terminal `cancelled` immediately; `running` -> `cancelling`
        with a timestamp, so the worker acknowledges at its next checkpoint.
        """
        try:
            key = uuid.UUID(job_id)
        except ValueError as error:
            raise JobNotFoundError(job_id) from error
        with self._session() as session, session.begin():
            now = self._db_now(session)
            queued = session.execute(
                update(jobs)
                .where(jobs.c.id == key, jobs.c.status == "queued")
                .values(
                    status="cancelled",
                    cancel_requested_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
            if _affected(queued) == 1:
                return "cancelled"
            running = session.execute(
                update(jobs)
                .where(jobs.c.id == key, jobs.c.status.in_(("running", "cancelling")))
                .values(
                    status="cancelling",
                    cancel_requested_at=now,
                    updated_at=now,
                )
            )
            if _affected(running) == 1:
                return "cancelling"
            row = session.execute(select(jobs.c.status).where(jobs.c.id == key)).scalar_one_or_none()
            if row is None:
                raise JobNotFoundError(job_id)
            return str(row)

    def acknowledge_cancel(self, job_id: str, claim: Claim) -> bool:
        """Fenced cancellation acknowledgement; True only when DB confirms it."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            result = session.execute(
                update(jobs)
                .where(
                    jobs.c.id == uuid.UUID(job_id),
                    jobs.c.status == "cancelling",
                    jobs.c.cancel_requested_at.is_not(None),
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                    jobs.c.lease_until > now,
                )
                .values(
                    status="cancelled",
                    phase="cancelled",
                    lease_token=None,
                    lease_until=None,
                    finished_at=now,
                    updated_at=now,
                )
            )
            return _affected(result) == 1

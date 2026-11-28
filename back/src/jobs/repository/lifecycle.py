"""Terminal transitions, TTL bookkeeping and garbage collection of job rows."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from src.jobs.models import Claim, Job
from src.jobs.schema import jobs

from ._base import OWNERSHIP_LOST, IllegalTransitionError, RepositoryBase, _affected, _row_to_job


class LifecycleMixin(RepositoryBase):
    """Terminal outcomes and retention, with the same fencing as other mutations."""

    def succeed(self, job_id: str, result_key: str, *, ttl_seconds: float, claim: Claim | None = None) -> None:
        """
        Attach an immutable result and transition to `succeeded`.

        Fenced by `claim` when supplied: a stale/cancelled worker affects 0 rows
        and must NOT expose its result. TTL starts on successful completion.
        """
        with self._session() as session, session.begin():
            now = self._db_now(session)
            conditions = [jobs.c.id == uuid.UUID(job_id), jobs.c.status == "running"]
            if claim is not None:
                conditions += [
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                    jobs.c.lease_until > now,
                    jobs.c.cancel_requested_at.is_(None),
                ]
            result = session.execute(
                update(jobs)
                .where(*conditions)
                .values(
                    status="succeeded",
                    phase="completed",
                    progress=100,
                    result_artifact=result_key,
                    lease_token=None,
                    lease_until=None,
                    finished_at=now,
                    result_expires_at=now + timedelta(seconds=ttl_seconds),
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    updated_at=now,
                )
            )
            if _affected(result) != 1:
                self.get(job_id)
                raise IllegalTransitionError("succeed requires a live owned lease")

    def fail(
        self,
        job_id: str,
        error_code: str,
        *,
        retryable: bool,
        max_attempts: int,
        claim: Claim | None = None,
        backoff_seconds: float = 0.0,
    ) -> str:
        """Classified failure: bounded retry with backoff, or terminal `failed`."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            conditions = [jobs.c.id == uuid.UUID(job_id)]
            if claim is not None:
                # Worker-owned failure: status, owner, fencing token and live
                # lease must all match in the same statement. A stale owner
                # receives an explicit ownership_lost outcome and must not
                # retry, publish or delete any artifact.
                conditions += [
                    jobs.c.status.in_(("running", "cancelling")),
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                    jobs.c.lease_until > now,
                    jobs.c.cancel_requested_at.is_(None),
                ]
            row = session.execute(select(jobs.c.attempt, jobs.c.status).where(*conditions).with_for_update()).first()
            if row is None:
                return OWNERSHIP_LOST if claim is not None else "failed"
            if retryable and row.attempt < max_attempts:
                session.execute(
                    update(jobs)
                    .where(*conditions)
                    .values(
                        status="queued",
                        phase="queued",
                        progress=0,
                        error_code=error_code,
                        lease_token=None,
                        lease_until=None,
                        worker_id=None,
                        next_attempt_at=now + timedelta(seconds=backoff_seconds),
                        updated_at=now,
                    )
                )
                return "queued"
            session.execute(
                update(jobs)
                .where(*conditions)
                .values(
                    status="failed",
                    error_code=error_code,
                    lease_token=None,
                    lease_until=None,
                    worker_id=None,
                    finished_at=now,
                    result_expires_at=now + timedelta(seconds=self._settings.jobs_ttl_seconds),
                    expires_at=now + timedelta(seconds=self._settings.jobs_ttl_seconds),
                    updated_at=now,
                )
            )
            return "failed"

    def cancel(self, job_id: str) -> bool:
        """Idempotent cancellation request (legacy bool shape for the API)."""
        state = self.request_cancel(job_id)
        return state in ("cancelled", "cancelling")

    def expire_queued(self, *, queue_wait_seconds: float) -> int:
        """Transition queued jobs past their queue deadline to `expired` (§3.9)."""
        if queue_wait_seconds <= 0:
            return 0
        with self._session() as session, session.begin():
            now = self._db_now(session)
            result = session.execute(
                update(jobs)
                .where(
                    jobs.c.status == "queued",
                    jobs.c.created_at <= now - timedelta(seconds=queue_wait_seconds),
                )
                .values(status="expired", error_code="queue_timeout", updated_at=now)
            )
            return _affected(result)

    def expired_jobs(self, now: float) -> list[Job]:
        """
        Terminal rows eligible for GC (§3.9).

        Active jobs are never swept: succeeded rows use `result_expires_at`
        (set at completion), failed/cancelled rows use a separate retention
        deadline (`expires_at` + `jobs_ttl_seconds` is applied by the worker).
        """
        cutoff = datetime.fromtimestamp(now, UTC)
        with self._session() as session:
            rows = session.execute(
                select(jobs).where(
                    jobs.c.status.in_(("succeeded", "failed", "cancelled", "expired")),
                    jobs.c.result_expires_at.is_not(None),
                    jobs.c.result_expires_at <= cutoff,
                )
            ).all()
        return [_row_to_job(row) for row in rows]

    def mark_terminal_retention(self, *, retention_seconds: float) -> None:
        """Give terminal rows without a result TTL a separate retention deadline."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            session.execute(
                update(jobs)
                .where(
                    jobs.c.status.in_(("failed", "cancelled", "expired")),
                    jobs.c.result_expires_at.is_(None),
                )
                .values(result_expires_at=now + timedelta(seconds=retention_seconds))
            )

    def remove_expired(self, job_ids: list[str]) -> None:
        """Delete expired rows after their artifacts are gone."""
        if not job_ids:
            return
        keys = [uuid.UUID(job_id) for job_id in job_ids]
        with self._session() as session, session.begin():
            session.execute(jobs.delete().where(jobs.c.id.in_(keys)))

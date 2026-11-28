"""Staging, enqueue and recovery operations for the durable job repository."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update

from src.jobs.models import Job, JobKind
from src.jobs.schema import jobs
from src.metrics import observe_job_created

from ._base import (
    ADMISSION_LOCK_KEY,
    IllegalTransitionError,
    JobNotFoundError,
    JobQueueFullError,
    RepositoryBase,
    _affected,
    _now,
)


class StagingMixin(RepositoryBase):
    """Staging rows are never claimable; only published `queued` rows are."""

    def create_staging(self, kind: JobKind, *, max_active: int, max_attempts: int, cache_key: str) -> tuple[Job, bool]:
        """
        Insert a `staging` row, or attach to an identical active job (§4.5).

        PostgreSQL-side de-duplication: a concurrent identical submission
        reuses the existing staging/queued/running job instead of doing the
        expensive work twice. A transaction-scoped advisory lock serialises the
        dedupe read, the active-count check and the insert, so neither duplicate
        jobs nor an over-full queue can be created under concurrency. Returns
        `(job, created)`.
        """
        job_id = uuid.uuid4()
        now = _now()
        attached_id: str | None = None
        with self._session() as session, session.begin():
            session.execute(select(func.pg_advisory_xact_lock(ADMISSION_LOCK_KEY)))
            existing = session.execute(
                select(jobs.c.id)
                .where(
                    jobs.c.cache_key == cache_key,
                    jobs.c.status.in_(("staging", "queued", "running", "cancelling")),
                )
                .order_by(jobs.c.created_at)
                .limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                attached_id = str(existing)
            else:
                active = session.execute(
                    select(func.count())
                    .select_from(jobs)
                    .where(jobs.c.status.in_(("staging", "queued", "running", "cancelling")))
                ).scalar_one()
                if active >= max_active:
                    msg = "Очередь заданий переполнена. Повторите позже."
                    raise JobQueueFullError(msg)
                session.execute(
                    jobs.insert().values(
                        id=job_id,
                        kind=kind,
                        status="staging",
                        phase="queued",
                        progress=0,
                        attempt=0,
                        max_attempts=max_attempts,
                        created_at=now,
                        updated_at=now,
                        cache_key=cache_key,
                        staging_until=now + timedelta(seconds=self._settings.staging_lease_seconds),
                    )
                )
        if attached_id is not None:
            return self.get(attached_id), False
        observe_job_created(kind)
        return self.get(str(job_id)), True

    def publish_staging(
        self,
        job_id: str,
        *,
        input_artifact: str,
        keys_artifact: str | None,
        ttl_seconds: float,
    ) -> Job:
        """Attach verified artifacts and move `staging -> queued` in one transaction."""
        with self._session() as session, session.begin():
            current = session.execute(
                select(jobs.c.status).where(jobs.c.id == uuid.UUID(job_id)).with_for_update()
            ).scalar_one_or_none()
            if current is None:
                raise JobNotFoundError(job_id)
            self._transition(current, "queued")
            now = self._db_now(session)
            result = session.execute(
                update(jobs)
                .where(jobs.c.id == uuid.UUID(job_id), jobs.c.status == "staging")
                .values(
                    status="queued",
                    phase="queued",
                    input_artifact=input_artifact,
                    keys_artifact=keys_artifact,
                    staging_until=None,
                    result_expires_at=now + timedelta(seconds=ttl_seconds),
                    expires_at=now + timedelta(seconds=ttl_seconds),
                    updated_at=now,
                )
            )
            if _affected(result) != 1:
                raise IllegalTransitionError("staging -> queued")
        return self.get(job_id)

    def abort_staging(self, job_id: str, error_code: str) -> None:
        """Fail a staging row after a failed atomic publish (idempotent)."""
        with self._session() as session, session.begin():
            current = session.execute(
                select(jobs.c.status).where(jobs.c.id == uuid.UUID(job_id)).with_for_update()
            ).scalar_one_or_none()
            if current is None or current != "staging":
                return
            self._transition(current, "failed")
            session.execute(
                update(jobs)
                .where(jobs.c.id == uuid.UUID(job_id), jobs.c.status == "staging")
                .values(
                    status="failed",
                    error_code=error_code,
                    staging_until=None,
                    finished_at=self._db_now(session),
                )
            )

    def touch_staging(self, job_id: str) -> bool:
        """Renew an in-progress staging lease; False if recovery claimed the row."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            result = session.execute(
                update(jobs)
                .where(
                    jobs.c.id == uuid.UUID(job_id),
                    jobs.c.status == "staging",
                    jobs.c.staging_until > now,
                )
                .values(staging_until=now + timedelta(seconds=self._settings.staging_lease_seconds), updated_at=now)
            )
            return _affected(result) == 1

    def recover_abandoned_staging(self, *, limit: int = 100) -> list[str]:
        """Atomically claim expired staging rows for cleanup (never make them claimable)."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            rows = session.execute(
                select(jobs.c.id)
                .where(jobs.c.status == "staging", jobs.c.staging_until <= now)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for row in rows:
                session.execute(
                    update(jobs)
                    .where(jobs.c.id == row.id, jobs.c.status == "staging", jobs.c.staging_until <= now)
                    .values(
                        status="failed",
                        phase="failed",
                        error_code="staging_abandoned",
                        staging_until=None,
                        finished_at=now,
                        updated_at=now,
                    )
                )
            return [str(row.id) for row in rows]

    def recover_expired_cancelling(self, *, limit: int = 100) -> list[str]:
        """Terminally cancel a job whose owner died after a cancel request."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            rows = session.execute(
                select(jobs.c.id)
                .where(jobs.c.status == "cancelling", jobs.c.lease_until <= now)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for row in rows:
                session.execute(
                    update(jobs)
                    .where(jobs.c.id == row.id, jobs.c.status == "cancelling", jobs.c.lease_until <= now)
                    .values(
                        status="cancelled",
                        phase="cancelled",
                        cancel_requested_at=now,
                        worker_id=None,
                        lease_token=None,
                        lease_until=None,
                        finished_at=now,
                        result_expires_at=now + timedelta(seconds=self._settings.jobs_ttl_seconds),
                        updated_at=now,
                    )
                )
            return [str(row.id) for row in rows]

    def create(self, kind: JobKind, *, max_active: int, ttl_seconds: float) -> Job:
        """
        Enqueue a job, enforcing the bounded active queue (429 on overflow).

        Phase 1 keeps the legacy single-call API: it creates and immediately
        publishes a staging row with no artifacts (the sync overflow path still
        writes artifact files afterwards, through `ArtifactStore`). Phase 2
        replaces this with the full staged protocol. Text jobs need no staged
        input file to be claimable.
        """
        staged, _created = self.create_staging(
            kind,
            max_active=max_active,
            max_attempts=self._settings.jobs_max_attempts,
            cache_key=str(uuid.uuid4()),
        )
        return self.publish_staging(staged.id, input_artifact="", keys_artifact=None, ttl_seconds=ttl_seconds)

    def reconcile_staging(self) -> list[str]:
        """
        Compatibility helper: claim + return abandoned staging ids.

        Kept for tests/diagnostics; runtime recovery runs from
        ``recover_abandoned_staging`` inside the worker maintenance/claim
        lifecycle and never deletes a live staging row.
        """
        return self.recover_abandoned_staging()

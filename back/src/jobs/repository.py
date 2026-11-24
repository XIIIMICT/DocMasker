"""
PostgreSQL job repository (ADR-0003, Phase 1).

One process-wide pooled engine; every operation uses its own short session and
never holds a transaction while masking/DOCX/ZIP/filesystem work runs. Claims
use `SELECT ... FOR UPDATE SKIP LOCKED` in one transaction, with a fresh
`uuid4()` lease token per claim (fencing). Staging rows are never claimable —
only `queued` rows with published artifacts can be taken, which removes the
enqueue/claim race. No PII/keys/filenames live in a row, log or exception.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import Session

from .models import Claim, Job, JobKind, JobPhase
from .schema import ALLOWED_TRANSITIONS, jobs

if TYPE_CHECKING:
    from src.config import Settings


class JobQueueFullError(RuntimeError):
    """The bounded queue cannot accept another job (429)."""


class JobNotFoundError(KeyError):
    """Unknown job id (404)."""


class IllegalTransitionError(RuntimeError):
    """A repository call would move a job through a disallowed transition."""


def _now() -> datetime:
    """Process time for inserts; ownership checks use PostgreSQL clock_timestamp()."""
    return datetime.now(UTC)


def _affected(result: Any) -> int:  # noqa: ANN401 (SQLAlchemy Result is dynamically typed)
    """Row count of an UPDATE/DELETE (SQLAlchemy CursorResult typing)."""
    return cast("CursorResult[Any]", result).rowcount


def _row_to_job(row: Any) -> Job:  # noqa: ANN401 (SQLAlchemy Row is dynamically typed)
    """Map a SQLAlchemy Row to the safe public Job projection."""
    m = row._mapping  # noqa: SLF001 (SQLAlchemy Row internals are stable)
    return Job(
        id=str(m["id"]),
        kind=m["kind"],
        status=m["status"],
        phase=m["phase"],
        progress=m["progress"],
        attempt=m["attempt"],
        error_code=m["error_code"],
        created_at=m["created_at"].timestamp(),
        updated_at=m["updated_at"].timestamp(),
        lease_until=m["lease_until"].timestamp() if m["lease_until"] else None,
        expires_at=(m["result_expires_at"] or m["expires_at"]).timestamp()
        if (m["result_expires_at"] or m["expires_at"])
        else 0.0,
        result_key=m["result_artifact"],
        worker_id=m["worker_id"],
        lease_token=str(m["lease_token"]) if m["lease_token"] else None,
        cancel_requested=m["cancel_requested_at"] is not None,
        max_attempts=m["max_attempts"],
        input_artifact=m["input_artifact"],
        cache_key=m["cache_key"],
        finished_at=m["finished_at"].timestamp() if m["finished_at"] else None,
    )


class JobRepository:
    """Durable metadata store backed by PostgreSQL (sync SQLAlchemy 2 + psycopg 3)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(
            settings.sqlalchemy_database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            connect_args={"connect_timeout": int(settings.db_connect_timeout_seconds)},
        )

    @contextmanager
    def _session(self) -> Generator[Session]:
        session = Session(self._engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    def count_active(self) -> int:
        """Return the number of non-terminal jobs (safe aggregate for /metrics)."""
        with self._session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(jobs)
                    .where(jobs.c.status.in_(("staging", "queued", "running", "cancelling")))
                ).scalar_one()
            )

    @staticmethod
    def _db_now(session: Session) -> datetime:
        return session.execute(select(func.clock_timestamp())).scalar_one()

    @staticmethod
    def _transition(current: str, target: str) -> None:
        if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            msg = f"Недопустимый переход задания: {current} -> {target}."
            raise IllegalTransitionError(msg)

    # -- staging / enqueue ---------------------------------------------------

    def create_staging(self, kind: JobKind, *, max_active: int, max_attempts: int, cache_key: str) -> tuple[Job, bool]:
        """
        Insert a `staging` row, or attach to an identical active job (§4.5).

        PostgreSQL-side de-duplication: a concurrent identical submission
        reuses the existing staging/queued/running job instead of doing the
        expensive work twice. Returns `(job, created)`.
        """
        job_id = uuid.uuid4()
        now = _now()
        attached_id: str | None = None
        with self._session() as session, session.begin():
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
                    )
                )
        if attached_id is not None:
            return self.get(attached_id), False
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
                .values(status="failed", error_code=error_code, finished_at=self._db_now(session))
            )

    # -- public API used by routing/api/worker --------------------------------

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

    def get(self, job_id: str) -> Job:
        """Read one job by opaque id (404 when unknown)."""
        try:
            key = uuid.UUID(job_id)
        except ValueError as error:
            raise JobNotFoundError(job_id) from error
        with self._session() as session:
            row = session.execute(select(jobs).where(jobs.c.id == key)).first()
        if row is None:
            raise JobNotFoundError(job_id)
        return _row_to_job(row)

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
                if record.attempt + 1 >= record.max_attempts:
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

    def heartbeat(
        self,
        job_id: str,
        phase: JobPhase,
        progress: int,
        *,
        lease_seconds: float,
        claim: Claim | None = None,
    ) -> bool:
        """
        Renew the lease, fenced by the claim token when one is supplied.

        Phase/phase progress must be monotonic; a stale worker's update affects
        0 rows and returns False. Cancellation is reported through the same
        channel (`cancel_requested_at` set -> no renewal).
        """
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            return False
        with self._session() as session, session.begin():
            now = self._db_now(session)
            conditions = [
                jobs.c.id == key,
                jobs.c.status == "running",
                jobs.c.lease_until > now,
            ]
            if claim is not None:
                conditions += [
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                    jobs.c.cancel_requested_at.is_(None),
                ]
            result = session.execute(
                update(jobs)
                .where(*conditions)
                .values(
                    phase=phase,
                    progress=progress,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            return _affected(result) == 1

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

    def acknowledge_cancel(self, job_id: str, claim: Claim) -> None:
        """Worker acknowledged cancellation; fenced by the claim token."""
        with self._session() as session, session.begin():
            now = self._db_now(session)
            session.execute(
                update(jobs)
                .where(
                    jobs.c.id == uuid.UUID(job_id),
                    jobs.c.status == "cancelling",
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
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
                conditions += [
                    jobs.c.worker_id == claim.worker_id,
                    jobs.c.lease_token == uuid.UUID(claim.lease_token),
                ]
            row = session.execute(select(jobs.c.attempt, jobs.c.status).where(*conditions).with_for_update()).first()
            if row is None:
                return "failed"
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

    def reconcile_staging(self) -> list[str]:
        """Return stale staging ids for crash recovery (Phase 2 will act on them)."""
        with self._session() as session:
            rows = session.execute(select(jobs.c.id).where(jobs.c.status == "staging")).all()
        return [str(row.id) for row in rows]

    def dispose(self) -> None:
        """Release the process-wide pool (tests / shutdown)."""
        self._engine.dispose()

    def ping(self) -> None:
        """Fail fast when PostgreSQL is unreachable (readiness, §2.3)."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

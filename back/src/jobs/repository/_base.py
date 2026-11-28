"""
Shared contract for the PostgreSQL job repository (ADR-0003, Phase 1).

Errors, row/result helpers and the core engine/session lifecycle live here;
the lifecycle-specific methods are split into mixins that only rely on this
shared surface. Every operation uses its own short session and never holds a
transaction while masking/DOCX/ZIP/filesystem work runs. Claims use
`SELECT ... FOR UPDATE SKIP LOCKED` in one transaction, with a fresh `uuid4()`
lease token per claim (fencing). No PII/keys/filenames live in a row, log or
exception.
"""

import uuid
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import Session

from src.jobs.models import Job
from src.jobs.schema import ALLOWED_TRANSITIONS, jobs
from src.metrics import set_db_pool_metrics, set_jobs_active

if TYPE_CHECKING:
    from src.config import Settings
    from src.jobs.models import JobKind


class JobQueueFullError(RuntimeError):
    """The bounded queue cannot accept another job (429)."""


class JobNotFoundError(KeyError):
    """Unknown job id (404)."""


class IllegalTransitionError(RuntimeError):
    """A repository call would move a job through a disallowed transition."""


#: Explicit fenced-mutation outcomes — never conflated with a terminal failure.
OWNERSHIP_LOST = "ownership_lost"

#: One transaction-scoped advisory lock serialises dedupe + capacity + insert.
#: A per-key lock would not protect the *global* active-job limit; the critical
#: section is short (three queries, no file I/O), so one lock is sufficient.
ADMISSION_LOCK_KEY = 0x444D5F41444D494E  # "DM_ADMIN"


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


class RepositoryBase:
    """Typing-only cross-mixin contract; no runtime behaviour is defined here."""

    if TYPE_CHECKING:
        _settings: Settings
        _engine: Engine

        def _session(self) -> AbstractContextManager[Session]: ...

        @staticmethod
        def _db_now(session: Session) -> datetime: ...

        @staticmethod
        def _transition(current: str, target: str) -> None: ...

        def get(self, job_id: str) -> Job: ...

        def create_staging(
            self, kind: JobKind, *, max_active: int, max_attempts: int, cache_key: str
        ) -> tuple[Job, bool]: ...

        def publish_staging(
            self,
            job_id: str,
            *,
            input_artifact: str,
            keys_artifact: str | None,
            ttl_seconds: float,
        ) -> Job: ...

        def recover_abandoned_staging(self, *, limit: int = 100) -> list[str]: ...

        def recover_expired_cancelling(self, *, limit: int = 100) -> list[str]: ...

        def request_cancel(self, job_id: str) -> str: ...


class RepositoryCore(RepositoryBase):
    """Engine/session lifecycle and shared read/transition primitives."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(
            settings.sqlalchemy_database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            connect_args={"connect_timeout": int(settings.db_connect_timeout_seconds)},
        )
        set_db_pool_metrics(
            in_use=0,
            capacity=settings.db_pool_size + settings.db_max_overflow,
        )

    @contextmanager
    def _session(self) -> Generator[Session]:
        session = Session(self._engine, expire_on_commit=False)
        try:
            yield session
        finally:
            session.close()

    def count_active(self) -> int:
        """
        Return the number of non-terminal jobs and refresh queue/pool gauges.

        One grouped query feeds `dm_jobs_active{kind}` (queue depth) and the
        bounded active-job total used by worker polling. No job ids, keys or
        filenames are read.
        """
        with self._session() as session:
            rows = session.execute(
                select(jobs.c.kind, func.count().label("total"))
                .where(jobs.c.status.in_(("staging", "queued", "running", "cancelling")))
                .group_by(jobs.c.kind)
            ).all()
        counts = {str(row.kind): int(row.total) for row in rows}
        set_jobs_active(counts)
        try:
            checkedout = getattr(self._engine.pool, "checkedout", None)
            in_use = int(checkedout()) if callable(checkedout) else 0
        except Exception:  # noqa: BLE001 - pool introspection is best-effort
            in_use = 0
        set_db_pool_metrics(
            in_use=in_use,
            capacity=self._settings.db_pool_size + self._settings.db_max_overflow,
        )
        return sum(counts.values())

    @staticmethod
    def _db_now(session: Session) -> datetime:
        return session.execute(select(func.clock_timestamp())).scalar_one()

    @staticmethod
    def _transition(current: str, target: str) -> None:
        if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            msg = f"Недопустимый переход задания: {current} -> {target}."
            raise IllegalTransitionError(msg)

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

    def dispose(self) -> None:
        """Release the process-wide pool (tests / shutdown)."""
        self._engine.dispose()

    def ping(self) -> None:
        """Fail fast when PostgreSQL is unreachable (readiness, §2.3)."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

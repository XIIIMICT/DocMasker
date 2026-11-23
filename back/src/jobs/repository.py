"""
SQLite WAL job repository (ADR-0002).

One private volume holds the database and the artifact spool. Claims are
transactional (`BEGIN IMMEDIATE` + `FOR UPDATE`-equivalent single writer), so
a restart or two worker processes cannot double-run a job. Leases expire and
are reclaimed; terminal artifacts are swept by TTL. No PII/keys ever enter a
row, a log or an exception message.
"""

import sqlite3
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Job, JobKind, JobPhase

if TYPE_CHECKING:
    from src.config import Settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    phase         TEXT NOT NULL,
    progress      INTEGER NOT NULL DEFAULT 0,
    attempt       INTEGER NOT NULL DEFAULT 0,
    error_code    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    lease_until   REAL,
    expires_at    REAL NOT NULL,
    result_key    TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_lease ON jobs (status, lease_until);
"""

_COLUMNS = (
    "id",
    "kind",
    "status",
    "phase",
    "progress",
    "attempt",
    "error_code",
    "created_at",
    "updated_at",
    "lease_until",
    "expires_at",
    "result_key",
)
_SELECT = (
    "SELECT id, kind, status, phase, progress, attempt, error_code, "
    "created_at, updated_at, lease_until, expires_at, result_key FROM jobs "
)


class JobQueueFullError(RuntimeError):
    """The bounded queue cannot accept another job (429)."""


class JobNotFoundError(KeyError):
    """Unknown job id (404)."""


class JobRepository:
    """Durable metadata store for one worker host (SQLite WAL)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = Path(settings.jobs_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    class _Connection:
        """Explicitly-closed connection (`with sqlite3.connect` does not close)."""

        def __init__(self, path: Path) -> None:
            self._path = path
            self._conn: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self._conn = sqlite3.connect(self._path, timeout=10.0, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout=10000")
            return self._conn

        def __exit__(self, *_exception: object) -> None:
            if self._conn is not None:
                self._conn.close()

    def _connect(self) -> _Connection:
        return self._Connection(self._db_path)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            phase=row["phase"],
            progress=row["progress"],
            attempt=row["attempt"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            lease_until=row["lease_until"],
            expires_at=row["expires_at"],
            result_key=row["result_key"],
        )

    def create(self, kind: JobKind, *, max_active: int, ttl_seconds: float) -> Job:
        """Enqueue a job, enforcing the bounded active queue (429 on overflow)."""
        job_id = str(uuid.uuid4())
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]
            if active >= max_active:
                conn.execute("ROLLBACK")
                msg = "Очередь заданий переполнена. Повторите позже."
                raise JobQueueFullError(msg)
            conn.execute(
                "INSERT INTO jobs (id, kind, status, phase, progress, attempt, "
                "created_at, updated_at, expires_at) VALUES (?, ?, 'queued', 'queued', 0, 0, ?, ?, ?)",
                (job_id, kind, now, now, now + ttl_seconds),
            )
            conn.execute("COMMIT")
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        """Read one job by opaque id."""
        with self._connect() as conn:
            row = conn.execute(_SELECT + "WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._row_to_job(row)

    def claim(self, *, lease_seconds: float, concurrency: int) -> Job | None:
        """
        Atomically claim the oldest eligible job (queued, or a stale lease).

        Concurrency is bounded by the number of currently running jobs. A
        claimed job gets a fresh lease token (its id + attempt + timestamp as
        `lease_until`) so a stale worker cannot keep renewing it.
        """
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Reclaim stale leases FIRST: a crashed worker's job must not
                # keep occupying a concurrency slot (otherwise a single dead
                # job with concurrency=1 wedges the queue forever).
                stale = conn.execute(
                    "SELECT id FROM jobs WHERE status = 'running' AND lease_until <= ?",
                    (now,),
                ).fetchall()
                for record in stale:
                    conn.execute(
                        "UPDATE jobs SET status = 'queued', phase = 'queued', "
                        "lease_until = NULL, updated_at = ? WHERE id = ?",
                        (now, record["id"]),
                    )
                running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'").fetchone()[0]
                if running >= concurrency:
                    conn.execute("COMMIT")
                    return None
                row = conn.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1").fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    "UPDATE jobs SET status = 'running', phase = 'validation', attempt = attempt + 1, "
                    "lease_until = ?, updated_at = ? WHERE id = ?",
                    (now + lease_seconds, now, row["id"]),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get(row["id"])

    def heartbeat(self, job_id: str, phase: JobPhase, progress: int, *, lease_seconds: float) -> bool:
        """Renew the lease and report progress; False when ownership is gone."""
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status, lease_until, expires_at FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None or row["status"] != "running" or row["lease_until"] is None or row["lease_until"] <= now:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "UPDATE jobs SET phase = ?, progress = ?, lease_until = ?, updated_at = ? WHERE id = ?",
                    (phase, progress, now + lease_seconds, now, job_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                return True

    def succeed(self, job_id: str, result_key: str, *, ttl_seconds: float) -> None:
        """Mark the result ready and set its download TTL."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'succeeded', phase = 'completed', progress = 100, "
                "result_key = ?, lease_until = NULL, expires_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'running'",
                (result_key, now + ttl_seconds, now, job_id),
            )

    def fail(self, job_id: str, error_code: str, *, retryable: bool, max_attempts: int) -> str:
        """
        Record a failure and return the new status.

        Retryable jobs with attempts left go back to `queued`; others become
        `failed`. No exception text (which could carry input values) is stored.
        """
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT attempt FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return "failed"
            if retryable and row["attempt"] < max_attempts:
                conn.execute(
                    "UPDATE jobs SET status = 'queued', phase = 'queued', progress = 0, "
                    "error_code = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                    (error_code, now, job_id),
                )
                conn.execute("COMMIT")
                return "queued"
            conn.execute(
                "UPDATE jobs SET status = 'failed', error_code = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                (error_code, now, job_id),
            )
            conn.execute("COMMIT")
        return "failed"

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a queued/running job (404 when unknown)."""
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = 'cancelled', lease_until = NULL, updated_at = ? "
                "WHERE id = ? AND status IN ('queued', 'running')",
                (now, job_id),
            )
            if cursor.rowcount == 0:
                # Distinguish 404 (unknown id) from an already-terminal job.
                self.get(job_id)
                return False
        return True

    def expired_jobs(self, now: float) -> list[Job]:
        """Terminal/succeeded jobs whose TTL has elapsed (for the sweeper)."""
        with self._connect() as conn:
            rows = conn.execute(_SELECT + "WHERE expires_at <= ?", (now,)).fetchall()
        return [self._row_to_job(row) for row in rows]

    def remove_expired(self, job_ids: list[str]) -> None:
        """Delete expired rows after their artifacts are gone."""
        if not job_ids:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany("DELETE FROM jobs WHERE id = ?", [(job_id,) for job_id in job_ids])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

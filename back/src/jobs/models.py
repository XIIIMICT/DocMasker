"""
Durable job records and phases (ADR-0002).

A job row is metadata only: op/files/keys are NEVER stored in SQLite or
logged. Input and result artifacts live as files on a private volume keyed by
the opaque job id. Statuses and phases are safe to expose to the client.
"""

from dataclasses import dataclass
from typing import Literal

JobKind = Literal["mask_text", "mask_file", "unmask_text", "unmask_file"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "expired"]
JobPhase = Literal["queued", "validation", "detection", "rewrite", "archive", "completed"]

#: Phases in execution order, for a coarse progress display.
PHASE_ORDER: tuple[JobPhase, ...] = ("queued", "validation", "detection", "rewrite", "archive", "completed")

_ACTIVE_STATUSES: frozenset[JobStatus] = frozenset({"queued", "running"})
_TERMINAL_STATUSES: frozenset[JobStatus] = frozenset({"succeeded", "failed", "cancelled", "expired"})


@dataclass(frozen=True, slots=True)
class Job:
    """One durable job row (no PII, no keys, no file contents)."""

    id: str
    kind: JobKind
    status: JobStatus
    phase: JobPhase
    progress: int
    attempt: int
    error_code: str | None
    created_at: float
    updated_at: float
    lease_until: float | None
    expires_at: float
    result_key: str | None

    @property
    def is_active(self) -> bool:
        """Whether the job still occupies a queue/worker slot."""
        return self.status in _ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        """Whether no further execution will happen for the job."""
        return self.status in _TERMINAL_STATUSES

    @property
    def has_result(self) -> bool:
        """Whether a downloadable result artifact exists."""
        return self.status == "succeeded" and self.result_key is not None

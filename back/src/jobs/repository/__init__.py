"""
PostgreSQL job repository (ADR-0003, Phase 1).

Facade: the stable public surface (`JobRepository` and its error types) is
composed from the core engine/session mixin and the three lifecycle concerns.
Existing `from src.jobs.repository import ...` / `from .repository import ...`
imports keep working unchanged. All SQL and transaction boundaries live in the
concern modules; no behaviour is reimplemented here.
"""

from ._base import (
    ADMISSION_LOCK_KEY,  # noqa: F401 - facade re-export
    OWNERSHIP_LOST,  # noqa: F401 - facade re-export
    IllegalTransitionError,  # noqa: F401 - facade re-export
    JobNotFoundError,  # noqa: F401 - facade re-export
    JobQueueFullError,  # noqa: F401 - facade re-export
    RepositoryCore,
)
from .claims import ClaimsMixin
from .lifecycle import LifecycleMixin
from .staging import StagingMixin


class JobRepository(RepositoryCore, StagingMixin, ClaimsMixin, LifecycleMixin):
    """Durable metadata store backed by PostgreSQL (sync SQLAlchemy 2 + psycopg 3)."""

"""
SQLAlchemy schema for the PostgreSQL job store (ADR-0003, Phase 1).

One `MetaData` is shared by Alembic (`migrations/env.py`) and the repository,
so the migration and the ORM/Core queries cannot drift. Columns carry no PII,
keys YAML, entity values, raw exception text or user filenames — those live in
private staged artifacts keyed by the opaque job id.
"""

import sqlalchemy as sa
from sqlalchemy import MetaData, Table

metadata = MetaData()

STATUSES = (
    "staging",
    "queued",
    "running",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
)
KINDS = ("mask_text", "mask_file", "unmask_text", "unmask_file")

#: Allowed transitions (brief §3.2). Enforced centrally by the repository;
#: the DB CHECK constraint only guards the status vocabulary itself.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "staging": frozenset({"queued", "failed"}),
    "queued": frozenset({"running", "cancelled", "expired"}),
    "running": frozenset({"running", "cancelling", "succeeded", "queued", "failed", "cancelled"}),
    "cancelling": frozenset({"cancelled", "queued", "failed"}),
    "succeeded": frozenset({"expired"}),
    "failed": frozenset({"expired"}),
    "cancelled": frozenset({"expired"}),
    "expired": frozenset(),
}

jobs: Table = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("phase", sa.Text(), nullable=False),
    sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    sa.Column("worker_id", sa.Text()),
    sa.Column("lease_token", sa.Uuid()),
    sa.Column("lease_until", sa.DateTime(timezone=True)),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
    sa.Column("staging_until", sa.DateTime(timezone=True)),
    sa.Column("queue_expires_at", sa.DateTime(timezone=True)),
    sa.Column("result_expires_at", sa.DateTime(timezone=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    sa.Column("input_artifact", sa.Text()),
    sa.Column("keys_artifact", sa.Text()),
    sa.Column("result_artifact", sa.Text()),
    sa.Column("cache_key", sa.Text()),
    sa.Column("error_code", sa.Text()),
    sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint("kind IN ('mask_text','mask_file','unmask_text','unmask_file')", name="ck_jobs_kind"),
    sa.CheckConstraint(
        "status IN ('staging','queued','running','cancelling','succeeded','failed','cancelled','expired')",
        name="ck_jobs_status",
    ),
    sa.Index("ix_jobs_queue", "status", "created_at"),
    sa.Index("ix_jobs_lease", "status", "lease_until"),
    sa.Index("ix_jobs_expiry", "result_expires_at"),
)

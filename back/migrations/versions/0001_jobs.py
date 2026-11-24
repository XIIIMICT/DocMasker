"""Create the PostgreSQL jobs table (Phase 1, ADR-0003)."""

import sqlalchemy as sa
from alembic import op

revision = "0001_jobs"
down_revision = None
branch_labels = None
depends_on = None

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


def upgrade() -> None:
    op.create_table(
        "jobs",
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
        sa.Column("queue_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result_expires_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("input_artifact", sa.Text()),
        sa.Column("keys_artifact", sa.Text()),
        sa.Column("result_artifact", sa.Text()),
        sa.Column("cache_key", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"kind IN {KINDS}", name="ck_jobs_kind"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_jobs_status"),
    )
    op.create_index("ix_jobs_queue", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_lease", "jobs", ["status", "lease_until"])
    op.create_index("ix_jobs_expiry", "jobs", ["result_expires_at"])


def downgrade() -> None:
    op.drop_table("jobs")

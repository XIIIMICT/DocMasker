"""Add a staging lease so abandoned enqueue operations can be recovered."""

import sqlalchemy as sa
from alembic import op

revision = "0002_staging_recovery"
down_revision = "0001_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("staging_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_staging_until", "jobs", ["status", "staging_until"])


def downgrade() -> None:
    op.drop_index("ix_jobs_staging_until", table_name="jobs")
    op.drop_column("jobs", "staging_until")

"""Alembic environment for DocMasker (PostgreSQL, sync psycopg 3 driver)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from src.config import get_settings
from src.jobs.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _database_url() -> str:
    """Prefer the explicit Alembic URL (CI/tests); fall back to Settings."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A short-lived NullPool engine: migrations are an explicit deployment/CI
    # step, not a per-request pool (brief §2.2).
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

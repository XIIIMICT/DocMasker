"""
PostgreSQL integration fixtures for the job-store tests (ADR-0003, Phase 1).

Tests must run against a real PostgreSQL: the `SKIP LOCKED` claim and lease
semantics are PostgreSQL-specific and cannot be validated on SQLite (brief
§2.1). Set TEST_DATABASE_URL (or DATABASE_URL) to an empty, disposable
database; the whole suite is skipped when no server is reachable, so a plain
offline `pytest` run stays deterministic and green. Migrations are applied once
per session; the `jobs` table is truncated between tests for isolation.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration tests need a real server)")
    engine = create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 (any driver/connection failure means skip)
        pytest.skip(f"PostgreSQL is not reachable: {type(error).__name__}")
    finally:
        engine.dispose()
    return TEST_DATABASE_URL


@pytest.fixture(autouse=False)
def migrated_database(pg_url: str) -> None:
    """Apply Alembic migrations once against the disposable test database."""
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", pg_url)
    command.upgrade(config, "head")


@pytest.fixture(autouse=False)
def clean_jobs(pg_url: str, migrated_database: None) -> Iterator[None]:
    """Isolate every test: clear rows + dispose caches before and after."""
    import gc

    from src.jobs import dependencies

    engine = create_engine(pg_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs"))
        _dispose_cached_repository()
        dependencies.job_repository.cache_clear()
        dependencies.artifact_store.cache_clear()
        gc.collect()
        yield
        _dispose_cached_repository()
        dependencies.job_repository.cache_clear()
        dependencies.artifact_store.cache_clear()
        gc.collect()
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs"))
    finally:
        _dispose_cached_repository()
        engine.dispose()
        gc.collect()


def _dispose_cached_repository() -> None:
    """Close the process-wide pool before clearing the lru_cache."""
    from src.jobs import dependencies

    if dependencies.job_repository.cache_info().currsize:
        dependencies.job_repository().dispose()

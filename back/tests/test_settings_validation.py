"""Phase 6 §10: Pydantic bounds and cross-field resource validators."""

import pytest
from pydantic import ValidationError

from src.config import Settings

#: Pydantic-settings uses validation_alias names as input keys.
_ALIASES: dict[str, str] = {
    "max_upload_mb": "UPLOAD_MAX_MB",
    "max_text_chars": "MAX_TEXT_CHARS",
    "max_keys_chars": "MAX_KEYS_CHARS",
    "sync_max_text_chars": "SYNC_MAX_TEXT_CHARS",
    "sync_max_docx_bytes": "SYNC_MAX_DOCX_BYTES",
    "sync_max_docx_paragraphs": "SYNC_MAX_DOCX_PARAGRAPHS",
    "db_pool_size": "DB_POOL_SIZE",
    "db_max_overflow": "DB_MAX_OVERFLOW",
    "db_connect_timeout_seconds": "DB_CONNECT_TIMEOUT_SECONDS",
    "jobs_concurrency": "JOBS_CONCURRENCY",
    "jobs_max_active": "JOBS_MAX_ACTIVE",
    "jobs_max_attempts": "JOBS_MAX_ATTEMPTS",
    "cache_max_entries": "CACHE_MAX_ENTRIES",
    "cache_max_memory_mb": "CACHE_MAX_MEMORY_MB",
    "cache_ttl_seconds": "CACHE_TTL_SECONDS",
    "cache_connect_timeout_seconds": "CACHE_CONNECT_TIMEOUT_SECONDS",
    "cache_socket_timeout_seconds": "CACHE_SOCKET_TIMEOUT_SECONDS",
    "cache_enabled": "CACHE_ENABLED",
    "cache_key_secret": "CACHE_KEY_SECRET",
    "jobs_lease_seconds": "JOBS_LEASE_SECONDS",
    "jobs_poll_seconds": "JOBS_POLL_SECONDS",
    "jobs_ttl_seconds": "JOBS_TTL_SECONDS",
    "worker_shutdown_grace_seconds": "WORKER_SHUTDOWN_GRACE_SECONDS",
    "database_url": "DATABASE_URL",
}


def _settings(**overrides: object) -> Settings:
    # Isolate from the developer's root .env so tests are deterministic.
    payload = {_ALIASES.get(key, key): value for key, value in overrides.items()}
    return Settings.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "max_upload_mb",
        "max_text_chars",
        "max_keys_chars",
        "sync_max_text_chars",
        "sync_max_docx_bytes",
        "sync_max_docx_paragraphs",
        "db_pool_size",
        "db_max_overflow",
        "jobs_concurrency",
        "jobs_max_active",
        "jobs_max_attempts",
        "cache_max_entries",
        "cache_max_memory_mb",
        "cache_ttl_seconds",
        "cache_connect_timeout_seconds",
        "cache_socket_timeout_seconds",
        "jobs_lease_seconds",
        "jobs_poll_seconds",
        "jobs_ttl_seconds",
        "worker_shutdown_grace_seconds",
    ],
)
def test_non_positive_limits_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match="положительной"):
        _settings(**{field: 0})


def test_negative_ttl_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(jobs_ttl_seconds=-1)


def test_sqlite_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        _settings(database_url="sqlite:///jobs.db")


def test_lease_heartbeat_relation() -> None:
    with pytest.raises(ValidationError, match="трети времени аренды"):
        _settings(jobs_lease_seconds=3.0, jobs_poll_seconds=2.0)


def test_sync_thresholds_cannot_exceed_hard_limits() -> None:
    with pytest.raises(ValidationError, match="SYNC_MAX_TEXT_CHARS"):
        _settings(max_text_chars=100, sync_max_text_chars=101)
    with pytest.raises(ValidationError, match="SYNC_MAX_DOCX_BYTES"):
        _settings(max_upload_mb=1, sync_max_docx_bytes=2 * 1024 * 1024)


def test_lease_exceeds_db_connect_timeout() -> None:
    with pytest.raises(ValidationError, match="превышать таймаут подключения"):
        _settings(jobs_lease_seconds=1.0, jobs_poll_seconds=0.1, db_connect_timeout_seconds=5.0)


def test_worker_concurrency_cannot_exceed_pool_capacity() -> None:
    with pytest.raises(ValidationError, match="ёмкость пула"):
        _settings(jobs_concurrency=50, db_pool_size=2, db_max_overflow=1)


def test_cache_secret_required_when_enabled() -> None:
    with pytest.raises(ValidationError, match="CACHE_KEY_SECRET"):
        _settings(
            cache_enabled=True,
            cache_key_secret="short",  # noqa: S106 - deliberately invalid test value
            jobs_ttl_seconds=100_000.0,
        )
    # Valid secret is accepted.
    _settings(
        cache_enabled=True,
        cache_key_secret="a-long-stable-secret-value",  # noqa: S106
        jobs_ttl_seconds=100_000.0,
    )


def test_cache_ttl_is_positive_when_enabled() -> None:
    with pytest.raises(ValidationError, match="положительной"):
        _settings(
            cache_enabled=True,
            cache_key_secret="a-long-stable-secret-value",  # noqa: S106
            cache_ttl_seconds=0,
            jobs_ttl_seconds=3600.0,
        )

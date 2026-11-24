"""Application settings loaded from the environment / root .env."""

import json
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration (single source: root .env)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DocMasker API"
    backend_port: int = Field(default=8000, validation_alias="BACKEND_PORT")
    #: Removed together with the env-var label override (ADR-0001): a config
    #: that still sets MASK_LABELS fails loudly instead of being ignored.
    mask_labels_removed: str | None = Field(default=None, validation_alias="MASK_LABELS")
    # Resource limits for the file/text endpoints. The Next proxy may enforce
    # the same values for early 413s, but the backend is the real boundary.
    max_upload_mb: int = Field(default=25, validation_alias="UPLOAD_MAX_MB")
    max_text_chars: int = Field(default=200_000, validation_alias="MAX_TEXT_CHARS")
    max_keys_chars: int = Field(default=1_000_000, validation_alias="MAX_KEYS_CHARS")
    # --- Durable jobs (ADR-0002 / PostgreSQL substrate ADR-0003) -----------
    #: PostgreSQL is the authoritative job store; no SQLite fallback remains.
    database_url: str = Field(
        default="postgresql+psycopg://dm:dm@localhost:5432/dm",
        validation_alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, validation_alias="DB_MAX_OVERFLOW")
    db_connect_timeout_seconds: float = Field(default=5.0, validation_alias="DB_CONNECT_TIMEOUT_SECONDS")
    #: Private artifact spool (shared volume, single-host boundary).
    jobs_dir: str = Field(default="/app/jobs", validation_alias="JOBS_DIR")
    #: Infrastructure settings (Valkey is a disposable cache index, Phase 3).
    valkey_url: str = Field(default="redis://valkey:6379/0", validation_alias="VALKEY_URL")
    cache_enabled: bool = Field(default=False, validation_alias="CACHE_ENABLED")
    cache_ttl_seconds: float = Field(default=86400.0, validation_alias="CACHE_TTL_SECONDS")
    cache_namespace_version: int = Field(default=1, validation_alias="CACHE_NAMESPACE_VERSION")
    #: Stable HMAC secret: rotating it intentionally invalidates all entries.
    cache_key_secret: str = Field(default="", validation_alias="CACHE_KEY_SECRET")
    cache_connect_timeout_seconds: float = Field(default=2.0, validation_alias="CACHE_CONNECT_TIMEOUT_SECONDS")
    cache_socket_timeout_seconds: float = Field(default=2.0, validation_alias="CACHE_SOCKET_TIMEOUT_SECONDS")
    cache_max_entries: int = Field(default=100_000, validation_alias="CACHE_MAX_ENTRIES")
    cache_max_memory_mb: int = Field(default=256, validation_alias="CACHE_MAX_MEMORY_MB")
    #: Bump when detection/masking semantics change the output (§4.3).
    masking_ruleset_version: int = Field(default=1, validation_alias="MASKING_RULESET_VERSION")
    #: Bump when the result manifest / archive contract changes (§4.3).
    result_manifest_version: int = Field(default=1, validation_alias="RESULT_MANIFEST_VERSION")
    #: Validated contents of active exception/lexicon files (Phase 4). The
    #: value "auto" means the digest is computed from the loaded snapshot.
    masking_config_digest: str = Field(default="auto", validation_alias="MASKING_CONFIG_DIGEST")
    masking_config_dir: str = Field(default="/app/config/masking", validation_alias="MASKING_CONFIG_DIR")
    keys_format_version: int = Field(default=1, validation_alias="KEYS_FORMAT_VERSION")
    docx_transformer_version: int = Field(default=1, validation_alias="DOCX_TRANSFORMER_VERSION")
    worker_shutdown_grace_seconds: float = Field(default=30.0, validation_alias="WORKER_SHUTDOWN_GRACE_SECONDS")
    #: Inputs at or below these thresholds stay synchronous (baseline-anchored).
    sync_max_text_chars: int = Field(default=20_000, validation_alias="SYNC_MAX_TEXT_CHARS")
    sync_max_docx_bytes: int = Field(default=1024 * 1024, validation_alias="SYNC_MAX_DOCX_BYTES")
    sync_max_docx_paragraphs: int = Field(default=200, validation_alias="SYNC_MAX_DOCX_PARAGRAPHS")
    #: Bounded queue / worker (one warm masker per thread).
    jobs_max_active: int = Field(default=16, validation_alias="JOBS_MAX_ACTIVE")
    jobs_concurrency: int = Field(default=2, validation_alias="JOBS_CONCURRENCY")
    jobs_max_attempts: int = Field(default=3, validation_alias="JOBS_MAX_ATTEMPTS")
    jobs_lease_seconds: float = Field(default=30.0, validation_alias="JOBS_LEASE_SECONDS")
    jobs_poll_seconds: float = Field(default=1.0, validation_alias="JOBS_POLL_SECONDS")
    jobs_ttl_seconds: float = Field(default=3600.0, validation_alias="JOBS_TTL_SECONDS")
    # Defaults to local dev origins. Only relevant when the browser talks to
    # the backend directly (the Next frontend proxies /api/* server-side).
    backend_cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5001", "http://127.0.0.1:5001"],
        validation_alias="BACKEND_CORS_ORIGINS",
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("["):
            return json.loads(stripped)
        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    @field_validator("mask_labels_removed", mode="after")
    @classmethod
    def _reject_mask_labels(cls, value: str | None) -> str | None:
        if value:
            msg = (
                "MASK_LABELS удалена: типы сущностей задаются каталогом labels.yaml "
                "(см. GET /api/labels). Уберите переменную из окружения."
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_infrastructure(self) -> Settings:
        """Fail fast on incoherent DB/pool/lease settings (brief §2.3)."""
        for name in (
            "db_pool_size",
            "db_max_overflow",
            "max_upload_mb",
            "max_text_chars",
            "max_keys_chars",
            "sync_max_text_chars",
            "sync_max_docx_bytes",
            "sync_max_docx_paragraphs",
            "jobs_max_active",
            "jobs_concurrency",
            "jobs_max_attempts",
            "cache_namespace_version",
            "cache_max_entries",
            "cache_max_memory_mb",
            "masking_ruleset_version",
            "result_manifest_version",
            "keys_format_version",
            "docx_transformer_version",
        ):
            if getattr(self, name) <= 0:
                msg = f"Настройка {name.upper()} должна быть положительной."
                raise ValueError(msg)
        for name in (
            "db_connect_timeout_seconds",
            "jobs_lease_seconds",
            "jobs_poll_seconds",
            "jobs_ttl_seconds",
            "cache_ttl_seconds",
            "cache_connect_timeout_seconds",
            "cache_socket_timeout_seconds",
            "worker_shutdown_grace_seconds",
        ):
            if getattr(self, name) <= 0:
                msg = f"Настройка {name.upper()} должна быть положительной."
                raise ValueError(msg)
        if not self.database_url.startswith("postgresql"):
            msg = "DATABASE_URL должен указывать на PostgreSQL (SQLite больше не поддерживается)."
            raise ValueError(msg)
        if self.jobs_poll_seconds >= self.jobs_lease_seconds / 3:
            msg = "Интервал опроса воркера должен быть меньше трети времени аренды."
            raise ValueError(msg)
        if self.cache_enabled and self.cache_ttl_seconds <= 0:
            msg = "CACHE_TTL_SECONDS должен быть положительным при включённом кэше."
            raise ValueError(msg)
        if self.cache_enabled and len(self.cache_key_secret) < 16:
            msg = "CACHE_KEY_SECRET должен быть стабильным секретом длиной не менее 16 символов."
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

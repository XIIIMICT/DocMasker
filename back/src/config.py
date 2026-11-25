"""Application settings loaded from the environment / root .env."""

import json
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


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
    #: ReportCollector pattern: .env keeps POSTGRES_HOST=localhost for host-side
    #: scripts (seed/migrations); compose overrides it with POSTGRES_HOST_DOCKER
    #: (the compose service name). DATABASE_URL is an optional explicit override
    #: used by CI, otherwise the URL is built from the components below.
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    postgres_user: str = Field(default="dm", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="dm", validation_alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="dm", validation_alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=15433, validation_alias="POSTGRES_PORT")
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
    docx_transformer_version: int = Field(default=2, validation_alias="DOCX_TRANSFORMER_VERSION")
    worker_shutdown_grace_seconds: float = Field(default=30.0, validation_alias="WORKER_SHUTDOWN_GRACE_SECONDS")
    #: Dedicated, non-blocking Prometheus listener in the worker process.
    worker_metrics_port: int = Field(default=8002, validation_alias="WORKER_METRICS_PORT")
    worker_metrics_host: str = Field(default="0.0.0.0", validation_alias="WORKER_METRICS_HOST")  # noqa: S104
    #: Inputs at or below these thresholds stay synchronous (baseline-anchored).
    sync_max_text_chars: int = Field(default=20_000, validation_alias="SYNC_MAX_TEXT_CHARS")
    sync_max_docx_bytes: int = Field(default=1024 * 1024, validation_alias="SYNC_MAX_DOCX_BYTES")
    sync_max_docx_paragraphs: int = Field(default=200, validation_alias="SYNC_MAX_DOCX_PARAGRAPHS")
    #: Phase 7 §9.1: bounded windowing for long-text detection. `AddrExtractor`
    #: is superlinear on a whole document, so text above `detection_min_chars`
    #: is split into overlapping structural windows and spans are re-based to
    #: absolute offsets. Values are tuned via `benchmarks/profile_detection.py`.
    detection_min_chars: int = Field(default=8_000, validation_alias="DETECTION_MIN_CHARS")
    detection_chunk_chars: int = Field(default=4_096, validation_alias="DETECTION_CHUNK_CHARS")
    detection_overlap_chars: int = Field(default=512, validation_alias="DETECTION_OVERLAP_CHARS")
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
            "detection_min_chars",
            "detection_chunk_chars",
            "detection_overlap_chars",
            "jobs_max_active",
            "jobs_concurrency",
            "jobs_max_attempts",
            "cache_namespace_version",
            "worker_metrics_port",
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
        if self.worker_metrics_port > 65535:
            msg = "WORKER_METRICS_PORT должен быть не более 65535."
            raise ValueError(msg)
        if not self.worker_metrics_host.strip():
            msg = "WORKER_METRICS_HOST не должен быть пустым."
            raise ValueError(msg)
        if self.database_url is not None and not self.database_url.startswith("postgresql"):
            msg = "DATABASE_URL должен указывать на PostgreSQL (SQLite больше не поддерживается)."
            raise ValueError(msg)
        if self.database_url is None:
            missing = [
                name
                for name, value in {
                    "POSTGRES_USER": self.postgres_user,
                    "POSTGRES_PASSWORD": self.postgres_password,
                    "POSTGRES_DB": self.postgres_db,
                    "POSTGRES_HOST": self.postgres_host,
                }.items()
                if not value
            ]
            if missing:
                msg = f"Не заданы обязательные переменные: {', '.join(missing)}."
                raise ValueError(msg)
        if self.jobs_poll_seconds >= self.jobs_lease_seconds / 3:
            msg = "Интервал опроса воркера должен быть меньше трети времени аренды."
            raise ValueError(msg)
        # §10 config validation: cross-field resource/timing sanity.
        if self.sync_max_text_chars > self.max_text_chars:
            msg = "SYNC_MAX_TEXT_CHARS не должен превышать жёсткий лимит MAX_TEXT_CHARS."
            raise ValueError(msg)
        if self.sync_max_docx_bytes > self.max_upload_mb * 1024 * 1024:
            msg = "SYNC_MAX_DOCX_BYTES не должен превышать жёсткий лимит UPLOAD_MAX_MB."
            raise ValueError(msg)
        if self.detection_overlap_chars >= self.detection_chunk_chars:
            msg = "DETECTION_OVERLAP_CHARS должен быть меньше DETECTION_CHUNK_CHARS."
            raise ValueError(msg)
        if self.jobs_lease_seconds <= self.db_connect_timeout_seconds:
            msg = "Время аренды задания должно превышать таймаут подключения к БД."
            raise ValueError(msg)
        if self.jobs_concurrency > self.db_pool_size + self.db_max_overflow:
            msg = "JOBS_CONCURRENCY не должен превышать ёмкость пула PostgreSQL."
            raise ValueError(msg)
        if self.cache_enabled and self.cache_ttl_seconds <= 0:
            msg = "CACHE_TTL_SECONDS должен быть положительным при включённом кэше."
            raise ValueError(msg)
        if self.cache_enabled and len(self.cache_key_secret) < 16:
            msg = "CACHE_KEY_SECRET должен быть стабильным секретом длиной не менее 16 символов."
            raise ValueError(msg)
        return self

    @property
    def sqlalchemy_database_url(self) -> str:
        """Explicit DATABASE_URL (CI) or a URL built from POSTGRES_* components."""
        if self.database_url is not None:
            return self.database_url
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()

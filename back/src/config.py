"""Application settings loaded from the environment / root .env."""

import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.masking.constants import resolve_labels


class Settings(BaseSettings):
    """Environment-driven configuration (single source: root .env)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DocMasker API"
    backend_port: int = Field(default=8000, validation_alias="BACKEND_PORT")
    #: Pipe-separated override of the active PII label set (see `constants.py`).
    mask_labels: str | None = Field(default=None, validation_alias="MASK_LABELS")
    # Resource limits for the file/text endpoints. The Next proxy may enforce
    # the same values for early 413s, but the backend is the real boundary.
    max_upload_mb: int = Field(default=25, validation_alias="UPLOAD_MAX_MB")
    max_text_chars: int = Field(default=200_000, validation_alias="MAX_TEXT_CHARS")
    max_keys_chars: int = Field(default=1_000_000, validation_alias="MAX_KEYS_CHARS")
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

    @field_validator("mask_labels", mode="after")
    @classmethod
    def _split_labels(cls, value: str | None) -> str | None:
        if not value:
            return None
        # resolve_labels rejects duplicates/unknown labels at startup (fail fast)
        # before the masker ever runs with a half-valid configuration.
        resolved = resolve_labels(value.split("|"))
        return "|".join(resolved)


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Shared job infrastructure and sync/job threshold (ADR-0002)."""

from functools import lru_cache

from src.config import Settings, get_settings

from .repository import JobRepository
from .storage import ArtifactStore


@lru_cache(maxsize=1)
def job_repository() -> JobRepository:
    """One repository per web process (state itself lives in the DB file)."""
    return JobRepository(get_settings())


@lru_cache(maxsize=1)
def artifact_store() -> ArtifactStore:
    """One artifact spool handle per process."""
    return ArtifactStore(get_settings().jobs_dir)


def should_use_job(*, text: str | None = None, data: bytes | None = None, paragraph_count: int = 0) -> bool:
    """
    Whether an input must go to the durable job path instead of a sync request.

    Thresholds are configurable (ADR-0002 decisions, baseline-anchored):
    text > SYNC_MAX_TEXT_CHARS, DOCX > SYNC_MAX_DOCX_BYTES or more than
    SYNC_MAX_DOCX_PARAGRAPHS paragraphs.
    """
    settings: Settings = get_settings()
    if text is not None and len(text) > settings.sync_max_text_chars:
        return True
    if data is not None and len(data) > settings.sync_max_docx_bytes:
        return True
    return paragraph_count > settings.sync_max_docx_paragraphs

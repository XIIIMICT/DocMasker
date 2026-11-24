"""
Bridge between synchronous endpoints and the job queue (ADR-0002).

Kept import-light: `masking.router` imports this module, while `jobs.api`
imports `masking.router.upload_limits` — a direct router→api import would
create a cycle.
"""

from src.config import Settings, get_settings

from .dependencies import artifact_store, job_repository, should_use_job
from .models import JobKind
from .schemas import JobAccepted


def job_urls(job_id: str) -> tuple[str, str]:
    """(status_url, result_url) for an accepted job."""
    return f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/result"


def accepted(job_id: str) -> JobAccepted:
    """Build the 202 response payload."""
    status_url, result_url = job_urls(job_id)
    return JobAccepted(job_id=job_id, status="queued", status_url=status_url, result_url=result_url)


def enqueue(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None = None,
    filename: str = "",
    profile_id: str | None = None,
) -> JobAccepted:
    """
    Staged, atomic enqueue (brief §3.3).

    1. insert a `staging` row (reserves a queue slot; worker cannot claim it);
    2. stream inputs/keys/metadata to unique private files (atomic writes);
    3. publish `staging -> queued` with all artifact references in one short DB
       transaction. On any failure the staging row is aborted and the artifacts
       cleaned up idempotently, so no partially staged job is ever exposed.
    """
    import hashlib

    settings: Settings = get_settings()
    store = artifact_store()
    repo = job_repository()
    digest = hashlib.sha256(input_bytes)
    if keys_bytes is not None:
        digest.update(b"\0keys\0")
        digest.update(keys_bytes)
    cache_key = digest.hexdigest()

    staged = repo.create_staging(
        kind,
        max_active=settings.jobs_max_active,
        max_attempts=settings.jobs_max_attempts,
        cache_key=cache_key,
    )
    try:
        store.write(staged.id, "input", input_bytes)
        store.write(staged.id, "name", filename.encode("utf-8"))
        store.write(staged.id, "kind", kind.encode("ascii"))
        if keys_bytes is not None:
            store.write(staged.id, "keys", keys_bytes)
        if profile_id is not None:
            store.write(staged.id, "profile", profile_id.encode("ascii"))
    except Exception:
        repo.abort_staging(staged.id, "staging_failed")
        store.delete(staged.id)
        raise
    try:
        repo.publish_staging(
            staged.id,
            input_artifact="input",
            keys_artifact="keys" if keys_bytes is not None else None,
            ttl_seconds=settings.jobs_ttl_seconds,
        )
    except Exception:
        repo.abort_staging(staged.id, "staging_failed")
        store.delete(staged.id)
        raise
    return accepted(staged.id)


def needs_job(*, text: str | None = None, data: bytes | None = None, paragraph_count: int = 0) -> bool:
    """Public wrapper over the configurable threshold."""
    return should_use_job(text=text, data=data, paragraph_count=paragraph_count)


def count_docx_paragraphs(data: bytes) -> int:
    """
    Cheap paragraph estimate before the full python-docx model is built.

    Counting `<w:p ` occurrences in `word/document.xml` gives the threshold
    check without parsing the document. A malformed package returns 0 — the
    real validation still runs on the sync path / at job submission.
    """
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except KeyError, zipfile.BadZipFile:
        return 0
    return xml.count(b"<w:p ") + xml.count(b"<w:p>")

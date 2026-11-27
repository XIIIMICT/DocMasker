"""
Bridge between synchronous endpoints and the job queue (ADR-0002).

Kept import-light: `masking.router` imports this module, while `jobs.api`
imports `masking.router.upload_limits` — a direct router→api import would
create a cycle.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING

from src.config import Settings, get_settings

from .dependencies import artifact_store, job_repository, should_use_job
from .models import JobKind
from .readiness import check_artifact_storage
from .repository import IllegalTransitionError
from .schemas import JobAccepted

if TYPE_CHECKING:
    from .cache import CacheFingerprint
    from .repository import JobRepository
    from .storage import ArtifactStore


def _touch_or_raise(repo: JobRepository, job_id: str) -> None:
    """Renew a staging lease; if recovery won, abort this publisher."""
    if not repo.touch_staging(job_id):
        msg = "staging lease lost during upload"
        raise IllegalTransitionError(msg)


def job_urls(job_id: str) -> tuple[str, str]:
    """(status_url, result_url) for an accepted job."""
    return f"/api/jobs/{job_id}", f"/api/jobs/{job_id}/result"


def accepted(job_id: str) -> JobAccepted:
    """Build the 202 response payload."""
    status_url, result_url = job_urls(job_id)
    return JobAccepted(job_id=job_id, status="queued", status_url=status_url, result_url=result_url)


def _digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_of_path(path: Path) -> str:
    """Hash a staged file in bounded chunks (§8.5: no full read into RAM)."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _enqueue(
    kind: JobKind,
    *,
    fingerprint: CacheFingerprint,
    keys_bytes: bytes | None,
    filename: str,
    write_input: Callable[[ArtifactStore, str], None],
) -> JobAccepted:
    """
    Staged, atomic enqueue (brief §3.3), shared by the bytes and path paths.

    0. check artifact storage readiness before any DB row or file exists;
    1. insert a `staging` row (reserves a queue slot; worker cannot claim it);
    2. stream inputs/keys/metadata to unique private files (atomic writes);
    3. publish `staging -> queued` with all artifact references in one short DB
       transaction. On any failure the staging row is aborted and the artifacts
       cleaned up idempotently, so no partially staged job is ever exposed.
    """
    from .cache import build_cache_key

    settings: Settings = get_settings()
    check_artifact_storage(settings.jobs_dir, settings.jobs_min_free_bytes)
    store = artifact_store()
    repo = job_repository()
    cache_key = build_cache_key(settings, fingerprint)
    staged, created = repo.create_staging(
        kind,
        max_active=settings.jobs_max_active,
        max_attempts=settings.jobs_max_attempts,
        cache_key=cache_key,
    )
    if not created:
        # An identical job is already staging/queued/running: attach to it
        # instead of duplicating expensive work (§4.5).
        return accepted(staged.id)
    try:
        _touch_or_raise(repo, staged.id)
        write_input(store, staged.id)
        store.write(staged.id, "name", filename.encode("utf-8"))
        store.write(staged.id, "kind", kind.encode("ascii"))
        if keys_bytes is not None:
            store.write(staged.id, "keys", keys_bytes)
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


def enqueue(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None = None,
    filename: str = "",
) -> JobAccepted:
    """Enqueue an in-memory (small text / sync-overflow) operation."""
    from .cache import fingerprint_for

    key_material = fingerprint_for(
        kind,
        input_bytes=input_bytes,
        keys_bytes=keys_bytes,
        filename=filename,
    )
    return _enqueue(
        kind,
        fingerprint=key_material,
        keys_bytes=keys_bytes,
        filename=filename,
        write_input=lambda store, job_id: store.write(job_id, "input", input_bytes),
    )


def enqueue_from_path(
    kind: JobKind,
    *,
    input_path: Path,
    input_digest: str,
    keys_bytes: bytes | None = None,
    filename: str = "",
) -> JobAccepted:
    """
    Enqueue a large staged upload without ever loading it into one `bytes`.

    The versioned cache key uses the streamed SHA-256 digest of the exact file
    (§4.2); `ArtifactStore.copy_from` streams the file into the spool.
    """
    from .cache import fingerprint_for

    key_material = fingerprint_for(
        kind,
        input_bytes=None,
        input_digest=input_digest,
        keys_bytes=keys_bytes,
        filename=filename,
    )
    return _enqueue(
        kind,
        fingerprint=key_material,
        keys_bytes=keys_bytes,
        filename=filename,
        write_input=lambda store, job_id: store.copy_from(
            job_id,
            "input",
            input_path,
            on_chunk=lambda: _touch_or_raise(job_repository(), job_id),
        ),
    )


def needs_job(
    *, text: str | None = None, data: bytes | None = None, size: int | None = None, paragraph_count: int = 0
) -> bool:
    """Public wrapper over the configurable threshold."""
    return should_use_job(text=text, data=data, size=size, paragraph_count=paragraph_count)


def _count_paragraphs_xml(xml: IO[bytes]) -> int:
    """Count `w:p` end events incrementally from a ZipExtFile-like stream."""
    from lxml import etree  # ty: ignore[missing-module-member]

    count = 0
    context = etree.iterparse(
        xml,
        events=("end",),
        recover=True,
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
    )
    for _event, element in context:
        if etree.QName(element).localname == "p":
            count += 1
        element.clear()
        while element.getprevious() is not None:
            del element.getparent()[0]
    return count


def count_docx_paragraphs_path(path: Path) -> int:
    """Count paragraphs from a staged path without loading the whole part."""
    import zipfile

    from lxml import etree  # ty: ignore[missing-module-member]

    try:
        with zipfile.ZipFile(path) as archive, archive.open("word/document.xml") as xml:
            return _count_paragraphs_xml(xml)
    except KeyError, zipfile.BadZipFile, etree.XMLSyntaxError, ValueError:
        return 0


def count_docx_paragraphs(data: bytes) -> int:
    """
    Streaming paragraph estimate (Phase 6 §10).

    Opens `word/document.xml` as a `ZipExtFile` and counts `w:p` end events
    with an incremental pull parser instead of `archive.read()` + a byte
    `count()` over the whole XML part. A malformed package returns 0; real
    validation still runs on the sync path / at job submission. Namespace
    prefixes are irrelevant — the local name `p` is what matters.
    """
    import io
    import zipfile

    from lxml import etree  # ty: ignore[missing-module-member]

    try:
        with (
            zipfile.ZipFile(io.BytesIO(data)) as archive,
            archive.open("word/document.xml") as xml,
        ):
            return _count_paragraphs_xml(xml)
    except KeyError, zipfile.BadZipFile, etree.XMLSyntaxError, ValueError:
        return 0

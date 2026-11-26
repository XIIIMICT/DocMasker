"""Masking and unmasking API router."""

import base64
import json
import re
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.jobs.cache import build_cache_key, canonical_json, fingerprint_for
from src.jobs.dependencies import result_cache
from src.jobs.repository import JobQueueFullError
from src.jobs.routing import count_docx_paragraphs, count_docx_paragraphs_path, enqueue, enqueue_from_path, needs_job
from src.masking.config import SCHEMA_VERSION, get_config
from src.metrics import observe_upload
from src.uploads import UploadTooLargeError, cleanup, stage_upload

from .archives import mark_cache_hit, mask_archive, restore_archive, safe_stem
from .docx_service import CoverageReport, OoxmlPackageError, mask_docx_in_memory, unmask_docx_in_memory
from .docx_validation import DocxValidationError, upload_limits, validate_docx_package, validate_docx_package_path
from .executor import ExecutorBusyError, run_blocking, run_cpu
from .mapping import KeysValidationError, MaskKeys, placeholders_from_document, validate_keys_yaml
from .ooxml.manifest import build_mask_manifest, build_unmask_manifest
from .rule_based import get_masker
from .schemas import (
    LabelsResponse,
    MaskedEntity,
    MaskRequest,
    MaskResponse,
    UnmaskRequest,
    UnmaskResponse,
)
from .service import mask_document, mask_stats, unmask_document

if TYPE_CHECKING:
    from src.jobs.models import JobKind

router = APIRouter(tags=["masking"])


async def _read_upload(file: UploadFile) -> bytes:
    """Read a sync upload and count the bounded outcome (never the content)."""
    try:
        source = await file.read()
    except Exception:
        observe_upload(outcome="error")
        raise
    observe_upload(outcome="ok")
    return source


async def _admit_file(
    file: UploadFile,
    *,
    kind: JobKind,
    keys_bytes: bytes | None,
    filename: str | None,
) -> bytes | JSONResponse:
    """
    Stream, validate and route one DOCX upload (P1.1).

    Hard byte/package limits are enforced from the staged path before a cache
    lookup or job publication; oversized inputs are enqueued directly from the
    staged file, never materialised as one `bytes` object.
    """
    settings = get_settings()
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )
    try:
        staged = await stage_upload(file, limits=upload_limits())
    except UploadTooLargeError as error:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error)) from error
    try:
        observe_upload(outcome="ok")
        if staged.size == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")
        try:
            await run_blocking(validate_docx_package_path, staged.path, upload_limits())
        except DocxValidationError as error:
            raise HTTPException(status_code=error.http_status, detail=str(error)) from error
        paragraphs = (
            0
            if staged.size > settings.sync_max_docx_bytes
            else await run_blocking(count_docx_paragraphs_path, staged.path)
        )
        if needs_job(size=staged.size, paragraph_count=paragraphs):
            try:
                accepted = await run_blocking(
                    enqueue_from_path,
                    kind,
                    input_path=staged.path,
                    input_digest=staged.sha256,
                    keys_bytes=keys_bytes,
                    filename=filename or file.filename,
                )
            except JobQueueFullError as error:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
            return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted.model_dump())
        # Bounded by sync_max_docx_bytes; materialise only after validation.
        return staged.path.read_bytes()
    finally:
        cleanup(staged)


@router.get("/labels", response_model=LabelsResponse)
async def labels_endpoint() -> LabelsResponse:
    """Return the single canonical label catalogue (read-only, runtime source)."""
    return LabelsResponse(
        schema_version=SCHEMA_VERSION,
        labels=[{"id": label, "title": title} for label, title in get_config().labels.entries],
    )


_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_CONTENT_TYPE = "application/zip"


def _ascii_attachment_name(name: str) -> str:
    """Fallback filename for `filename=` (must be ASCII/latin-1 safe)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.()-]", "_", name)
    return ascii_name or "masked.docx"


def _content_disposition(download_name: str) -> str:
    """RFC 5987 attachment header (ASCII fallback + UTF-8 name for Cyrillic)."""
    return f"attachment; filename=\"{_ascii_attachment_name(download_name)}\"; filename*=UTF-8''{quote(download_name)}"


def _download_headers(download_name: str) -> dict[str, str]:
    """Transport-only headers: semantic metadata lives in `manifest.json` (§7.3)."""
    return {
        "Content-Disposition": _content_disposition(download_name),
        "Content-Type": _ZIP_CONTENT_TYPE,
        "Cache-Control": "no-store",
    }


def _manifest_ctx() -> tuple[int, str, int]:
    """Version/digest fields shared by sync and job manifests."""
    settings = get_settings()
    return (
        settings.masking_ruleset_version,
        get_config().digest,
        settings.cache_namespace_version,
    )


def _cache_key(kind: str, *, input_bytes: bytes, keys_bytes: bytes | None, filename: str) -> str:
    """Versioned HMAC key for a fully validated synchronous operation."""
    return build_cache_key(
        get_settings(),
        fingerprint_for(kind, input_bytes=input_bytes, keys_bytes=keys_bytes, filename=filename),
    )


def _cache_get(key: str) -> bytes | None:
    """Cache hit is only served after input validation (§4.5)."""
    return result_cache().get(key)


def _cache_put(key: str, data: bytes) -> None:
    """Populate only after a fully successful computation."""
    result_cache().put(key, data)


# §10: DB/Valkey/ZIP/XML/filesystem work must not run on the ASGI event loop.
# These thin wrappers push every short blocking call onto the dedicated IO pool.
async def _cache_get_async(key: str) -> bytes | None:
    return await run_blocking(_cache_get, key)


async def _cache_put_async(key: str, data: bytes) -> None:
    await run_blocking(_cache_put, key, data)


async def _cache_file_async(key: str, archive: bytes, headers: dict[str, str]) -> None:
    """Pack (base64/JSON) + store the ZIP off the event loop (§10)."""

    def pack_and_put() -> None:
        _cache_put(key, _pack_file_result(archive, headers))

    await run_blocking(pack_and_put)


async def _invalidate_async(key: str) -> None:
    def invalidate() -> None:
        result_cache().invalidate(key)

    await run_blocking(invalidate)


async def _validate_docx_async(source: bytes) -> None:
    await run_blocking(validate_docx_package, source, upload_limits())


async def _count_paragraphs_async(source: bytes) -> int:
    return await run_blocking(count_docx_paragraphs, source)


async def _job_overflow_async(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None,
    filename: str,
) -> JSONResponse:
    """Enqueue off the event loop (DB + staged artifact writes)."""
    try:
        accepted = await run_blocking(
            enqueue,
            kind,
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename=filename,
        )
    except JobQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted.model_dump())


def _pack_file_result(archive: bytes, headers: dict[str, str]) -> bytes:
    """Cache the ZIP plus its non-semantic response headers as one artifact."""
    return canonical_json(
        {
            "archive_b64": base64.b64encode(archive).decode("ascii"),
            "headers": headers,
        }
    ).encode("utf-8")


def _unpack_file_result(cached: bytes) -> tuple[bytes, dict[str, str]] | None:
    try:
        payload = json.loads(cached)
        return base64.b64decode(payload["archive_b64"]), dict(payload["headers"])
    except Exception:
        return None


def _build_mask_archive(
    *,
    source: bytes,
    masked_bytes: bytes,
    keys_yaml: str,
    entities: list[MaskedEntity],
    coverage: CoverageReport,
    base: str,
) -> tuple[bytes, dict[str, str]]:
    """Sync helper: build manifest + ZIP off the event loop (§10)."""
    document_name = f"{base} (замаскированный).docx"
    keys_name = f"{base} (ключи демаскировки).yaml"
    ruleset, digest, namespace = _manifest_ctx()
    manifest = build_mask_manifest(
        source_bytes=source,
        document_name=document_name,
        keys_name=keys_name,
        entities=[entity.model_dump() for entity in entities],
        coverage=coverage.as_manifest(),
        cache_hit=False,
        namespace_version=namespace,
        masking_ruleset_version=ruleset,
        exception_config_digest=digest,
    )
    archive = mask_archive(masked_bytes, keys_yaml, base, manifest)
    return archive, _download_headers(f"{base} (замаскированный).zip")


def _build_restore_archive(
    *,
    source: bytes,
    restored: bytes,
    unused: list[str],
    coverage: CoverageReport,
    base: str,
) -> tuple[bytes, dict[str, str]]:
    """Sync helper: build manifest + ZIP off the event loop (§10)."""
    document_name = f"{base} (демаскированный).docx"
    ruleset, digest, namespace = _manifest_ctx()
    manifest = build_unmask_manifest(
        source_bytes=source,
        document_name=document_name,
        unused_keys=unused,
        coverage=coverage.as_manifest(),
        cache_hit=False,
        namespace_version=namespace,
        masking_ruleset_version=ruleset,
        exception_config_digest=digest,
    )
    archive = restore_archive(restored, base, manifest)
    return archive, _download_headers(f"{base} (демаскированный).zip")


def _busy() -> HTTPException:
    """429 for the bounded synchronous executor."""
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Сервер занят. Повторите позже.")


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(request: MaskRequest) -> MaskResponse | JSONResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    limit = get_settings().max_text_chars
    if len(request.text) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Текст больше допустимого ({limit} символов).",
        )
    input_bytes = request.text.encode("utf-8")
    if needs_job(text=request.text):
        return await _job_overflow_async(
            "mask_text",
            input_bytes=input_bytes,
            keys_bytes=None,
            filename="",
        )
    key = _cache_key("mask_text", input_bytes=input_bytes, keys_bytes=None, filename="")
    cached = await _cache_get_async(key)
    if cached is not None:
        try:
            return MaskResponse.model_validate_json(cached)
        except Exception:
            await _invalidate_async(key)
    try:
        masked, entities, keys = await run_cpu(mask_document, request)
    except ExecutorBusyError as error:
        raise _busy() from error
    response = MaskResponse(text=masked, stats=mask_stats(entities), keys=keys.to_yaml())
    await _cache_put_async(key, response.model_dump_json().encode("utf-8"))
    return response


@router.post("/mask-file")
async def mask_file_endpoint(
    file: Annotated[UploadFile, File(description="DOCX file to mask")],
) -> Response:
    """Upload a .docx, mask PII in place (formatting preserved), return a ZIP of result + keys."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )

    admitted = await _admit_file(file, kind="mask_file", keys_bytes=None, filename=file.filename)
    if isinstance(admitted, JSONResponse):
        return admitted
    source = admitted

    # Validate the package BEFORE a cache lookup (a hit must not bypass validation).
    key = _cache_key("mask_file", input_bytes=source, keys_bytes=None, filename=file.filename)
    cached = await _cache_get_async(key)
    if cached is not None:
        unpacked = _unpack_file_result(cached)
        if unpacked is not None:
            archive, headers = unpacked
            return Response(content=mark_cache_hit(archive), media_type=_ZIP_CONTENT_TYPE, headers=headers)
        await _invalidate_async(key)

    def _mask_file() -> tuple[bytes, list[MaskedEntity], MaskKeys, CoverageReport]:
        return mask_docx_in_memory(source, get_masker())

    try:
        masked_bytes, entities, keys, coverage = await run_cpu(_mask_file)
    except ExecutorBusyError as error:
        raise _busy() from error
    except OoxmlPackageError as error:
        # Strict blocking error for genuinely unprocessable content (§6.5).
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    base = safe_stem(file.filename)
    archive, headers = await run_blocking(
        _build_mask_archive,
        source=source,
        masked_bytes=masked_bytes,
        keys_yaml=keys.to_yaml(),
        entities=entities,
        coverage=coverage,
        base=base,
    )
    await _cache_file_async(key, archive, headers)
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)


@router.post("/unmask", response_model=UnmaskResponse)
async def unmask_text_endpoint(request: UnmaskRequest) -> UnmaskResponse | JSONResponse:
    """Restore a masked text from its demasking keys."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    settings = get_settings()
    if len(request.text) > settings.max_text_chars:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Текст больше допустимого ({settings.max_text_chars} символов).",
        )
    if len(request.keys) > settings.max_keys_chars:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Ключи больше допустимых ({settings.max_keys_chars} символов).",
        )
    try:
        await run_blocking(validate_keys_yaml, request.keys)
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    input_bytes = request.text.encode("utf-8")
    keys_bytes = request.keys.encode("utf-8")
    if needs_job(text=request.text):
        return await _job_overflow_async(
            "unmask_text",
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename="",
        )
    key = _cache_key("unmask_text", input_bytes=input_bytes, keys_bytes=keys_bytes, filename="")
    cached = await _cache_get_async(key)
    if cached is not None:
        try:
            return UnmaskResponse.model_validate_json(cached)
        except Exception:
            await _invalidate_async(key)
    try:
        restored = await run_cpu(unmask_document, request)
    except ExecutorBusyError as error:
        raise _busy() from error
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    response = UnmaskResponse(text=restored)
    await _cache_put_async(key, response.model_dump_json().encode("utf-8"))
    return response


@router.post("/unmask-file")
async def unmask_file_endpoint(
    file: Annotated[UploadFile, File(description="Masked DOCX file to restore")],
    keys: Annotated[str, Form(description="Demasking keys as YAML")],
) -> Response:
    """Upload a masked .docx plus its keys, return a ZIP of the restored document + manifest."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )

    settings = get_settings()
    if len(keys) > settings.max_keys_chars:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Ключи больше допустимых ({settings.max_keys_chars} символов).",
        )

    try:
        placeholders = await run_blocking(placeholders_from_document, await run_blocking(validate_keys_yaml, keys))
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    keys_bytes = keys.encode("utf-8")
    admitted = await _admit_file(file, kind="unmask_file", keys_bytes=keys_bytes, filename=file.filename)
    if isinstance(admitted, JSONResponse):
        return admitted
    source = admitted
    key = _cache_key("unmask_file", input_bytes=source, keys_bytes=keys_bytes, filename=file.filename)
    cached = await _cache_get_async(key)
    if cached is not None:
        unpacked = _unpack_file_result(cached)
        if unpacked is not None:
            archive, headers = unpacked
            return Response(content=mark_cache_hit(archive), media_type=_ZIP_CONTENT_TYPE, headers=headers)
        await _invalidate_async(key)

    def _unmask_file() -> tuple[bytes, list[str], CoverageReport]:
        return unmask_docx_in_memory(source, placeholders)

    try:
        restored, unused, coverage = await run_cpu(_unmask_file)
    except ExecutorBusyError as error:
        raise _busy() from error
    except OoxmlPackageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    base = safe_stem(file.filename).removesuffix(" (замаскированный)")
    archive, headers = await run_blocking(
        _build_restore_archive,
        source=source,
        restored=restored,
        unused=unused,
        coverage=coverage,
        base=base,
    )
    await _cache_file_async(key, archive, headers)
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)

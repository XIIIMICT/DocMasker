"""Masking and unmasking API router."""

import base64
import json
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.jobs.cache import build_cache_key, canonical_json, fingerprint_for
from src.jobs.dependencies import result_cache
from src.jobs.repository import JobQueueFullError
from src.jobs.routing import count_docx_paragraphs, enqueue, needs_job
from src.masking.profiles import ProfilesValidationError, get_profile, list_profiles

from .archives import mask_archive, restore_archive
from .docx_service import mask_docx_in_memory, unmask_docx_in_memory
from .docx_validation import (
    DocxValidationError,
    unsupported_docx_parts,
    upload_limits,
    validate_docx_package,
)
from .executor import ExecutorBusyError, run_cpu
from .mapping import KeysValidationError, MaskKeys, placeholders_from_document, validate_keys_yaml
from .rule_based import get_masker_for
from .schemas import (
    MaskedEntity,
    MaskRequest,
    MaskResponse,
    ProfilesResponse,
    UnmaskRequest,
    UnmaskResponse,
)
from .service import mask_document, mask_stats, unmask_document

if TYPE_CHECKING:
    from src.jobs.models import JobKind

router = APIRouter(tags=["masking"])


@router.get("/profiles", response_model=ProfilesResponse)
async def profiles_endpoint() -> ProfilesResponse:
    """List the registered masking profiles (read-only metadata)."""
    return ProfilesResponse(
        profiles=[
            {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "display_name": profile.display_name,
                "labels": [{"id": meta.id, "title": meta.title, "order": meta.order} for meta in profile.labels],
            }
            for profile in list_profiles()
        ]
    )


_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_CONTENT_TYPE = "application/zip"


def _ascii_attachment_name(name: str) -> str:
    """Fallback filename for `filename=` (must be ASCII/latin-1 safe)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.()-]", "_", name)
    return ascii_name or "masked.docx"


def _label_breakdown(entities: list[MaskedEntity]) -> str:
    """Per-label counts as a JSON object, e.g. ``{"person": 2, "phone": 1}``."""
    return json.dumps(dict(Counter(entity.label for entity in entities)))


def _content_disposition(download_name: str) -> str:
    """RFC 5987 attachment header (ASCII fallback + UTF-8 name for Cyrillic)."""
    return f"attachment; filename=\"{_ascii_attachment_name(download_name)}\"; filename*=UTF-8''{quote(download_name)}"


def _attachment_headers(download_name: str, entities: list[MaskedEntity]) -> dict[str, str]:
    """Download headers shared by every masked-file response (name + counts)."""
    return {
        "Content-Disposition": _content_disposition(download_name),
        "X-Masked-Count": str(len(entities)),
        "X-Masked-Breakdown": _label_breakdown(entities),
    }


def _unsupported_header(data: bytes) -> str | None:
    """Build a short, safe warning code list (no values, no filenames)."""
    codes = unsupported_docx_parts(data)
    return ",".join(codes) if codes else None


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


def _busy() -> HTTPException:
    """429 for the bounded synchronous executor."""
    return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Сервер занят. Повторите позже.")


def _job_overflow(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None,
    filename: str,
    profile_id: str | None,
) -> JSONResponse:
    """Accept an oversized input as a durable job (202 + status/result URLs)."""
    try:
        accepted = enqueue(
            kind,
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename=filename,
            profile_id=profile_id,
        )
    except JobQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted.model_dump())


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(request: MaskRequest) -> MaskResponse | JSONResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    limit = get_settings().max_text_chars
    if len(request.text) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Текст больше допустимого ({limit} символов).",
        )
    input_bytes = request.text.encode("utf-8")
    if needs_job(text=request.text):
        return _job_overflow(
            "mask_text",
            input_bytes=input_bytes,
            keys_bytes=None,
            filename="",
            profile_id=request.profile_id,
        )
    key = _cache_key("mask_text", input_bytes=input_bytes, keys_bytes=None, filename="")
    cached = _cache_get(key)
    if cached is not None:
        try:
            return MaskResponse.model_validate_json(cached)
        except Exception:
            result_cache().invalidate(key)
    try:
        masked, entities, keys = await run_cpu(mask_document, request)
    except ExecutorBusyError as error:
        raise _busy() from error
    except ProfilesValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    response = MaskResponse(text=masked, stats=mask_stats(entities), keys=keys.to_yaml())
    _cache_put(key, response.model_dump_json().encode("utf-8"))
    return response


@router.post("/mask-file")
async def mask_file_endpoint(
    file: Annotated[UploadFile, File(description="DOCX file to mask")],
    profile_id: Annotated[str | None, Form(description="Masking profile id (default profile when omitted)")] = None,
) -> Response:
    """Upload a .docx, mask PII in place (formatting preserved), return a ZIP of result + keys."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )

    source = await file.read()
    if not source:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")
    if needs_job(data=source, paragraph_count=count_docx_paragraphs(source)):
        return _job_overflow(
            "mask_file",
            input_bytes=source,
            keys_bytes=None,
            filename=file.filename,
            profile_id=profile_id,
        )

    try:
        profile = get_profile(profile_id)
    except ProfilesValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    # Validate the package BEFORE a cache lookup (§4.5: a hit must not bypass
    # input/security validation).
    try:
        validate_docx_package(source, upload_limits())
    except DocxValidationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error
    key = _cache_key("mask_file", input_bytes=source, keys_bytes=None, filename=file.filename)
    cached = _cache_get(key)
    if cached is not None:
        unpacked = _unpack_file_result(cached)
        if unpacked is not None:
            archive, headers = unpacked
            return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)
        result_cache().invalidate(key)

    def _mask_file() -> tuple[bytes, list[MaskedEntity], MaskKeys]:
        return mask_docx_in_memory(source, get_masker_for(profile.profile_id))

    try:
        masked_bytes, entities, keys = await run_cpu(_mask_file)
    except ExecutorBusyError as error:
        raise _busy() from error

    base = Path(file.filename).stem
    archive = mask_archive(masked_bytes, keys.to_yaml(), base)
    headers = _attachment_headers(f"{base} (замаскированный).zip", entities)
    headers["Content-Type"] = _ZIP_CONTENT_TYPE
    unsupported = _unsupported_header(source)
    if unsupported:
        headers["X-Docx-Unsupported-Parts"] = unsupported
    _cache_put(key, _pack_file_result(archive, headers))
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)


@router.post("/unmask", response_model=UnmaskResponse)
async def unmask_text_endpoint(request: UnmaskRequest) -> UnmaskResponse | JSONResponse:
    """Restore a masked text from its demasking keys."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    settings = get_settings()
    if len(request.keys) > settings.max_keys_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Ключи больше допустимых ({settings.max_keys_chars} символов).",
        )
    try:
        validate_keys_yaml(request.keys)
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    input_bytes = request.text.encode("utf-8")
    keys_bytes = request.keys.encode("utf-8")
    if needs_job(text=request.text):
        return _job_overflow(
            "unmask_text",
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename="",
            profile_id=None,
        )
    key = _cache_key("unmask_text", input_bytes=input_bytes, keys_bytes=keys_bytes, filename="")
    cached = _cache_get(key)
    if cached is not None:
        try:
            return UnmaskResponse.model_validate_json(cached)
        except Exception:
            result_cache().invalidate(key)
    try:
        restored = await run_cpu(unmask_document, request)
    except ExecutorBusyError as error:
        raise _busy() from error
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    response = UnmaskResponse(text=restored)
    _cache_put(key, response.model_dump_json().encode("utf-8"))
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
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Ключи больше допустимых ({settings.max_keys_chars} символов).",
        )

    source = await file.read()
    if not source:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")
    if needs_job(data=source, paragraph_count=count_docx_paragraphs(source)):
        return _job_overflow(
            "unmask_file",
            input_bytes=source,
            keys_bytes=keys.encode("utf-8"),
            filename=file.filename,
            profile_id=None,
        )

    try:
        placeholders = placeholders_from_document(validate_keys_yaml(keys))
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    try:
        validate_docx_package(source, upload_limits())
    except DocxValidationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error

    keys_bytes = keys.encode("utf-8")
    key = _cache_key("unmask_file", input_bytes=source, keys_bytes=keys_bytes, filename=file.filename)
    cached = _cache_get(key)
    if cached is not None:
        unpacked = _unpack_file_result(cached)
        if unpacked is not None:
            archive, headers = unpacked
            return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)
        result_cache().invalidate(key)

    def _unmask_file() -> tuple[bytes, list[str]]:
        return unmask_docx_in_memory(source, placeholders)

    try:
        restored, unused = await run_cpu(_unmask_file)
    except ExecutorBusyError as error:
        raise _busy() from error
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error

    base = Path(file.filename).stem
    # A masked document is downloaded as "<base> (замаскированный).docx" and
    # uploaded back for restoring; drop that suffix so the restored file is
    # named "<base> (демаскированный).docx", not "... (замаскированный) (...)".
    base = base.removesuffix(" (замаскированный)")
    archive = restore_archive(restored, unused, base)
    download_name = f"{base} (демаскированный).zip"
    headers = {"Content-Disposition": _content_disposition(download_name), "Content-Type": _ZIP_CONTENT_TYPE}
    unsupported = _unsupported_header(source)
    if unsupported:
        headers["X-Docx-Unsupported-Parts"] = unsupported
    _cache_put(key, _pack_file_result(archive, headers))
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)

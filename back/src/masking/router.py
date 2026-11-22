"""Masking and unmasking API router."""

import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from src.config import get_settings
from src.masking.profiles import ProfilesValidationError, get_profile, list_profiles

from .docx_service import mask_docx_in_memory, unmask_docx_in_memory
from .docx_validation import DocxLimits, DocxValidationError, validate_docx_package
from .mapping import KeysValidationError, placeholders_from_document, validate_keys_yaml
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


def upload_limits() -> DocxLimits:
    """DOCX package limits derived from the settings (single source: root .env)."""
    settings = get_settings()
    return DocxLimits(
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        max_uncompressed_bytes=settings.max_upload_mb * 10 * 1024 * 1024,
    )


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


def _archive(masked_docx: bytes, keys_yaml: str, base: str) -> bytes:
    """
    Bundle the masked document and its keys into one ZIP.

    The front end unpacks it and offers the two files as separate downloads, so
    the user never sees the archive. It also keeps the two artifacts of one
    masking run together (a keys file without its document is useless).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base} (замаскированный).docx", masked_docx)
        archive.writestr(f"{base} (ключи демаскировки).yaml", keys_yaml)
    return buffer.getvalue()


def _restore_archive(restored_docx: bytes, unused_keys: list[str], base: str) -> bytes:
    """
    Bundle the restored document and a manifest of unapplied keys into one ZIP.

    The unused-keys metadata used to travel in the `X-Unused-Keys` header, but
    the list holds original PII values and outgrew every header size limit for
    large key sets — it now rides in the response body as a small JSON manifest.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base} (демаскированный).docx", restored_docx)
        archive.writestr(
            f"{base} (отчёт о демаскировке).json",
            json.dumps({"unused_keys": unused_keys}, ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(request: MaskRequest) -> MaskResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    limit = get_settings().max_text_chars
    if len(request.text) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Текст больше допустимого ({limit} символов).",
        )
    try:
        masked, entities, keys = mask_document(request)
    except ProfilesValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return MaskResponse(text=masked, stats=mask_stats(entities), keys=keys.to_yaml())


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

    try:
        profile = get_profile(profile_id)
    except ProfilesValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    try:
        validate_docx_package(source, upload_limits())
        masked_bytes, entities, keys = mask_docx_in_memory(source, get_masker_for(profile.profile_id))
    except DocxValidationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error

    base = Path(file.filename).stem
    archive = _archive(masked_bytes, keys.to_yaml(), base)
    headers = _attachment_headers(f"{base} (замаскированный).zip", entities)
    headers["Content-Type"] = _ZIP_CONTENT_TYPE
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)


@router.post("/unmask", response_model=UnmaskResponse)
async def unmask_text_endpoint(request: UnmaskRequest) -> UnmaskResponse:
    """Restore a masked text from its demasking keys."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    settings = get_settings()
    if len(request.text) > settings.max_text_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Текст больше допустимого ({settings.max_text_chars} символов).",
        )
    if len(request.keys) > settings.max_keys_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Ключи больше допустимых ({settings.max_keys_chars} символов).",
        )
    try:
        restored = unmask_document(request)
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return UnmaskResponse(text=restored)


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

    try:
        placeholders = placeholders_from_document(validate_keys_yaml(keys))
        validate_docx_package(source, upload_limits())
        restored, unused = unmask_docx_in_memory(source, placeholders)
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except DocxValidationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error

    base = Path(file.filename).stem
    # A masked document is downloaded as "<base> (замаскированный).docx" and
    # uploaded back for restoring; drop that suffix so the restored file is
    # named "<base> (демаскированный).docx", not "... (замаскированный) (...)".
    base = base.removesuffix(" (замаскированный)")
    archive = _restore_archive(restored, unused, base)
    download_name = f"{base} (демаскированный).zip"
    headers = {"Content-Disposition": _content_disposition(download_name), "Content-Type": _ZIP_CONTENT_TYPE}
    return Response(content=archive, media_type=_ZIP_CONTENT_TYPE, headers=headers)

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

from .docx_service import mask_docx_in_memory, unmask_docx_in_memory
from .mapping import KeysValidationError, placeholders_from_document, validate_keys_yaml
from .schemas import MaskedEntity, MaskRequest, MaskResponse, UnmaskRequest, UnmaskResponse
from .service import mask_document, mask_stats, unmask_document

router = APIRouter(tags=["masking"])

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


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(request: MaskRequest) -> MaskResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    masked, entities, keys = mask_document(request)
    return MaskResponse(text=masked, stats=mask_stats(entities), keys=keys.to_yaml())


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

    source = await file.read()
    if not source:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")

    masked_bytes, entities, keys = mask_docx_in_memory(source)

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
    """Upload a masked .docx plus its keys, return the restored document."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )

    source = await file.read()
    if not source:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")

    try:
        placeholders = placeholders_from_document(validate_keys_yaml(keys))
    except KeysValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    restored, unused = unmask_docx_in_memory(source, placeholders)

    base = Path(file.filename).stem
    download_name = f"{base} (демаскированный).docx"
    headers = {
        "Content-Disposition": _content_disposition(download_name),
        # Keys whose placeholder was absent from the document, as a JSON list.
        # Headers are latin-1, so the JSON (Cyrillic values) is percent-encoded.
        "X-Unused-Keys": quote(json.dumps(unused, ensure_ascii=False)),
    }
    return Response(content=restored, media_type=_DOCX_CONTENT_TYPE, headers=headers)

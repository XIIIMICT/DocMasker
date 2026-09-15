"""Masking API router."""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from .docx_service import mask_docx_in_memory
from .schemas import MaskedEntity, MaskRequest, MaskResponse
from .service import mask_document

router = APIRouter(tags=["masking"])

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _ascii_attachment_name(name: str) -> str:
    """Fallback filename for `filename=` (must be ASCII/latin-1 safe)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.()-]", "_", name)
    return ascii_name or "masked.docx"


def _label_breakdown(entities: list[MaskedEntity]) -> str:
    """Per-label counts as a JSON object, e.g. ``{"person": 2, "phone": 1}``."""
    return json.dumps(dict(Counter(entity.label for entity in entities)))


def _attachment_headers(download_name: str, entities: list[MaskedEntity]) -> dict[str, str]:
    """Download headers shared by every file response (RFC 5987 name + counts)."""
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{_ascii_attachment_name(download_name)}\"; filename*=UTF-8''{quote(download_name)}"
        ),
        "X-Masked-Count": str(len(entities)),
        "X-Masked-Breakdown": _label_breakdown(entities),
    }


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(request: MaskRequest) -> MaskResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Текст пуст.")
    masked, entities = mask_document(request)
    return MaskResponse(text=masked, masked_entities=entities)


@router.post("/mask-file")
async def mask_file_endpoint(
    file: Annotated[UploadFile, File(description="DOCX file to mask")],
) -> Response:
    """Upload a .docx, mask PII in place (formatting preserved), return it for download."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Файл должен иметь расширение .docx.",
        )

    source = await file.read()
    if not source:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Файл пуст.")

    masked_bytes, entities = mask_docx_in_memory(source)

    base = Path(file.filename).stem
    headers = _attachment_headers(f"{base} (замаскированный).docx", entities)
    return Response(content=masked_bytes, media_type=_DOCX_CONTENT_TYPE, headers=headers)

"""Masking API router."""

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from .docx_service import mask_docx_in_memory
from .schemas import MaskRequest, MaskResponse
from .service import mask_document

router = APIRouter(tags=["masking"])

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _ascii_attachment_name(name: str) -> str:
    """Fallback filename for `filename=` (must be ASCII/latin-1 safe)."""
    ascii_name = re.sub(r"[^A-Za-z0-9_.()-]", "_", name)
    return ascii_name or "masked.docx"


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
    download_name = f"{base} (замаскированный).docx"
    # `filename=` must be ASCII/latin-1; use RFC 5987 filename* for the real
    # (possibly Cyrillic) name so browsers save the correct file.
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{_ascii_attachment_name(download_name)}\"; filename*=UTF-8''{quote(download_name)}"
        ),
        "X-Masked-Count": str(len(entities)),
    }
    return Response(content=masked_bytes, media_type=_DOCX_CONTENT_TYPE, headers=headers)

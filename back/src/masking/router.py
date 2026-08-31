"""Masking API router."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from .schemas import MaskRequest, MaskResponse
from .service import GLiNERModelLike, get_gliner_model, mask_document

router = APIRouter(tags=["masking"])

ModelDependency = Annotated[GLiNERModelLike, Depends(get_gliner_model)]


@router.post("/mask", response_model=MaskResponse)
async def mask_text_endpoint(model: ModelDependency, request: MaskRequest) -> MaskResponse:
    """Mask sensitive information in the given text."""
    if not request.text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Текст пуст.")
    try:
        masked, entities = mask_document(model, request)
    except RuntimeError as error:  # pragma: no cover - model inference failures
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error
    return MaskResponse(text=masked, masked_entities=entities)

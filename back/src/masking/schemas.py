"""Pydantic schemas for the masking API."""

from pydantic import BaseModel, Field


class MaskRequest(BaseModel):
    """Incoming text to mask."""

    text: str = Field(min_length=1, description="Source text to mask sensitive information in.")


class MaskedEntity(BaseModel):
    """A single detected sensitive span in the *original* text."""

    label: str
    text: str
    start: int
    end: int
    score: float


class MaskResponse(BaseModel):
    """The masked text plus the list of detected entities."""

    text: str
    masked_entities: list[MaskedEntity]

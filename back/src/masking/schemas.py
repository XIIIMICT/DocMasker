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
    """The masked text, the detected entities and the demasking keys (YAML)."""

    text: str
    masked_entities: list[MaskedEntity]
    keys: str = Field(description="Demasking keys as YAML (empty when nothing was masked).")


class UnmaskRequest(BaseModel):
    """A masked text plus the keys that restore it."""

    text: str = Field(min_length=1, description="Masked text to restore.")
    keys: str = Field(min_length=1, description="Demasking keys as YAML.")


class UnmaskResponse(BaseModel):
    """The restored text."""

    text: str

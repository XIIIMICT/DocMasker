"""Pydantic schemas for the masking API (Phase 4)."""

from pydantic import BaseModel, Field


class MaskRequest(BaseModel):
    """Incoming text to mask."""

    text: str = Field(min_length=1, description="Source text to mask sensitive information in.")


class MaskedEntity(BaseModel):
    """A single detected sensitive span in the *original* text (internal)."""

    label: str
    text: str
    start: int
    end: int
    score: float


class LabelInfo(BaseModel):
    """One canonical label (id + Russian keys-section title)."""

    id: str
    title: str


class LabelsResponse(BaseModel):
    """The single canonical label catalogue, in output order."""

    schema_version: int
    labels: list[LabelInfo]


class MaskStats(BaseModel):
    """Aggregate counts per label — the client never receives raw entity values."""

    total: int = Field(description="Total masked occurrences across all labels.")
    occurrences_by_label: dict[str, int] = Field(description="Masked occurrence count per label.")
    unique_by_label: dict[str, int] = Field(description="Distinct masked values per label.")


class MaskResponse(BaseModel):
    """
    The masked text, aggregate statistics and the demasking keys (YAML).

    Raw entity spans (original values + offsets) are deliberately not part of
    the response: the UI only needs aggregates, so original PII values are not
    duplicated over the wire (the keys file already carries the originals).
    """

    text: str
    stats: MaskStats
    keys: str = Field(description="Demasking keys as YAML (empty when nothing was masked).")


class UnmaskRequest(BaseModel):
    """A masked text plus the keys that restore it."""

    text: str = Field(min_length=1, description="Masked text to restore.")
    keys: str = Field(min_length=1, description="Demasking keys as YAML.")


class UnmaskResponse(BaseModel):
    """The restored text."""

    text: str

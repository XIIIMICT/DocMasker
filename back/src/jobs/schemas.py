"""Job API schemas (ADR-0002): 202/status/result, safe metadata only."""

from pydantic import BaseModel, Field

from src.jobs.models import JobKind, JobPhase, JobStatus


class JobAccepted(BaseModel):
    """Response to a submitted job (202)."""

    job_id: str
    status: JobStatus
    status_url: str = Field(description="Poll this URL for status/progress.")
    result_url: str = Field(description="Download the result here once succeeded.")


class JobStatusResponse(BaseModel):
    """Client-visible job state — never carries input values, keys or errors text."""

    job_id: str
    status: JobStatus
    phase: JobPhase
    progress: int = Field(ge=0, le=100)
    attempt: int
    error_code: str | None = None
    cancel_requested: bool = False
    created_at: float | None = None
    finished_at: float | None = None
    result_url: str | None = None


class JobSubmitText(BaseModel):
    """Text-mode job body (kind inferred from the endpoint path)."""

    text: str
    profile_id: str | None = None
    keys: str | None = None


class JobKindInfo(BaseModel):
    """Descriptor for a supported job kind."""

    kind: JobKind
    input: str
    result: str

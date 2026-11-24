"""Job API: submit / status / result / cancel (ADR-0002)."""

import io
import zipfile
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from src.config import get_settings
from src.masking.docx_validation import DocxValidationError, upload_limits, validate_docx_package
from src.masking.mapping import KeysValidationError, validate_keys_yaml
from src.masking.profiles import ProfilesValidationError, get_profile

from .dependencies import artifact_store, job_repository
from .repository import JobNotFoundError, JobQueueFullError
from .routing import enqueue, job_urls
from .schemas import JobAccepted, JobStatusResponse

if TYPE_CHECKING:
    from .models import JobKind

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ZIP_MIME = "application/zip"


def _safe_error(detail: str) -> HTTPException:
    """Map validation failures to a stable 422 (no exception text leaked)."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _enqueue_safe(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None,
    filename: str,
    profile_id: str | None,
) -> JobAccepted:
    """Enqueue, translating the bounded-queue overflow into an HTTP 429."""
    try:
        return enqueue(
            kind,
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename=filename,
            profile_id=profile_id,
        )
    except JobQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error


def _validated_keys(keys: str | None) -> bytes | None:
    """Validate a keys YAML form field and return its UTF-8 bytes."""
    if keys is None:
        return None
    try:
        validate_keys_yaml(keys)
    except KeysValidationError as error:
        raise _safe_error(str(error)) from error
    return keys.encode("utf-8")


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def submit_job(
    file: Annotated[UploadFile | None, File(description="DOCX file to mask/restore")] = None,
    text: Annotated[str | None, Form(description="Text to mask/restore")] = None,
    keys: Annotated[str | None, Form(description="Demasking keys YAML (unmask only)")] = None,
    kind: Annotated[str, Form(description="mask_text | mask_file | unmask_text | unmask_file")] = "mask_text",
    profile_id: Annotated[str | None, Form(description="Masking profile id")] = None,
    filename: Annotated[str | None, Form(description="Original file name for the result archive")] = None,
) -> JobAccepted:
    """Submit a large operation: 202 + opaque job id; inputs are spooled."""
    allowed: tuple[JobKind, ...] = ("mask_text", "mask_file", "unmask_text", "unmask_file")
    if kind not in allowed:
        raise _safe_error("Неизвестный тип задания.")
    try:
        get_profile(profile_id)
    except ProfilesValidationError as error:
        raise _safe_error(str(error)) from error
    # Operation-specific fields (§3.11): unmask_* require keys, mask_* reject
    # keys (unless deliberately supported) — reject inappropriate fields early.
    if kind.startswith("unmask_") and (keys is None or not keys.strip()):
        raise _safe_error("Для демаскировки необходимы ключи демаскировки.")
    if kind.startswith("mask_") and keys is not None and keys.strip():
        raise _safe_error("Ключи демаскировки не применяются при маскировании.")
    keys_bytes = _validated_keys(keys)

    if kind.endswith("_text"):
        if text is None or not text.strip():
            raise _safe_error("Текст пуст.")
        if len(text) > get_settings().max_text_chars:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Текст больше допустимого.",
            )
        return _enqueue_safe(
            kind,
            input_bytes=text.encode("utf-8"),
            keys_bytes=keys_bytes,
            filename="",
            profile_id=profile_id,
        )

    if file is None or not file.filename or not file.filename.lower().endswith(".docx"):
        raise _safe_error("Файл должен иметь расширение .docx.")
    data = await file.read()
    if not data:
        raise _safe_error("Файл пуст.")
    try:
        validate_docx_package(data, upload_limits())
    except DocxValidationError as error:
        raise HTTPException(status_code=error.http_status, detail=str(error)) from error
    return _enqueue_safe(
        kind,
        input_bytes=data,
        keys_bytes=keys_bytes,
        filename=filename or file.filename,
        profile_id=profile_id,
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str) -> JobStatusResponse:
    """Status/progress/phase + a safe error code; unknown id → 404."""
    try:
        job = job_repository().get(job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено.") from error
    _status_url, result_url = job_urls(job_id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        phase=job.phase,
        progress=job.progress,
        attempt=job.attempt,
        error_code=job.error_code,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        finished_at=job.finished_at,
        result_url=result_url if job.has_result else None,
    )


@router.get("/{job_id}/result")
async def job_result(job_id: str) -> Response:
    """Download the ZIP result; 404 when expired, 409 when not ready."""
    try:
        job = job_repository().get(job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено.") from error
    if job.status != "succeeded" or job.result_key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Результат ещё не готов.")
    if not artifact_store().exists(job_id, "result"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Результат задания недоступен (истёк срок хранения).",
        )
    data = artifact_store().read(job_id, "result")
    ascii_name = f"{job_id}-result.zip"
    download_name = f"{job_id} (результат).zip"
    headers = {
        "Content-Type": _ZIP_MIME,
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download_name)}",
        "Cache-Control": "no-store",
    }
    return Response(content=data, media_type=_ZIP_MIME, headers=headers)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: str) -> Response:
    """Idempotent cancellation request (§3.7); unknown id → 404."""
    try:
        job_repository().request_cancel(job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задание не найдено.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def zip_result(entries: dict[str, bytes]) -> bytes:
    """Build a result ZIP without holding every artifact uncompressed twice."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()

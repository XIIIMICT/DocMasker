"""Job API: submit / status / result / cancel (ADR-0002, Phase 6 §8.1/§8.5/§10)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse

from src.config import get_settings
from src.errors import ApiError
from src.masking.docx_validation import DocxValidationError, upload_limits, validate_docx_package_path
from src.masking.executor import run_blocking
from src.masking.mapping import KeysValidationError, validate_keys_yaml
from src.uploads import UploadTooLargeError, cleanup, stage_upload

from .dependencies import artifact_store, job_repository
from .repository import JobNotFoundError, JobQueueFullError
from .routing import enqueue, enqueue_from_path, job_urls
from .schemas import JobAccepted, JobStatusResponse
from .storage import ArtifactError

if TYPE_CHECKING:
    from .models import Job, JobKind

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ZIP_MIME = "application/zip"


def _unprocessable(code: str, detail: str) -> ApiError:
    return ApiError(code, detail, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _enqueue_safe(
    kind: JobKind,
    *,
    input_bytes: bytes,
    keys_bytes: bytes | None,
    filename: str,
) -> JobAccepted:
    """Enqueue, translating the bounded-queue overflow into an HTTP 429."""
    try:
        return enqueue(
            kind,
            input_bytes=input_bytes,
            keys_bytes=keys_bytes,
            filename=filename,
        )
    except JobQueueFullError as error:
        raise ApiError("queue_full", str(error), status.HTTP_429_TOO_MANY_REQUESTS) from error


def _validated_keys(keys: str | None) -> bytes | None:
    """Validate a keys YAML form field and return its UTF-8 bytes."""
    if keys is None:
        return None
    try:
        validate_keys_yaml(keys)
    except KeysValidationError as error:
        raise _unprocessable("invalid_keys", str(error)) from error
    return keys.encode("utf-8")


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def submit_job(
    file: Annotated[UploadFile | None, File(description="DOCX file to mask/restore")] = None,
    text: Annotated[str | None, Form(description="Text to mask/restore")] = None,
    keys: Annotated[str | None, Form(description="Demasking keys YAML (unmask only)")] = None,
    kind: Annotated[str, Form(description="mask_text | mask_file | unmask_text | unmask_file")] = "mask_text",
    filename: Annotated[str | None, Form(description="Original file name for the result archive")] = None,
) -> JobAccepted:
    """Submit a large operation: 202 + opaque job id; uploads are staged/streamed."""
    allowed: tuple[JobKind, ...] = ("mask_text", "mask_file", "unmask_text", "unmask_file")
    if kind not in allowed:
        raise _unprocessable("unknown_kind", "Неизвестный тип задания.")
    # Operation-specific fields (§3.11): unmask_* require keys, mask_* reject
    # keys — reject inappropriate fields early.
    if kind.startswith("unmask_") and (keys is None or not keys.strip()):
        raise _unprocessable("keys_required", "Для демаскировки необходимы ключи демаскировки.")
    if kind.startswith("mask_") and keys is not None and keys.strip():
        raise _unprocessable("unexpected_keys", "Ключи демаскировки не применяются при маскировании.")
    if keys is not None and len(keys) > get_settings().max_keys_chars:
        raise ApiError(
            "keys_too_large",
            f"Ключи больше допустимых ({get_settings().max_keys_chars} символов).",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )
    keys_bytes = await run_blocking(_validated_keys, keys)

    if kind.endswith("_text"):
        if text is None or not text.strip():
            raise _unprocessable("empty_text", "Текст пуст.")
        if len(text) > get_settings().max_text_chars:
            raise ApiError("text_too_large", "Текст больше допустимого.", status.HTTP_413_CONTENT_TOO_LARGE)
        return await run_blocking(
            _enqueue_safe,
            kind,
            input_bytes=text.encode("utf-8"),
            keys_bytes=keys_bytes,
            filename="",
        )

    if file is None or not file.filename or not file.filename.lower().endswith(".docx"):
        raise _unprocessable("invalid_extension", "Файл должен иметь расширение .docx.")
    try:
        staged = await stage_upload(file)
    except UploadTooLargeError as error:
        raise ApiError("file_too_large", str(error), status.HTTP_413_CONTENT_TOO_LARGE) from error
    try:
        if staged.size == 0:
            raise _unprocessable("empty_file", "Файл пуст.")
        # Validate from the staged filesystem path; the full document is never
        # loaded into a single bytes object on this path (§8.5/§10).
        try:
            await run_blocking(validate_docx_package_path, staged.path, upload_limits())
        except DocxValidationError as error:
            raise ApiError("invalid_package", str(error), error.http_status) from error
        try:
            return await run_blocking(
                enqueue_from_path,
                kind,
                input_path=staged.path,
                input_digest=staged.sha256,
                keys_bytes=keys_bytes,
                filename=filename or file.filename,
            )
        except JobQueueFullError as error:
            raise ApiError("queue_full", str(error), status.HTTP_429_TOO_MANY_REQUESTS) from error
    finally:
        # Cancellation/exception-safe cleanup of the private temp file.
        await run_blocking(cleanup, staged)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str) -> JobStatusResponse:
    """Status/progress/phase + a safe error code; unknown id → 404."""

    def get_job() -> Job:
        return job_repository().get(job_id)

    try:
        job = await run_blocking(get_job)
    except JobNotFoundError as error:
        raise ApiError("job_not_found", "Задание не найдено.", status.HTTP_404_NOT_FOUND) from error
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
    """Stream the ZIP result from disk; 404 when expired, 409 when not ready."""

    def get_job() -> Job:
        return job_repository().get(job_id)

    try:
        job = await run_blocking(get_job)
    except JobNotFoundError as error:
        raise ApiError("job_not_found", "Задание не найдено.", status.HTTP_404_NOT_FOUND) from error
    if job.status != "succeeded" or job.result_key is None:
        raise ApiError("result_not_ready", "Результат ещё не готов.", status.HTTP_409_CONFLICT)
    store = artifact_store()
    try:
        # Legacy rows may still reference the shared "result" suffix; new
        # workers publish attempt-scoped immutable keys.
        path = store.path_for(job_id, "result") if job.result_key == "result" else store.path_for_result(job.result_key)
    except ArtifactError as error:
        raise ApiError("result_expired", "Некорректный идентификатор задания.", status.HTTP_404_NOT_FOUND) from error
    # Defense in depth for CodeQL: normalise the untrusted path and require it
    # to be contained in the private spool root before streaming it.
    root_real = os.path.realpath(str(store.root))  # noqa: ASYNC240 - fast realpath, required by CodeQL
    safe_path = os.path.realpath(str(path))  # noqa: ASYNC240
    if not safe_path.startswith(root_real + os.sep):
        raise ApiError("result_expired", "Некорректный идентификатор задания.", status.HTTP_404_NOT_FOUND)
    path = Path(safe_path)
    exists = await run_blocking(path.is_file)
    if not exists:
        raise ApiError(
            "result_expired", "Результат задания недоступен (истёк срок хранения).", status.HTTP_404_NOT_FOUND
        )
    ascii_name = f"{job_id}-result.zip"
    download_name = f"{job_id} (результат).zip"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download_name)}",
        "Cache-Control": "no-store",
    }
    # FileResponse streams from the filesystem — no read_bytes() of a large ZIP.
    return FileResponse(path, media_type=_ZIP_MIME, headers=headers)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: str) -> Response:
    """Idempotent cancellation request (§3.7); unknown id → 404."""

    def request_cancel() -> None:
        job_repository().request_cancel(job_id)

    try:
        await run_blocking(request_cancel)
    except JobNotFoundError as error:
        raise ApiError("job_not_found", "Задание не найдено.", status.HTTP_404_NOT_FOUND) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)

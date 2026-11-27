"""
Stable API error contract and request correlation (Phase 6, §8.1).

Every error response has the shape:
    {"code": "<stable machine-readable>", "detail": "<Russian user-facing>",
     "request_id": "<opaque uuid4>"}
plus an `X-Request-ID` header. No tracebacks, local paths, DB messages, file
contents, keys or PII ever leave the API.
"""

import logging
import math
import uuid
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .app_logging import bind_request_id, log_error, log_event, log_warning
from .cache_headers import NO_STORE_HEADERS, is_api_path
from .jobs.readiness import ArtifactStorageError
from .metrics import (
    http_request_finished,
    http_request_started,
    mark_http_failure,
    observe_http_request,
)
from .observability import route_template

LOGGER = logging.getLogger("docmasker.errors")

REQUEST_ID_HEADER = "X-Request-ID"
RETRY_AFTER_HEADER = "Retry-After"


class ApiErrorResponse(BaseModel):
    """The one error envelope documented in OpenAPI; never carries submitted data."""

    code: str = Field(description="Stable machine-readable error code.")
    detail: str = Field(description="Russian user-facing detail; no PII, keys or local paths.")
    request_id: str = Field(description="Opaque id, identical to the X-Request-ID header.")


_REQUEST_ID_HEADER_SCHEMA: dict[str, Any] = {
    "description": "Correlation id; matches `request_id` in the error body.",
    "schema": {"type": "string"},
}


def error_responses(*statuses: int) -> dict[int, dict[str, Any]]:
    """Reusable OpenAPI declarations for the single sanitized error envelope."""
    responses: dict[int, dict[str, Any]] = {
        code: {
            "model": ApiErrorResponse,
            "description": "Sanitized error: stable code, Russian detail, opaque request id.",
            "headers": {REQUEST_ID_HEADER: _REQUEST_ID_HEADER_SCHEMA},
        }
        for code in statuses
    }
    if status.HTTP_429_TOO_MANY_REQUESTS in responses:
        headers: dict[str, Any] = responses[status.HTTP_429_TOO_MANY_REQUESTS]["headers"]
        headers[RETRY_AFTER_HEADER] = {
            "description": "Present on queue_full rejections: whole seconds before resubmitting.",
            "schema": {"type": "integer", "minimum": 1},
        }
    return responses


#: Stable codes per HTTP status, used when a plain HTTPException is raised.
_DEFAULT_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_CONTENT_TOO_LARGE: "payload_too_large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "unprocessable",
    status.HTTP_429_TOO_MANY_REQUESTS: "busy",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
}


class ApiError(HTTPException):
    """A domain error with a stable machine-readable code."""

    def __init__(self, code: str, detail: str, status_code: int) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


#: Stable codes for the router's fixed Russian detail strings (no need to
#: touch every `raise HTTPException`). Checked in order; longer phrases first.
_DETAIL_CODES: tuple[tuple[str, str], ...] = (
    ("Ключи больше допустимых", "keys_too_large"),
    ("Текст больше допустимого", "text_too_large"),
    ("Файл больше допустимого", "file_too_large"),
    ("Файл должен иметь расширение", "invalid_extension"),
    ("не является корректным DOCX", "invalid_package"),
    ("Зашифрованные DOCX", "encrypted_package"),
    ("архив-бомба", "zip_bomb"),
    ("распаковывается в слишком большой", "uncompressed_too_large"),
    ("слишком много внутренних частей", "too_many_entries"),
    ("неподдерживаемое вложенное содержимое", "unsupported_package"),
    ("некорректную или небезопасную XML", "unsafe_xml"),
    ("Слишком большая XML-часть", "xml_part_too_large"),
    ("Сервер занят", "executor_busy"),
    ("Очередь заданий переполнена", "queue_full"),
    ("Результат ещё не готов", "result_not_ready"),
    ("истёк срок хранения", "result_expired"),
    ("Задание не найдено", "job_not_found"),
    ("Файл пуст", "empty_file"),
    ("Текст пуст", "empty_text"),
    ("Файл ключей", "invalid_keys"),
    ("Ключи демаскировки", "invalid_keys"),
    ("Неизвестный тип задания", "unknown_kind"),
    ("неизвестный раздел ключей", "unknown_section"),
)


def _code_for(detail: str, status_code: int) -> str:
    for phrase, code in _DETAIL_CODES:
        if phrase in detail:
            return code
    return _DEFAULT_CODES.get(status_code, "error")


def _request_id(request: Request) -> str:
    """Reuse a middleware-supplied id, or mint one for direct/test calls."""
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def _error_body(code: str, detail: str, request_id: str) -> dict[str, str]:
    return {"code": code, "detail": detail, "request_id": request_id}


def _error_headers(request: Request, request_id: str, *, code: str | None = None) -> dict[str, str]:
    """Correlate errors, keep no-store on API paths, hint retry on queue_full."""
    headers = {REQUEST_ID_HEADER: request_id}
    if code == "queue_full":
        retry_after = math.ceil(config.get_settings().jobs_retry_after_seconds)
        headers[RETRY_AFTER_HEADER] = str(max(1, retry_after))
    if is_api_path(request.url.path):
        headers.update(NO_STORE_HEADERS)
    return headers


def install_error_handlers(app: FastAPI) -> None:
    """Register the request-id middleware and all exception handlers."""

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        """Correlate every request and record HTTP metrics without any PII."""
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        response_status = 500
        http_request_started()
        with bind_request_id(request_id):
            try:
                response = await call_next(request)
                response_status = response.status_code
                response.headers[REQUEST_ID_HEADER] = request_id
            except Exception:
                # The outer ServerErrorMiddleware renders the Russian error
                # envelope; here only the bounded outcome is recorded.
                response_status = 500
                mark_http_failure()
                raise
            else:
                return response
            finally:
                http_request_finished()
                route = route_template(request.app, request.scope)
                duration = perf_counter() - started
                observe_http_request(
                    method=request.scope.get("method", "OTHER"),
                    route=route,
                    status=response_status,
                    duration_seconds=duration,
                )
                if response_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                    mark_http_failure()
                if request.scope.get("path") not in {"/health", "/ready", "/metrics"}:
                    fields = {
                        "request_id": request_id,
                        "method": request.scope.get("method", "OTHER"),
                        "route": route,
                        "status_code": response_status,
                        "duration_ms": round(duration * 1000, 1),
                    }
                    if response_status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                        log_error(LOGGER, "http_request_error", "Request failed.", **fields)
                    elif response_status >= status.HTTP_400_BAD_REQUEST:
                        log_warning(LOGGER, "http_request_failed", "Request rejected.", **fields)
                    else:
                        log_event(LOGGER, "http_request", "Request handled.", **fields)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        rid = _request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, str(exc.detail), rid),
            headers=_error_headers(request, rid, code=exc.code),
        )

    @app.exception_handler(ArtifactStorageError)
    async def artifact_storage_error_handler(request: Request, exc: ArtifactStorageError) -> JSONResponse:
        """Typed storage rejection: same envelope, no job row and no 500."""
        rid = _request_id(request)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_body(exc.code, exc.detail, rid),
            headers=_error_headers(request, rid, code=exc.code),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        rid = _request_id(request)
        detail = str(exc.detail) if exc.detail else "Ошибка запроса."
        code = (
            getattr(exc, "code", None) or (exc.headers or {}).get("X-Error-Code") or _code_for(detail, exc.status_code)
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, detail, rid),
            headers=_error_headers(request, rid, code=code),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
        rid = _request_id(request)
        # Never echo the submitted values (they may contain PII).
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", "Некорректные данные запроса.", rid),
            headers=_error_headers(request, rid, code="validation_error"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = _request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(_DEFAULT_CODES.get(exc.status_code, "error"), "Ошибка запроса.", rid),
            headers=_error_headers(request, rid, code=_DEFAULT_CODES.get(exc.status_code, "error")),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log only the exception class (never its message / inputs).
        LOGGER.error("unhandled error request_id=%s type=%s", _request_id(request), type(exc).__name__)
        rid = _request_id(request)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "Внутренняя ошибка сервера.", rid),
            headers=_error_headers(request, rid, code="internal_error"),
        )

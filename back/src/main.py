"""DocMasker FastAPI application entrypoint."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST

from .api import api_router
from .app_logging import configure_application_logging, log_event
from .config import get_settings
from .errors import install_error_handlers
from .jobs.dependencies import job_repository, result_cache
from .masking.config import get_config
from .masking.executor import run_blocking
from .metrics import generate_metrics, observe_dependency_check
from .observability import sample_runtime_metrics

settings = get_settings()
configure_application_logging()
LOGGER = logging.getLogger("docmasker.application")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Load the immutable masking config and start best-effort runtime sampling."""
    # Fail fast on an invalid immutable config snapshot at startup.
    get_config()
    log_event(
        LOGGER,
        "application_started",
        "DocMasker starting.",
        operation="startup",
        outcome="ok",
    )
    runtime_metrics = asyncio.create_task(
        sample_runtime_metrics(
            log_dir=Path("logs"),
            jobs_dir=Path(settings.jobs_dir),
        ),
        name="runtime-metrics",
    )
    try:
        yield
    finally:
        runtime_metrics.cancel()
        with suppress(asyncio.CancelledError):
            await runtime_metrics
    log_event(LOGGER, "application_stopped", "DocMasker stopped.", operation="shutdown", outcome="ok")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins or [],
    allow_credentials=False,
    allow_methods=["DELETE", "GET", "PATCH", "POST"],
    allow_headers=["*"],
)
app.include_router(api_router)
install_error_handlers(app)


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    """Liveness probe: always ok (no model to load), used by Docker healthcheck."""
    return {"status": "ok"}


@app.get("/ready")
async def readiness() -> JSONResponse:
    """Verify the PostgreSQL substrate; Valkey is observed but never gates readiness."""
    started = time.perf_counter()
    try:
        await run_blocking(job_repository().ping)
    except Exception as exc:  # noqa: BLE001 - only the type is logged
        observe_dependency_check(dependency="postgres", outcome="error", duration_seconds=time.perf_counter() - started)
        log_warning_readiness(type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "reason": "Хранилище заданий недоступно."},
        )
    observe_dependency_check(dependency="postgres", outcome="ok", duration_seconds=time.perf_counter() - started)

    if settings.cache_enabled:
        valkey_started = time.perf_counter()
        valkey_ok = False
        try:
            valkey_ok = await run_blocking(lambda: result_cache().ping())
        except Exception:  # noqa: BLE001 - a cache outage is never a readiness failure
            valkey_ok = False
        observe_dependency_check(
            dependency="valkey",
            outcome="ok" if valkey_ok else "error",
            duration_seconds=time.perf_counter() - valkey_started,
        )
    return JSONResponse(
        content={"status": "ready", "labels": len(get_config().labels.entries)},
    )


def log_warning_readiness(error_type: str) -> None:
    """Log a readiness failure by exception type only (no DB message or PII)."""
    LOGGER.warning("readiness: PostgreSQL unavailable (error_type=%s)", error_type)


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """
    Scrape endpoint.

    `Accept: text/plain` selects Prometheus exposition; the default keeps the
    legacy coarse JSON contract consumed by existing clients.
    """
    accept = request.headers.get("accept", "")
    if "text/plain" in accept.lower():
        return Response(content=generate_metrics(), media_type=CONTENT_TYPE_LATEST)
    try:
        active = await run_blocking(job_repository().count_active)
    except Exception as exc:  # noqa: BLE001 - only the type is logged
        LOGGER.warning("metrics: PostgreSQL unavailable (error_type=%s)", type(exc).__name__)
        active = None
    try:
        cache = await run_blocking(lambda: dict(result_cache().metrics))
    except Exception:  # noqa: BLE001 - cache is never a correctness dependency
        cache = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0}
    return JSONResponse(content={"active_jobs": active, "cache": cache})

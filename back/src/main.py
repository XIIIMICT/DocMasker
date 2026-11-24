"""DocMasker FastAPI application entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import api_router
from .config import get_settings
from .errors import install_error_handlers
from .jobs.dependencies import job_repository, result_cache
from .masking.config import get_config
from .masking.executor import run_blocking

settings = get_settings()
LOGGER = logging.getLogger("docmasker.application")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Load the immutable masking config snapshot (fail fast on invalid data)."""
    config = get_config()
    LOGGER.info(
        "DocMasker starting (masking labels=%d, config digest=%s).",
        len(config.labels.entries),
        config.digest[:12],
    )
    yield
    LOGGER.info("DocMasker stopped.")


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
    """Verify the PostgreSQL substrate and masking config are usable (Phase 6 §10)."""
    try:
        await run_blocking(job_repository().ping)
    except Exception as exc:  # noqa: BLE001 - only the type is logged
        LOGGER.warning("readiness: PostgreSQL unavailable (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "reason": "Хранилище заданий недоступно."},
        )
    return JSONResponse(
        content={"status": "ready", "labels": len(get_config().labels.entries)},
    )


@app.get("/metrics")
async def metrics() -> dict[str, object]:
    """Return coarse safe counters only; a DB outage degrades, never 500 (Phase 6 §10)."""
    try:
        active = await run_blocking(job_repository().count_active)
    except Exception as exc:  # noqa: BLE001 - only the type is logged
        LOGGER.warning("metrics: PostgreSQL unavailable (%s)", type(exc).__name__)
        active = None
    try:
        cache = await run_blocking(lambda: dict(result_cache().metrics))
    except Exception:  # noqa: BLE001 - cache is never a correctness dependency
        cache = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0}
    return {"active_jobs": active, "cache": cache}

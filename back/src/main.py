"""DocMasker FastAPI application entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .config import get_settings
from .masking.config import get_config

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


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    """Readiness probe for Docker."""
    return {"status": "ok"}

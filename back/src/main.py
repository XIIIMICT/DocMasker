"""DocMasker FastAPI application entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .config import get_settings
from .masking.service import get_gliner_model

settings = get_settings()
LOGGER = logging.getLogger("docmasker.application")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load the GLiNER model once at startup so /health reflects readiness."""
    LOGGER.info("DocMasker starting.")
    get_gliner_model()
    LOGGER.info("DocMasker ready.")
    yield


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
    """Readiness probe for Docker (returns ok once the model is loaded)."""
    return {"status": "ok"}

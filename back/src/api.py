"""Top-level API router."""

from fastapi import APIRouter

from src.jobs.api import router as jobs_router

from .masking.router import router as masking_router

api_router = APIRouter(prefix="/api")
api_router.include_router(masking_router)
api_router.include_router(jobs_router)

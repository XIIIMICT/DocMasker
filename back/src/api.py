"""Top-level API router."""

from fastapi import APIRouter

from .masking.router import router as masking_router

api_router = APIRouter(prefix="/api")
api_router.include_router(masking_router)

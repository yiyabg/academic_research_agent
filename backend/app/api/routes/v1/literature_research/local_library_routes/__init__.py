"""Thin composition root for local-paper-library subroutes."""

from fastapi import APIRouter

from .analysis import router as analysis_router
from .analysis_stream import router as analysis_stream_router
from .memory import router as memory_router
from .search import router as search_router
from .sync import router as sync_router

router = APIRouter()
router.include_router(sync_router)
router.include_router(search_router)
router.include_router(analysis_router)
router.include_router(analysis_stream_router)
router.include_router(memory_router)

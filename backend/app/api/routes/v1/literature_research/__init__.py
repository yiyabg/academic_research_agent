"""Literature research API router."""

from fastapi import APIRouter

from app.api.routes.v1.literature_research import (
    artifacts,
    catalog,
    evaluations,
    events,
    evidence,
    memory,
    local_library,
    metrics,
    organizations,
    projects,
    protocols,
    runs,
)

router = APIRouter()
router.include_router(organizations.router, prefix="/organizations")
router.include_router(artifacts.router, prefix="/runs")
router.include_router(catalog.router, prefix="/runs")
router.include_router(projects.router, prefix="/projects")
router.include_router(protocols.router, prefix="/projects")
router.include_router(runs.router, prefix="/runs")
router.include_router(events.router, prefix="/runs")
router.include_router(evidence.router, prefix="/runs")
router.include_router(evaluations.router)
router.include_router(memory.router)
router.include_router(local_library.router, prefix="/local-library")
router.include_router(metrics.router, prefix="/admin/metric-snapshots")

"""Health check endpoints.

Provides Kubernetes-compatible health check endpoints:
- /health - Simple liveness check
- /health/live - Detailed liveness probe
- /health/ready - Readiness probe with dependency checks
"""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

import asyncio
import socket
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.api.deps import DBSession, Redis
from app.core.config import settings
from app.schemas.base import HealthDetailResponse, HealthResponse
from app.services.health import build_health_response
from app.services.literature_research.document_parser import ResearchDocumentParser
from app.services.literature_research.document_safety import ClamAVDocumentScanner
from app.services.literature_research.object_store import get_research_object_store
from app.services.llm_provider import (
    probe_llm_provider,
)
from app.worker.celery_app import celery_app

router = APIRouter()

async def _active_research_queues() -> dict[str, list[dict[str, Any]]] | None:
    """Run Celery's blocking inspector outside the API event loop."""
    inspector = celery_app.control.inspect(timeout=1.5)
    return await asyncio.to_thread(inspector.active_queues)


async def _llm_provider_health() -> dict[str, Any]:
    """Route-local seam retained so API tests can isolate external providers."""
    return await probe_llm_provider()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> dict[str, Any]:
    """Simple liveness probe - check if application is running.

    This is a lightweight check that should always succeed if the
    application is running. Use this for basic connectivity tests.

    Returns:
        {"status": "healthy"}
    """
    return {
        "status": "healthy",
        "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
    }


@router.get("/health/live", response_model=HealthDetailResponse)
async def liveness_probe() -> dict[str, Any]:
    """Detailed liveness probe for Kubernetes.

    This endpoint is designed for Kubernetes liveness probes.
    It checks if the application process is alive and responding.
    Failure indicates the container should be restarted.

    Returns:
        Structured response with timestamp and service info.
    """
    return build_health_response(
        status="alive",
        details={
            "version": getattr(settings, "VERSION", "1.0.0"),
            "environment": settings.ENVIRONMENT,
        },
    )


@router.get("/health/ready", response_model=None)
async def readiness_probe(
    db: DBSession,
    redis: Redis,
) -> dict[str, Any] | JSONResponse:
    """Readiness probe for Kubernetes.

    This endpoint checks if all dependencies are ready to handle traffic.
    It verifies database connections, Redis, and other critical services.
    Failure indicates traffic should be temporarily diverted.

    Checks performed:
    - Database connectivity
    - Redis connectivity

    Returns:
        Structured response with individual check results.
        Returns 503 if any critical check fails.
    """
    checks: dict[str, dict[str, Any]] = {}

    try:
        start = datetime.now(UTC)
        await db.execute(text("SELECT 1"))
        latency_ms = (datetime.now(UTC) - start).total_seconds() * 1000
        checks["database"] = {
            "status": "healthy",
            "latency_ms": round(latency_ms, 2),
            "type": "postgresql",
        }
    except Exception as e:
        checks["database"] = {
            "status": "unhealthy",
            "error": str(e),
            "type": "postgresql",
        }
    try:
        start = datetime.now(UTC)
        is_healthy = await redis.ping()
        latency_ms = (datetime.now(UTC) - start).total_seconds() * 1000
        if is_healthy:
            checks["redis"] = {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
            }
        else:
            checks["redis"] = {
                "status": "unhealthy",
                "error": "Ping failed",
            }
    except Exception as e:
        checks["redis"] = {
            "status": "unhealthy",
            "error": str(e),
        }
    try:
        start = datetime.now(UTC)
        with socket.create_connection((settings.QDRANT_HOST, settings.QDRANT_PORT), timeout=2):
            pass
        latency_ms = (datetime.now(UTC) - start).total_seconds() * 1000
        checks["vector_store"] = {
            "status": "healthy",
            "latency_ms": round(latency_ms, 2),
            "type": "qdrant",
        }
    except Exception as e:
        checks["vector_store"] = {
            "status": "unhealthy",
            "error": str(e),
            "type": "qdrant",
        }

    try:
        start = datetime.now(UTC)
        await get_research_object_store().healthcheck()
        checks["research_object_store"] = {
            "status": "healthy",
            "latency_ms": round((datetime.now(UTC) - start).total_seconds() * 1000, 2),
            "type": "s3" if settings.S3_ENDPOINT else "local",
        }
    except Exception as e:
        checks["research_object_store"] = {
            "status": "unhealthy",
            "error": str(e),
            "type": "s3" if settings.S3_ENDPOINT else "local",
        }

    # Model metadata retrieval validates credential/network/model access without
    # consuming generation tokens. The result is cached to avoid probe storms.
    checks["llm"] = await _llm_provider_health()
    try:
        start = datetime.now(UTC)
        await ClamAVDocumentScanner().ping()
        checks["malware_scanner"] = {
            "status": "healthy",
            "latency_ms": round((datetime.now(UTC) - start).total_seconds() * 1000, 2),
            "type": "clamav",
        }
    except Exception as e:
        checks["malware_scanner"] = {
            "status": "unavailable",
            "error": str(e),
            "type": "clamav",
        }
    try:
        start = datetime.now(UTC)
        versions = await ResearchDocumentParser.runtime_healthcheck()
        checks["research_parsing"] = {
            "status": "healthy",
            "latency_ms": round((datetime.now(UTC) - start).total_seconds() * 1000, 2),
            "versions": versions,
        }
    except Exception as e:
        checks["research_parsing"] = {
            "status": "unavailable",
            "error": str(e),
        }
    try:
        queue_map = await _active_research_queues()
        queues = {
            str(queue["name"])
            for worker_queues in (queue_map or {}).values()
            for queue in worker_queues
        }
        required_queues = {
            "research-io",
            "research-cpu",
            "research-llm",
            "paper-analysis",
        }
        missing = sorted(required_queues - queues)
        checks["research_workers"] = {
            "status": "healthy" if not missing else "unhealthy",
            "workers": len(queue_map or {}),
            "queues": sorted(queues),
            "missing_queues": missing,
        }
    except Exception as e:
        checks["research_workers"] = {"status": "unhealthy", "error": str(e)}

    critical_names = {
        "database",
        "redis",
        "vector_store",
        "research_object_store",
        "research_workers",
    }
    critical = {k: v for k, v in checks.items() if k in critical_names}
    all_healthy = (
        all(check.get("status") == "healthy" for check in critical.values()) if critical else True
    )

    # The admin /system page reads each service from the top level, so flatten
    # the checks alongside the structured `checks` field for K8s probes.
    response_data = build_health_response(
        status="ready" if all_healthy else "not_ready",
        checks=checks,
    )
    response_data.update(checks)
    response_data["capabilities"] = {
        "search_only": all_healthy,
        "full_research": (
            all_healthy
            and checks["llm"]["status"] == "healthy"
            and checks["malware_scanner"]["status"] == "healthy"
            and checks["research_parsing"]["status"] == "healthy"
        ),
    }

    if not all_healthy:
        return JSONResponse(status_code=503, content=response_data)

    return response_data


# Backward compatibility - keep /ready endpoint
@router.get("/ready", response_model=None)
async def readiness_check(
    db: DBSession,
    redis: Redis,
) -> dict[str, Any] | JSONResponse:
    """Readiness check (alias for /health/ready).

    Deprecated: Use /health/ready instead.
    """
    return await readiness_probe(
        db=db,
        redis=redis,
    )

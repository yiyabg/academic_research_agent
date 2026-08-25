"""Health endpoint tests."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def healthy_research_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unrelated research dependencies healthy in focused readiness tests."""
    monkeypatch.setattr(
        "app.api.routes.v1.health.socket.create_connection",
        lambda *_args, **_kwargs: nullcontext(),
    )
    store = SimpleNamespace(healthcheck=AsyncMock())
    monkeypatch.setattr("app.api.routes.v1.health.get_research_object_store", lambda: store)
    monkeypatch.setattr("app.api.routes.v1.health.ClamAVDocumentScanner.ping", AsyncMock())
    monkeypatch.setattr(
        "app.api.routes.v1.health.ResearchDocumentParser.runtime_healthcheck",
        AsyncMock(
            return_value={
                "pymupdf": "fixture",
                "tesseract": "fixture",
                "grobid": "true",
            }
        ),
    )
    queues = {
        "worker@tests": [
            {"name": "research-io"},
            {"name": "research-cpu"},
            {"name": "research-llm"},
            {"name": "paper-analysis"},
        ]
    }
    monkeypatch.setattr(
        "app.api.routes.v1.health._active_research_queues",
        AsyncMock(return_value=queues),
    )
    monkeypatch.setattr(
        "app.api.routes.v1.health._llm_provider_health",
        AsyncMock(
            return_value={
                "status": "healthy",
                "provider": "openai",
                "model": settings.AI_MODEL,
                "probe": "fixture",
            }
        ),
    )


@pytest.mark.anyio
async def test_health_check(client: AsyncClient):
    """Test liveness probe."""
    response = await client.get(f"{settings.API_V1_STR}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.anyio
async def test_readiness_check(client: AsyncClient):
    """Test readiness probe with mocked dependencies."""
    response = await client.get(f"{settings.API_V1_STR}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ready", "degraded"]
    assert "checks" in data


@pytest.mark.anyio
async def test_readiness_check_redis_healthy(client: AsyncClient, mock_redis):
    """Test readiness when Redis is healthy."""
    mock_redis.ping = AsyncMock(return_value=True)

    response = await client.get(f"{settings.API_V1_STR}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["redis"]["status"] == "healthy"
    assert "latency_ms" in data["checks"]["redis"]


@pytest.mark.anyio
async def test_readiness_check_redis_unhealthy(client: AsyncClient, mock_redis):
    """Test readiness when Redis is unhealthy."""
    mock_redis.ping = AsyncMock(side_effect=Exception("Connection failed"))

    response = await client.get(f"{settings.API_V1_STR}/ready")
    # Should return 503 when Redis is down
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["checks"]["redis"]["status"] == "unhealthy"
    assert "error" in data["checks"]["redis"]


@pytest.mark.anyio
async def test_readiness_check_db_healthy(client: AsyncClient, mock_db_session):
    """Test readiness when database is healthy."""
    # Mock successful DB query
    mock_db_session.execute = AsyncMock()

    response = await client.get(f"{settings.API_V1_STR}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["database"]["status"] == "healthy"


@pytest.mark.anyio
async def test_readiness_check_db_unhealthy(client: AsyncClient, mock_db_session):
    """Test readiness when database is unhealthy."""
    mock_db_session.execute = AsyncMock(side_effect=Exception("DB connection failed"))

    response = await client.get(f"{settings.API_V1_STR}/ready")
    # Should return 503 when DB is down
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["checks"]["database"]["status"] == "unhealthy"


@pytest.mark.anyio
async def test_scanner_outage_disables_full_research_but_keeps_search_ready(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.v1.health.ClamAVDocumentScanner.ping",
        AsyncMock(side_effect=ConnectionError("clamd unavailable")),
    )
    response = await client.get(f"{settings.API_V1_STR}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["malware_scanner"]["status"] == "unavailable"
    assert data["capabilities"]["search_only"] is True
    assert data["capabilities"]["full_research"] is False


@pytest.mark.anyio
async def test_llm_probe_failure_disables_full_research_but_keeps_search_ready(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.routes.v1.health._llm_provider_health",
        AsyncMock(
            return_value={
                "status": "unavailable",
                "provider": "openai",
                "model": settings.AI_MODEL,
                "detail": "LLM provider probe failed",
                "error_type": "APITimeoutError",
            }
        ),
    )
    response = await client.get(f"{settings.API_V1_STR}/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["llm"]["status"] == "unavailable"
    assert data["capabilities"]["search_only"] is True
    assert data["capabilities"]["full_research"] is False

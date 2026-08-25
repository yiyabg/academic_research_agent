"""HTTP client for the isolated private local-paper BGE reranker service."""

from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import settings


class LocalPaperReranker(Protocol):
    async def score(self, *, query: str, documents: list[str]) -> list[float]: ...


class BGERerankerV2M3HTTP:
    """Client for the only local-paper Cross-Encoder execution path.

    A missing/unhealthy service raises instead of returning un-reranked
    results, so the API can never claim that BGE reranking occurred when it
    did not.
    """

    def __init__(self, service_url: str | None = None) -> None:
        self.service_url = (service_url or settings.LOCAL_PAPER_RERANKER_SERVICE_URL).rstrip("/")

    async def score(self, *, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        async with httpx.AsyncClient(
            timeout=settings.LOCAL_PAPER_MODEL_HTTP_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                f"{self.service_url}/rerank",
                json={"query": query, "documents": documents},
            )
            response.raise_for_status()
        scores = response.json().get("scores")
        if not isinstance(scores, list):
            raise RuntimeError("BGE reranker service response has no scores list")
        return [float(score) for score in scores]

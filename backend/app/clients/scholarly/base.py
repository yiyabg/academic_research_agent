"""Base contracts and HTTP reliability behavior for scholarly sources."""

import asyncio
import hashlib
import json
import random
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.literature_research.discovery import SourcePage, SourceQuery


class ScholarlySourceError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


def request_fingerprint(source: str, query: SourceQuery, cursor: str | None) -> str:
    payload = {
        "source": source,
        "query": query.model_dump(mode="json"),
        "cursor": cursor,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


class ScholarlySource(ABC):
    name: str

    @abstractmethod
    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        """Fetch one source-native page without applying quality or relevance filters."""

    async def aclose(self) -> None:
        """Close resources owned by the adapter."""
        return None


class BaseHttpScholarlySource(ScholarlySource):
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=settings.SCHOLARLY_HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": settings.SCHOLARLY_USER_AGENT},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, Any],
        allowed_statuses: set[int] | None = None,
    ) -> httpx.Response:
        attempts = settings.SCHOLARLY_HTTP_MAX_RETRIES
        for attempt in range(attempts):
            try:
                response = await self.client.get(url, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 == attempts:
                    raise ScholarlySourceError(str(exc), retryable=True) from exc
                await asyncio.sleep(min(2**attempt + random.random(), 10))
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt + 1 == attempts:
                    raise ScholarlySourceError(
                        f"{self.name} returned HTTP {response.status_code}",
                        retryable=True,
                        status_code=response.status_code,
                    )
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                await asyncio.sleep(min(delay + random.random(), 30))
                continue
            if response.status_code in {401, 403}:
                raise ScholarlySourceError(
                    f"{self.name} authorization failed",
                    retryable=False,
                    status_code=response.status_code,
                )
            if response.is_error and response.status_code not in (allowed_statuses or set()):
                raise ScholarlySourceError(
                    f"{self.name} returned HTTP {response.status_code}",
                    retryable=False,
                    status_code=response.status_code,
                )
            return response
        raise ScholarlySourceError(f"{self.name} request exhausted retries", retryable=True)

    @staticmethod
    def _retrieved_at() -> datetime:
        return datetime.now(UTC)

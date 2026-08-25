"""OpenAlex works API adapter."""

from typing import Any
from urllib.parse import quote

from app.clients.scholarly.base import BaseHttpScholarlySource, request_fingerprint
from app.core.config import settings
from app.domain.literature_research.source_types import openalex_types_for
from app.schemas.literature_research.discovery import (
    RawSourceRecord,
    ScholarlySourceName,
    SourcePage,
    SourceQuery,
)


class OpenAlexSource(BaseHttpScholarlySource):
    name = ScholarlySourceName.OPENALEX.value
    endpoint = "https://api.openalex.org/works"

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        active_cursor = cursor or "*"
        work_types = openalex_types_for(query.publication_types)
        if not work_types:
            raise ValueError("OpenAlex query has no supported publication types")
        filters = [
            f"from_publication_date:{query.date_from.isoformat()}",
            f"to_publication_date:{query.date_to.isoformat()}",
        ]
        filters.append(f"type:{'|'.join(work_types)}")
        params: dict[str, Any] = {
            "search": query.query_text,
            "filter": ",".join(filters),
            "per-page": query.result_limit,
            "cursor": active_cursor,
        }
        if settings.OPENALEX_API_KEY:
            params["api_key"] = settings.OPENALEX_API_KEY
        if settings.CROSSREF_MAILTO:
            params["mailto"] = settings.CROSSREF_MAILTO
        response = await self._get(self.endpoint, params=params)
        payload = response.json()
        retrieved_at = self._retrieved_at()
        allowed_types = set(work_types)
        records = [
            RawSourceRecord(
                source=ScholarlySourceName.OPENALEX,
                source_id=str(item["id"]),
                retrieved_at=retrieved_at,
                raw=item,
            )
            for item in payload.get("results", [])
            if item.get("id") and item.get("type") in allowed_types
        ]
        next_cursor = payload.get("meta", {}).get("next_cursor") if records else None
        return SourcePage(
            source=ScholarlySourceName.OPENALEX,
            query_id=query.query_id,
            cursor_in=active_cursor,
            cursor_out=next_cursor,
            request_fingerprint=request_fingerprint(self.name, query, active_cursor),
            http_status=response.status_code,
            retrieved_at=retrieved_at,
            records=records,
            raw_body=response.content,
            response_etag=response.headers.get("ETag"),
            response_last_modified=response.headers.get("Last-Modified"),
        )

    async def lookup_doi(self, query: SourceQuery, doi: str) -> SourcePage:
        """Fetch exactly one work by DOI; this endpoint performs no search or paging."""
        params: dict[str, Any] = {}
        if settings.OPENALEX_API_KEY:
            params["api_key"] = settings.OPENALEX_API_KEY
        if settings.CROSSREF_MAILTO:
            params["mailto"] = settings.CROSSREF_MAILTO
        encoded_doi = quote(doi, safe="/")
        response = await self._get(
            f"{self.endpoint}/doi:{encoded_doi}",
            params=params,
            allowed_statuses={404},
        )
        retrieved_at = self._retrieved_at()
        payload = {} if response.status_code == 404 else response.json()
        records = []
        allowed_types = set(openalex_types_for(query.publication_types))
        if not allowed_types:
            raise ValueError("OpenAlex DOI lookup has no supported publication types")
        if (
            response.status_code == 200
            and payload.get("id")
            and payload.get("type") in allowed_types
        ):
            records.append(
                RawSourceRecord(
                    source=ScholarlySourceName.OPENALEX,
                    source_id=str(payload["id"]),
                    retrieved_at=retrieved_at,
                    raw=payload,
                )
            )
        return SourcePage(
            source=ScholarlySourceName.OPENALEX,
            query_id=query.query_id,
            request_fingerprint=request_fingerprint(self.name, query, f"doi:{doi}"),
            http_status=response.status_code,
            retrieved_at=retrieved_at,
            records=records,
            raw_body=response.content,
            response_etag=response.headers.get("ETag"),
            response_last_modified=response.headers.get("Last-Modified"),
        )

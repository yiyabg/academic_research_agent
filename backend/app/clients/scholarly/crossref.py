"""Crossref REST metadata adapter."""

import hashlib
from typing import Any

from app.clients.scholarly.base import BaseHttpScholarlySource, request_fingerprint
from app.core.config import settings
from app.domain.literature_research.source_types import crossref_types_for
from app.schemas.literature_research.discovery import (
    RawSourceRecord,
    ScholarlySourceName,
    SourcePage,
    SourceQuery,
)


class CrossrefSource(BaseHttpScholarlySource):
    name = ScholarlySourceName.CROSSREF.value
    endpoint = "https://api.crossref.org/works"

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        active_cursor = cursor or "*"
        allowed_types = crossref_types_for(query.publication_types)
        if not allowed_types:
            raise ValueError("Crossref query has no supported publication types")
        filters = [
            f"from-online-pub-date:{query.date_from.isoformat()}",
            f"until-online-pub-date:{query.date_to.isoformat()}",
            *(f"type:{value}" for value in allowed_types),
        ]
        params: dict[str, Any] = {
            "query.bibliographic": query.query_text,
            "filter": ",".join(filters),
            "select": (
                "DOI,title,author,abstract,published-online,published-print,issued,"
                "ISSN,container-title,publisher,URL,type,link,volume,issue,page"
            ),
            "rows": query.result_limit,
            "cursor": active_cursor,
        }
        if settings.CROSSREF_MAILTO:
            params["mailto"] = settings.CROSSREF_MAILTO
        response = await self._get(self.endpoint, params=params)
        payload = response.json()
        retrieved_at = self._retrieved_at()
        message = payload.get("message", {})
        records = []
        for item in message.get("items", []):
            # Repeated Crossref type filters are OR-ed. Enforce the same
            # allowlist locally in case an upstream response violates it.
            if item.get("type") not in allowed_types:
                continue
            source_id = item.get("DOI") or item.get("URL")
            if not source_id:
                source_id = hashlib.sha256(repr(item).encode()).hexdigest()
            records.append(
                RawSourceRecord(
                    source=ScholarlySourceName.CROSSREF,
                    source_id=str(source_id),
                    retrieved_at=retrieved_at,
                    raw=item,
                )
            )
        next_cursor = message.get("next-cursor") if records else None
        return SourcePage(
            source=ScholarlySourceName.CROSSREF,
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

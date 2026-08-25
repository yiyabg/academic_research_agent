"""arXiv Atom API adapter for preprint discovery and version dates."""

import xml.etree.ElementTree as ET
from datetime import date

from app.clients.scholarly.base import BaseHttpScholarlySource, request_fingerprint
from app.schemas.literature_research.discovery import (
    RawSourceRecord,
    ScholarlySourceName,
    SourcePage,
    SourceQuery,
)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


def _text(entry: ET.Element, path: str) -> str | None:
    node = entry.find(path)
    return node.text.strip() if node is not None and node.text else None


def _published_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _arxiv_query_expression(query_text: str) -> str:
    """Translate planner phrases into arXiv fielded Boolean syntax once."""
    terms = []
    for raw_term in query_text.split(" AND "):
        term = raw_term.strip().strip('"').strip()
        if term:
            terms.append(f'all:"{term}"')
    return " AND ".join(terms)


class ArxivSource(BaseHttpScholarlySource):
    name = ScholarlySourceName.ARXIV.value
    endpoint = "https://export.arxiv.org/api/query"
    page_size = 100

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        start = int(cursor or 0)
        params = {
            "search_query": _arxiv_query_expression(query.query_text),
            "start": start,
            "max_results": self.page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        response = await self._get(self.endpoint, params=params)
        retrieved_at = self._retrieved_at()
        root = ET.fromstring(response.content)
        records: list[RawSourceRecord] = []
        entries = root.findall(f"{ATOM}entry")
        for entry in entries:
            published = _published_date(_text(entry, f"{ATOM}published"))
            if published is None or not (query.date_from <= published <= query.date_to):
                continue
            source_id = _text(entry, f"{ATOM}id")
            if not source_id:
                continue
            raw = {
                "id": source_id,
                "title": _text(entry, f"{ATOM}title"),
                "summary": _text(entry, f"{ATOM}summary"),
                "published": _text(entry, f"{ATOM}published"),
                "updated": _text(entry, f"{ATOM}updated"),
                "doi": _text(entry, f"{ARXIV}doi"),
                "journal_ref": _text(entry, f"{ARXIV}journal_ref"),
                "authors": [
                    _text(author, f"{ATOM}name")
                    for author in entry.findall(f"{ATOM}author")
                    if _text(author, f"{ATOM}name")
                ],
                "categories": [
                    item.attrib.get("term")
                    for item in entry.findall(f"{ATOM}category")
                    if item.attrib.get("term")
                ],
                "links": [dict(link.attrib) for link in entry.findall(f"{ATOM}link")],
            }
            records.append(
                RawSourceRecord(
                    source=ScholarlySourceName.ARXIV,
                    source_id=source_id,
                    retrieved_at=retrieved_at,
                    raw=raw,
                )
            )
        total_text = _text(root, "{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total = int(total_text or 0)
        next_cursor = str(start + self.page_size) if start + self.page_size < total else None
        return SourcePage(
            source=ScholarlySourceName.ARXIV,
            query_id=query.query_id,
            cursor_in=str(start),
            cursor_out=next_cursor,
            request_fingerprint=request_fingerprint(self.name, query, str(start)),
            http_status=response.status_code,
            retrieved_at=retrieved_at,
            records=records,
            raw_body=response.content,
            response_etag=response.headers.get("ETag"),
            response_last_modified=response.headers.get("Last-Modified"),
        )

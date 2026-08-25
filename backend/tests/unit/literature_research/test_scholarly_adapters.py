"""Fixture-backed scholarly source adapter contract tests."""

import json
from datetime import date

import httpx
import pytest

from app.clients.scholarly.arxiv import ArxivSource, _arxiv_query_expression
from app.clients.scholarly.crossref import CrossrefSource
from app.clients.scholarly.openalex import OpenAlexSource
from app.schemas.literature_research.discovery import ScholarlySourceName, SourceQuery
from app.schemas.literature_research.protocol import DocumentType


def source_query(source: ScholarlySourceName) -> SourceQuery:
    return SourceQuery(
        query_id=f"q-{source.value}",
        family="topic_exact",
        source=source,
        query_text="auditable research agents",
        date_from=date(2026, 5, 21),
        date_to=date(2026, 8, 21),
        publication_types=[
            DocumentType.JOURNAL_ARTICLE,
            DocumentType.CONFERENCE_PAPER,
        ],
    )


@pytest.mark.parametrize(
    ("planner_expression", "arxiv_expression"),
    [
        ('"auditable research agents"', 'all:"auditable research agents"'),
        (
            '"retrieval augmented generation" AND "scientific literature"',
            'all:"retrieval augmented generation" AND all:"scientific literature"',
        ),
        ("auditable research agents", 'all:"auditable research agents"'),
    ],
)
def test_arxiv_query_expression_does_not_double_quote_planner_phrases(
    planner_expression: str, arxiv_expression: str
) -> None:
    assert _arxiv_query_expression(planner_expression) == arxiv_expression


@pytest.mark.anyio
async def test_crossref_cursor_and_raw_response_are_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["cursor"] == "*"
        assert request.url.params["rows"] == "100"
        filters = request.url.params["filter"]
        assert "from-online-pub-date:2026-05-21" in filters
        assert "type:journal-article" in filters
        assert "type:proceedings-article" in filters
        return httpx.Response(
            200,
            json={
                "message": {
                    "next-cursor": "next-token",
                    "items": [
                        {
                            "DOI": "10.1000/ABC",
                            "title": ["Auditable Agents"],
                            "type": "journal-article",
                        }
                    ],
                }
            },
            headers={"ETag": "fixture-v1"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = CrossrefSource(client)
    page = await adapter.search(source_query(ScholarlySourceName.CROSSREF))
    await client.aclose()

    assert page.cursor_out == "next-token"
    assert page.records[0].source_id == "10.1000/ABC"
    assert json.loads(page.raw_body)["message"]["items"][0]["DOI"] == "10.1000/ABC"
    assert page.response_etag == "fixture-v1"


@pytest.mark.anyio
async def test_openalex_uses_cursor_date_and_document_type_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        filters = request.url.params["filter"]
        assert "from_publication_date:2026-05-21" in filters
        assert "to_publication_date:2026-08-21" in filters
        assert "type:article|proceedings-article" in filters
        assert request.url.params["cursor"] == "cursor-1"
        return httpx.Response(
            200,
            json={
                "meta": {"next_cursor": "cursor-2"},
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "display_name": "Paper",
                        "type": "article",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    page = await OpenAlexSource(client).search(
        source_query(ScholarlySourceName.OPENALEX), "cursor-1"
    )
    await client.aclose()

    assert page.cursor_out == "cursor-2"
    assert page.records[0].source_id == "https://openalex.org/W1"


@pytest.mark.anyio
async def test_openalex_lookup_doi_uses_singleton_endpoint_without_search_or_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/works/doi:10.1000/ABC")
        assert "search" not in request.url.params
        assert "cursor" not in request.url.params
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1000/ABC",
                "display_name": "Paper",
                "type": "article",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    query = source_query(ScholarlySourceName.OPENALEX).model_copy(
        update={"query_text": "10.1000/ABC", "result_limit": 1}
    )
    page = await OpenAlexSource(client).lookup_doi(query, "10.1000/ABC")
    await client.aclose()

    assert page.cursor_in is None
    assert page.cursor_out is None
    assert page.records[0].source_id == "https://openalex.org/W1"


@pytest.mark.anyio
async def test_crossref_rejects_an_upstream_type_outside_the_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "type:journal-article" in request.url.params["filter"]
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {"DOI": "10.1000/article", "type": "journal-article"},
                        {"DOI": "10.1000/dataset", "type": "dataset"},
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    journal_only = source_query(ScholarlySourceName.CROSSREF).model_copy(
        update={"publication_types": [DocumentType.JOURNAL_ARTICLE]}
    )
    page = await CrossrefSource(client).search(journal_only)
    await client.aclose()

    assert [item.source_id for item in page.records] == ["10.1000/article"]


@pytest.mark.anyio
async def test_openalex_doi_lookup_rejects_a_type_outside_the_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W-dataset",
                "doi": "https://doi.org/10.1000/dataset",
                "display_name": "Dataset",
                "type": "dataset",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    query = source_query(ScholarlySourceName.OPENALEX).model_copy(
        update={
            "query_text": "10.1000/dataset",
            "publication_types": [DocumentType.JOURNAL_ARTICLE],
            "result_limit": 1,
        }
    )
    page = await OpenAlexSource(client).lookup_doi(query, "10.1000/dataset")
    await client.aclose()

    assert page.records == []


@pytest.mark.anyio
async def test_openalex_lookup_doi_treats_not_found_as_empty_auditable_page() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, content=b""))
    )
    query = source_query(ScholarlySourceName.OPENALEX).model_copy(
        update={"query_text": "10.1000/missing", "result_limit": 1}
    )
    page = await OpenAlexSource(client).lookup_doi(query, "10.1000/missing")
    await client.aclose()

    assert page.http_status == 404
    assert page.records == []


@pytest.mark.anyio
async def test_arxiv_filters_each_record_against_absolute_date_window() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>2</opensearch:totalResults>
      <entry><id>https://arxiv.org/abs/2607.00001v2</id><title>Inside</title>
        <summary>Included</summary><published>2026-07-01T00:00:00Z</published>
        <author><name>Alice Smith</name></author>
        <link href="https://arxiv.org/pdf/2607.00001" type="application/pdf" />
      </entry>
      <entry><id>https://arxiv.org/abs/2604.00001v1</id><title>Outside</title>
        <summary>Excluded</summary><published>2026-04-01T00:00:00Z</published>
        <author><name>Bob Jones</name></author>
      </entry>
    </feed>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "0"
        assert request.url.params["search_query"] == 'all:"auditable research agents"'
        return httpx.Response(200, content=xml)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    page = await ArxivSource(client).search(source_query(ScholarlySourceName.ARXIV))
    await client.aclose()

    assert [record.raw["title"] for record in page.records] == ["Inside"]
    assert page.cursor_out is None

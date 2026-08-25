"""Zero-network deployed verifier for query facets and source type gates."""

import asyncio
import json
from datetime import UTC, date, datetime

import httpx

from app.clients.scholarly.crossref import CrossrefSource
from app.clients.scholarly.openalex import OpenAlexSource
from app.schemas.literature_research.discovery import RawSourceRecord, ScholarlySourceName
from app.schemas.literature_research.protocol import (
    DocumentType,
    ExclusionFacet,
    ProtocolCompileRequest,
    SynonymGroup,
    TopicFacet,
)
from app.services.literature_research.metadata_normalizer import MetadataNormalizerService
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.query_planner import QueryPlannerService


async def verify() -> dict[str, object]:
    protocol = ProtocolCompilerService().compile(
        ProtocolCompileRequest(
            topic="auditable research agents",
            as_of_date=date(2026, 8, 22),
            allowed_types=[
                DocumentType.JOURNAL_ARTICLE,
                DocumentType.CONFERENCE_PAPER,
            ],
            must_have_facets=[
                TopicFacet(
                    facet_id="auditability",
                    name="audit trail",
                    description="The system exposes an inspectable audit trail.",
                )
            ],
            should_have_facets=[
                TopicFacet(
                    facet_id="evidence",
                    name="evidence provenance",
                    description="Claims link to source evidence.",
                )
            ],
            exclude_facets=[
                ExclusionFacet(
                    facet_id="shopping",
                    description="Consumer shopping assistants are excluded.",
                )
            ],
            synonym_groups=[
                SynonymGroup(
                    concept="research agents",
                    terms=["research assistants", "literature review agents"],
                )
            ],
        )
    ).protocol
    plan = QueryPlannerService().plan(protocol)
    assert len(plan.queries) == 1
    assert plan.candidate_limit == 35
    query = plan.queries[0]
    for phrase in ('"audit trail"', '"evidence provenance"', '"research assistants"'):
        assert phrase in query.query_text
    assert "shopping" not in query.query_text
    assert query.filters["exclude_facet_ids"] == ["shopping"]

    crossref_filters = ""

    def crossref_handler(request: httpx.Request) -> httpx.Response:
        nonlocal crossref_filters
        crossref_filters = request.url.params["filter"]
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

    crossref_client = httpx.AsyncClient(transport=httpx.MockTransport(crossref_handler))
    try:
        page = await CrossrefSource(crossref_client).search(query)
    finally:
        await crossref_client.aclose()
    assert "type:journal-article" in crossref_filters
    assert "type:proceedings-article" in crossref_filters
    assert [item.source_id for item in page.records] == ["10.1000/article"]

    def openalex_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W-dataset",
                "doi": "https://doi.org/10.1000/dataset",
                "type": "dataset",
            },
        )

    openalex_client = httpx.AsyncClient(transport=httpx.MockTransport(openalex_handler))
    try:
        enriched = await OpenAlexSource(openalex_client).lookup_doi(
            query.model_copy(
                update={
                    "source": ScholarlySourceName.OPENALEX,
                    "query_text": "10.1000/dataset",
                    "result_limit": 1,
                }
            ),
            "10.1000/dataset",
        )
    finally:
        await openalex_client.aclose()
    assert enriched.records == []

    normalized = MetadataNormalizerService().normalize(
        RawSourceRecord(
            source=ScholarlySourceName.CROSSREF,
            source_id="10.1000/dataset",
            retrieved_at=datetime.now(UTC),
            raw={"DOI": "10.1000/dataset", "title": ["Dataset"], "type": "dataset"},
        )
    )
    assert normalized.document_type == DocumentType.UNKNOWN
    return {
        "status": "discovery_contract_ok",
        "crossref_keyword_searches": len(plan.queries),
        "candidate_limit": plan.candidate_limit,
        "facet_coverage": query.facet_coverage,
        "source_type_filters": [
            item for item in crossref_filters.split(",") if item.startswith("type:")
        ],
        "unexpected_crossref_records_accepted": len(page.records) - 1,
        "unexpected_openalex_records_accepted": len(enriched.records),
        "unknown_normalized_type": normalized.document_type.value,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, sort_keys=True))

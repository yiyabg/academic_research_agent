"""Query-plan constraint preservation and immutable source snapshot tests."""

import gzip
import hashlib
from datetime import date
from uuid import uuid4

import pytest

from app.schemas.literature_research.protocol import (
    DocumentType,
    ExclusionFacet,
    ProtocolCompileRequest,
    SynonymGroup,
    TopicFacet,
)
from app.services.literature_research.object_store import (
    LocalResearchObjectStore,
    store_source_page,
    validate_object_key,
)
from app.services.literature_research.protocol_compiler import ProtocolCompilerService
from app.services.literature_research.query_planner import QueryPlannerService


def test_query_plan_is_one_bounded_crossref_doi_seed_search() -> None:
    protocol = (
        ProtocolCompilerService()
        .compile(
            ProtocolCompileRequest(
                topic="auditable research agents",
                as_of_date=date(2026, 8, 21),
                allowed_types=[DocumentType.JOURNAL_ARTICLE],
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
        )
        .protocol
    )
    plan = QueryPlannerService().plan(protocol)

    assert plan.strategy == "doi_seeded"
    assert plan.max_pages_per_query == 1
    assert plan.candidate_limit == 35
    assert len(plan.queries) == 1
    assert plan.queries[0].source.value == "crossref"
    assert plan.queries[0].family == "topic_doi_seed"
    query = plan.queries[0]
    assert query.query_text.startswith('"auditable research agents"')
    assert '"audit trail"' in query.query_text
    assert '"evidence provenance"' in query.query_text
    assert '"research assistants"' in query.query_text
    assert "shopping" not in query.query_text
    assert query.facet_coverage == ["auditability", "evidence"]
    assert query.filters["exclude_facet_ids"] == ["shopping"]
    assert plan.queries[0].result_limit == 35
    assert all(query.date_from == date(2026, 5, 21) for query in plan.queries)
    assert all(query.date_to == date(2026, 8, 21) for query in plan.queries)
    assert all(query.publication_types == [DocumentType.JOURNAL_ARTICLE] for query in plan.queries)


@pytest.mark.anyio
async def test_snapshot_key_is_content_addressed_and_gzip_is_reproducible(tmp_path) -> None:
    store = LocalResearchObjectStore(tmp_path)
    organization_id, project_id, run_id = uuid4(), uuid4(), uuid4()
    raw = b'{"message":{"items":[{"DOI":"10.1000/a"}]}}'
    key, digest = await store_source_page(
        store,
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
        source="crossref",
        query_id="q-001",
        page_number=1,
        raw_body=raw,
    )
    second_key, second_digest = await store_source_page(
        store,
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
        source="crossref",
        query_id="q-001",
        page_number=1,
        raw_body=raw,
    )

    assert key == second_key
    assert digest == second_digest == hashlib.sha256(raw).hexdigest()
    assert digest[:16] in key
    assert f"tenants/{organization_id}/projects/{project_id}/runs/{run_id}" in key
    assert gzip.decompress(await store.get(key)) == raw


@pytest.mark.parametrize("key", ["/absolute", "../escape", "a/../../escape"])
def test_object_store_rejects_path_escape(key: str) -> None:
    with pytest.raises(ValueError):
        validate_object_key(key)

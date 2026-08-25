"""Discovery pagination, saturation, persistence, and failure isolation tests."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.clients.scholarly.base import ScholarlySource
from app.schemas.literature_research.discovery import (
    QueryPlan,
    RawSourceRecord,
    ScholarlySourceName,
    SourcePage,
    SourceQuery,
)
from app.schemas.literature_research.protocol import DocumentType
from app.services.literature_research.discovery import DiscoveryService
from app.services.literature_research.object_store import LocalResearchObjectStore

NOW = datetime(2026, 8, 21, tzinfo=UTC)


class RepeatingSource(ScholarlySource):
    name = "crossref"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        self.calls += 1
        raw = {
            "DOI": "10.1000/repeated",
            "title": ["Repeated Result"],
            "type": "journal-article",
            "issued": {"date-parts": [[2026, 7, 1]]},
            "author": [{"family": "Smith"}],
        }
        return SourcePage(
            source=ScholarlySourceName.CROSSREF,
            query_id=query.query_id,
            cursor_in=cursor or "*",
            cursor_out=f"cursor-{self.calls}",
            request_fingerprint="sha256:" + f"{self.calls:064x}",
            http_status=200,
            retrieved_at=NOW,
            records=[
                RawSourceRecord(
                    source=ScholarlySourceName.CROSSREF,
                    source_id="10.1000/repeated",
                    retrieved_at=NOW,
                    raw=raw,
                )
            ],
            raw_body=str(raw).encode(),
        )


class DoiSeedSource(ScholarlySource):
    name = "crossref"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        self.calls += 1
        records = []
        for source_id, doi in [
            ("seed-a", "10.1000/A"),
            ("seed-a-duplicate", "https://doi.org/10.1000/a"),
            ("seed-b", "10.1000/B"),
            ("seed-c", "10.1000/C"),
            ("seed-without-doi", None),
        ]:
            raw = {
                "title": [f"Paper {source_id}"],
                "type": "journal-article",
                "issued": {"date-parts": [[2026, 7, 1]]},
                "author": [{"family": "Smith"}],
            }
            if doi:
                raw["DOI"] = doi
            records.append(
                RawSourceRecord(
                    source=ScholarlySourceName.CROSSREF,
                    source_id=source_id,
                    retrieved_at=NOW,
                    raw=raw,
                )
            )
        return SourcePage(
            source=ScholarlySourceName.CROSSREF,
            query_id=query.query_id,
            cursor_in="*",
            cursor_out="must-not-be-followed",
            request_fingerprint="sha256:" + "a" * 64,
            http_status=200,
            retrieved_at=NOW,
            records=records,
            raw_body=b"crossref-seeds",
        )


class ExactDoiOpenAlexSource(ScholarlySource):
    name = "openalex"

    def __init__(self) -> None:
        self.search_calls = 0
        self.lookup_calls: list[str] = []

    async def search(self, query: SourceQuery, cursor: str | None = None) -> SourcePage:
        del query, cursor
        self.search_calls += 1
        raise AssertionError("OpenAlex keyword search must not run in DOI-seeded discovery")

    async def lookup_doi(self, query: SourceQuery, doi: str) -> SourcePage:
        self.lookup_calls.append(doi)
        raw = {
            "id": f"https://openalex.org/W{len(self.lookup_calls)}",
            "doi": f"https://doi.org/{doi}",
            "display_name": f"OpenAlex {doi}",
            "type": "article",
            "publication_date": "2026-07-01",
            "authorships": [],
        }
        return SourcePage(
            source=ScholarlySourceName.OPENALEX,
            query_id=query.query_id,
            request_fingerprint="sha256:" + f"{len(self.lookup_calls):064x}",
            http_status=200,
            retrieved_at=NOW,
            records=[
                RawSourceRecord(
                    source=ScholarlySourceName.OPENALEX,
                    source_id=raw["id"],
                    retrieved_at=NOW,
                    raw=raw,
                )
            ],
            raw_body=str(raw).encode(),
        )


def query() -> SourceQuery:
    return SourceQuery(
        query_id="q-001-crossref",
        family="topic_exact",
        source=ScholarlySourceName.CROSSREF,
        query_text="repeated result",
        date_from=date(2026, 5, 21),
        date_to=date(2026, 8, 21),
        publication_types=[DocumentType.JOURNAL_ARTICLE],
    )


@pytest.mark.anyio
async def test_saturation_stops_pagination_and_preserves_all_raw_pages(tmp_path) -> None:
    adapter = RepeatingSource()
    service = DiscoveryService(
        adapters={ScholarlySourceName.CROSSREF: adapter},
        object_store=LocalResearchObjectStore(tmp_path),
    )
    query_row = MagicMock(id=uuid4())
    with (
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_query",
            new=AsyncMock(return_value=query_row),
        ),
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_page_with_records",
            new=AsyncMock(),
        ) as create_page,
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_failure",
            new=AsyncMock(),
        ),
        patch(
            "app.services.literature_research.discovery.discovery_repository.persist_cluster",
            new=AsyncMock(),
        ) as persist_cluster,
    ):
        outcome = await service.execute(
            AsyncMock(),
            organization_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
            plan=QueryPlan(
                queries=[query()],
                max_pages_per_query=10,
                saturation_rounds=2,
            ),
        )

    assert adapter.calls == 3
    assert create_page.await_count == 3
    assert outcome.page_count == 3
    assert outcome.raw_record_count == 3
    assert outcome.unique_record_count == 1
    assert outcome.work_count == 1
    assert outcome.version_count == 1
    persist_cluster.assert_awaited_once()
    assert len(list(tmp_path.rglob("*.raw.gz"))) == 3


@pytest.mark.anyio
async def test_missing_source_adapter_is_recorded_without_failing_other_queries(
    tmp_path,
) -> None:
    service = DiscoveryService(
        adapters={ScholarlySourceName.CROSSREF: RepeatingSource()},
        object_store=LocalResearchObjectStore(tmp_path),
    )
    missing = query().model_copy(
        update={
            "query_id": "q-002-pubmed",
            "source": ScholarlySourceName.PUBMED,
        }
    )
    with (
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_query",
            new=AsyncMock(return_value=MagicMock(id=uuid4())),
        ),
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_page_with_records",
            new=AsyncMock(),
        ),
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_failure",
            new=AsyncMock(),
        ) as create_failure,
        patch(
            "app.services.literature_research.discovery.discovery_repository.persist_cluster",
            new=AsyncMock(),
        ),
    ):
        outcome = await service.execute(
            AsyncMock(),
            organization_id=None,
            project_id=uuid4(),
            run_id=uuid4(),
            plan=QueryPlan(queries=[missing], max_pages_per_query=1),
        )

    assert outcome.work_count == 0
    assert outcome.failures[0].code == "SOURCE_NOT_CONFIGURED"
    create_failure.assert_awaited_once()


@pytest.mark.anyio
async def test_doi_seeded_discovery_searches_once_deduplicates_and_caps_exact_lookups(
    tmp_path,
) -> None:
    crossref = DoiSeedSource()
    openalex = ExactDoiOpenAlexSource()
    service = DiscoveryService(
        adapters={
            ScholarlySourceName.CROSSREF: crossref,
            ScholarlySourceName.OPENALEX: openalex,
        },
        object_store=LocalResearchObjectStore(tmp_path),
    )
    with (
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_query",
            new=AsyncMock(return_value=MagicMock(id=uuid4())),
        ) as create_query,
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_page_with_records",
            new=AsyncMock(),
        ) as create_page,
        patch(
            "app.services.literature_research.discovery.discovery_repository.create_failure",
            new=AsyncMock(),
        ),
        patch(
            "app.services.literature_research.discovery.discovery_repository.persist_cluster",
            new=AsyncMock(),
        ) as persist_cluster,
    ):
        outcome = await service.execute(
            AsyncMock(),
            organization_id=uuid4(),
            project_id=uuid4(),
            run_id=uuid4(),
            plan=QueryPlan(
                strategy="doi_seeded",
                candidate_limit=2,
                max_pages_per_query=20,
                queries=[query().model_copy(update={"result_limit": 2})],
            ),
        )

    assert crossref.calls == 1
    assert openalex.search_calls == 0
    assert openalex.lookup_calls == ["10.1000/a", "10.1000/b"]
    assert create_query.await_count == 3
    assert create_page.await_count == 3
    assert outcome.keyword_search_count == 1
    assert outcome.candidate_doi_count == 2
    assert outcome.exact_doi_lookup_count == 2
    assert outcome.exact_doi_match_count == 2
    assert outcome.query_count == 3
    assert outcome.work_count == 2
    assert persist_cluster.await_count == 2

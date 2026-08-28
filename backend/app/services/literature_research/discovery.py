"""Bounded-concurrency scholarly discovery with immutable source snapshots."""

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.scholarly import ArxivSource, CrossrefSource, OpenAlexSource
from app.clients.scholarly.base import ScholarlySource, ScholarlySourceError
from app.db.models.literature_research.discovery import ResearchSearchQuery
from app.domain.literature_research.normalization import normalize_doi
from app.domain.literature_research.versioning import version_observation_groups
from app.repositories.literature_research import discovery as discovery_repository
from app.schemas.literature_research.discovery import (
    DiscoveryOutcome,
    QueryPlan,
    RawSourceRecord,
    ScholarlySourceName,
    SourceFailure,
    SourcePage,
    SourceQuery,
)
from app.services.literature_research.entity_resolution import EntityResolutionService
from app.services.literature_research.metadata_normalizer import MetadataNormalizerService
from app.services.literature_research.object_store import (
    ResearchObjectStore,
    get_research_object_store,
    store_source_page,
)


@dataclass(slots=True)
class _StoredPage:
    page_number: int
    page: SourcePage
    object_key: str
    raw_sha256: str


@dataclass(slots=True)
class _QueryResult:
    query: SourceQuery
    pages: list[_StoredPage]
    failures: list[SourceFailure]
    exhausted: bool


def _record_doi(record: RawSourceRecord) -> str | None:
    ids = record.raw.get("ids")
    ids_doi = ids.get("doi") if isinstance(ids, dict) else None
    return normalize_doi(str(record.raw.get("DOI") or record.raw.get("doi") or ids_doi or ""))


class DiscoveryService:
    """Execute a compiled query plan without changing protocol constraints."""

    def __init__(
        self,
        *,
        adapters: dict[ScholarlySourceName, ScholarlySource] | None = None,
        object_store: ResearchObjectStore | None = None,
        max_concurrent_queries: int = 6,
    ) -> None:
        self.adapters = adapters or {
            ScholarlySourceName.CROSSREF: CrossrefSource(),
            ScholarlySourceName.OPENALEX: OpenAlexSource(),
            ScholarlySourceName.ARXIV: ArxivSource(),
        }
        self.object_store = object_store or get_research_object_store()
        self.semaphore = asyncio.Semaphore(max_concurrent_queries)
        self.normalizer = MetadataNormalizerService()
        self.resolver = EntityResolutionService()

    async def execute(
        self,
        db: AsyncSession,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        plan: QueryPlan,
    ) -> DiscoveryOutcome:
        if plan.strategy == "doi_seeded":
            return await self._execute_doi_seeded(
                db,
                organization_id=organization_id,
                project_id=project_id,
                run_id=run_id,
                plan=plan,
            )

        query_rows = {
            query.query_id: await discovery_repository.create_query(db, run_id=run_id, query=query)
            for query in plan.queries
        }
        query_results = await asyncio.gather(
            *(
                self._fetch_query(organization_id, project_id, run_id, query, plan)
                for query in plan.queries
            )
        )

        return await self._persist_and_resolve(
            db,
            run_id=run_id,
            query_rows=query_rows,
            query_results=query_results,
            keyword_search_count=len(plan.queries),
        )

    async def _execute_doi_seeded(
        self,
        db: AsyncSession,
        *,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        plan: QueryPlan,
    ) -> DiscoveryOutcome:
        """Run one Crossref search, then enrich at most N unique DOIs exactly once."""
        seed_queries = [
            query for query in plan.queries if query.source == ScholarlySourceName.CROSSREF
        ]
        if len(seed_queries) != 1:
            raise ValueError("doi_seeded discovery requires exactly one Crossref seed query")
        seed_query = seed_queries[0]
        query_rows = {
            seed_query.query_id: await discovery_repository.create_query(
                db, run_id=run_id, query=seed_query
            )
        }
        seed_result = await self._fetch_query(
            organization_id,
            project_id,
            run_id,
            seed_query,
            plan.model_copy(update={"max_pages_per_query": 1}),
        )

        dois: list[str] = []
        seen_dois: set[str] = set()
        for stored in seed_result.pages:
            for record in stored.page.records:
                doi = _record_doi(record)
                if doi is None or doi in seen_dois:
                    continue
                seen_dois.add(doi)
                dois.append(doi)
                if len(dois) >= plan.candidate_limit:
                    break
            if len(dois) >= plan.candidate_limit:
                break

        lookup_queries = [
            seed_query.model_copy(
                update={
                    "query_id": f"q-doi-{index:03d}-openalex",
                    "family": "doi_exact_enrichment",
                    "source": ScholarlySourceName.OPENALEX,
                    "query_text": doi,
                    "facet_coverage": [],
                    "origin": "crossref_doi_seed",
                    "result_limit": 1,
                }
            )
            for index, doi in enumerate(dois, start=1)
        ]
        for query in lookup_queries:
            query_rows[query.query_id] = await discovery_repository.create_query(
                db, run_id=run_id, query=query
            )
        lookup_results = await asyncio.gather(
            *(
                self._fetch_openalex_doi(
                    organization_id,
                    project_id,
                    run_id,
                    query,
                    doi,
                )
                for query, doi in zip(lookup_queries, dois, strict=True)
            )
        )
        return await self._persist_and_resolve(
            db,
            run_id=run_id,
            query_rows=query_rows,
            query_results=[seed_result, *lookup_results],
            keyword_search_count=1,
            candidate_doi_count=len(dois),
            exact_doi_lookup_count=len(lookup_queries),
            exact_doi_match_count=sum(
                bool(result.pages and result.pages[0].page.records) for result in lookup_results
            ),
            candidate_dois=set(dois),
        )

    async def _persist_and_resolve(
        self,
        db: AsyncSession,
        *,
        run_id: UUID,
        query_rows: dict[str, ResearchSearchQuery],
        query_results: list[_QueryResult],
        keyword_search_count: int,
        candidate_doi_count: int = 0,
        exact_doi_lookup_count: int = 0,
        exact_doi_match_count: int = 0,
        candidate_dois: set[str] | None = None,
    ) -> DiscoveryOutcome:
        all_records: list[RawSourceRecord] = []
        failures: list[SourceFailure] = []
        page_count = 0
        for result in query_results:
            query_row = query_rows[result.query.query_id]
            failures.extend(result.failures)
            for failure in result.failures:
                await discovery_repository.create_failure(db, run_id=run_id, failure=failure)
            for stored in result.pages:
                await discovery_repository.create_page_with_records(
                    db,
                    run_id=run_id,
                    query_row=query_row,
                    page_number=stored.page_number,
                    page=stored.page,
                    raw_object_key=stored.object_key,
                    raw_sha256=stored.raw_sha256,
                )
                all_records.extend(stored.page.records)
                page_count += 1

        unique_records: dict[tuple[ScholarlySourceName, str], RawSourceRecord] = {}
        for record in all_records:
            unique_records[(record.source, record.source_id)] = record
        resolution_records = list(unique_records.values())
        if candidate_dois is not None:
            resolution_records = [
                record for record in resolution_records if _record_doi(record) in candidate_dois
            ]
        normalized = [self.normalizer.normalize(record) for record in resolution_records]
        clusters = self.resolver.resolve(normalized)
        for cluster in clusters:
            await discovery_repository.persist_cluster(db, run_id=run_id, cluster=cluster)

        counts = Counter(record.source for record in unique_records.values())
        return DiscoveryOutcome(
            query_count=len(query_results),
            successful_query_count=sum(not item.failures for item in query_results),
            exhausted_query_count=sum(item.exhausted for item in query_results),
            page_count=page_count,
            raw_record_count=len(all_records),
            unique_record_count=len(unique_records),
            work_count=len(clusters),
            version_count=sum(
                len(version_observation_groups(cluster.versions)) for cluster in clusters
            ),
            source_counts=dict(counts),
            keyword_search_count=keyword_search_count,
            candidate_doi_count=candidate_doi_count,
            exact_doi_lookup_count=exact_doi_lookup_count,
            exact_doi_match_count=exact_doi_match_count,
            failures=failures,
        )

    async def _fetch_openalex_doi(
        self,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        query: SourceQuery,
        doi: str,
    ) -> _QueryResult:
        adapter = self.adapters.get(ScholarlySourceName.OPENALEX)
        lookup = getattr(adapter, "lookup_doi", None)
        if lookup is None:
            return _QueryResult(
                query=query,
                pages=[],
                failures=[
                    SourceFailure(
                        source=ScholarlySourceName.OPENALEX,
                        query_id=query.query_id,
                        code="DOI_LOOKUP_NOT_CONFIGURED",
                        message="OpenAlex DOI lookup adapter is not configured",
                        retryable=False,
                        occurred_at=datetime.now(UTC),
                    )
                ],
                exhausted=False,
            )
        try:
            async with self.semaphore:
                page = await lookup(query, doi)
        except ScholarlySourceError as exc:
            return _QueryResult(
                query=query,
                pages=[],
                failures=[
                    SourceFailure(
                        source=ScholarlySourceName.OPENALEX,
                        query_id=query.query_id,
                        code=(
                            f"HTTP_{exc.status_code}"
                            if exc.status_code is not None
                            else "DOI_LOOKUP_FAILED"
                        ),
                        message=str(exc),
                        retryable=exc.retryable,
                        occurred_at=datetime.now(UTC),
                    )
                ],
                exhausted=False,
            )
        except Exception as exc:
            return _QueryResult(
                query=query,
                pages=[],
                failures=[
                    SourceFailure(
                        source=ScholarlySourceName.OPENALEX,
                        query_id=query.query_id,
                        code="DOI_LOOKUP_PARSE_FAILED",
                        message=str(exc),
                        retryable=False,
                        occurred_at=datetime.now(UTC),
                    )
                ],
                exhausted=False,
            )
        object_key, raw_sha256 = await store_source_page(
            self.object_store,
            organization_id=organization_id,
            project_id=project_id,
            run_id=run_id,
            source=query.source.value,
            query_id=query.query_id,
            page_number=1,
            raw_body=page.raw_body,
        )
        return _QueryResult(
            query=query,
            pages=[
                _StoredPage(
                    page_number=1,
                    page=page,
                    object_key=object_key,
                    raw_sha256=raw_sha256,
                )
            ],
            failures=[],
            exhausted=True,
        )

    async def _fetch_query(
        self,
        organization_id: UUID | None,
        project_id: UUID,
        run_id: UUID,
        query: SourceQuery,
        plan: QueryPlan,
    ) -> _QueryResult:
        adapter = self.adapters.get(query.source)
        if adapter is None:
            return _QueryResult(
                query=query,
                pages=[],
                failures=[
                    SourceFailure(
                        source=query.source,
                        query_id=query.query_id,
                        code="SOURCE_NOT_CONFIGURED",
                        message=f"No adapter configured for {query.source.value}",
                        retryable=False,
                        occurred_at=datetime.now(UTC),
                    )
                ],
                exhausted=False,
            )

        pages: list[_StoredPage] = []
        failures: list[SourceFailure] = []
        cursor: str | None = None
        empty_pages = 0
        saturation_pages = 0
        seen: set[tuple[ScholarlySourceName, str]] = set()
        exhausted = False
        for page_number in range(1, plan.max_pages_per_query + 1):
            try:
                async with self.semaphore:
                    page = await adapter.search(query, cursor)
            except ScholarlySourceError as exc:
                failures.append(
                    SourceFailure(
                        source=query.source,
                        query_id=query.query_id,
                        code=(
                            f"HTTP_{exc.status_code}"
                            if exc.status_code is not None
                            else "SOURCE_REQUEST_FAILED"
                        ),
                        message=str(exc),
                        retryable=exc.retryable,
                        occurred_at=datetime.now(UTC),
                    )
                )
                break
            except Exception as exc:  # source parser bugs are isolated and auditable
                failures.append(
                    SourceFailure(
                        source=query.source,
                        query_id=query.query_id,
                        code="SOURCE_PARSE_FAILED",
                        message=str(exc),
                        retryable=False,
                        occurred_at=datetime.now(UTC),
                    )
                )
                break

            object_key, raw_sha256 = await store_source_page(
                self.object_store,
                organization_id=organization_id,
                project_id=project_id,
                run_id=run_id,
                source=query.source.value,
                query_id=query.query_id,
                page_number=page_number,
                raw_body=page.raw_body,
            )
            pages.append(
                _StoredPage(
                    page_number=page_number,
                    page=page,
                    object_key=object_key,
                    raw_sha256=raw_sha256,
                )
            )
            identities = {(item.source, item.source_id) for item in page.records}
            new_count = len(identities - seen)
            seen.update(identities)
            empty_pages = empty_pages + 1 if not page.records else 0
            saturation_pages = saturation_pages + 1 if new_count == 0 else 0
            if (
                page.cursor_out is None
                or empty_pages >= plan.stop_after_empty_pages
                or saturation_pages >= plan.saturation_rounds
            ):
                exhausted = True
                break
            cursor = page.cursor_out
        if pages and len(pages) >= plan.max_pages_per_query and not failures:
            exhausted = True
        return _QueryResult(query=query, pages=pages, failures=failures, exhausted=exhausted)

    async def aclose(self) -> None:
        await asyncio.gather(*(adapter.aclose() for adapter in self.adapters.values()))

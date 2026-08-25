"""Persistence primitives for auditable scholarly discovery."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.discovery import (
    ResearchSearchQuery,
    ResearchSourceFailure,
    ResearchSourcePage,
    ResearchSourceRecord,
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.domain.literature_research.versioning import (
    choose_version_representative,
    version_observation_groups,
)
from app.schemas.literature_research.discovery import SourceFailure, SourcePage, SourceQuery
from app.schemas.literature_research.work import NormalizedPaper, ResolvedWorkCluster


def raw_payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_query(
    db: AsyncSession, *, run_id: UUID, query: SourceQuery
) -> ResearchSearchQuery:
    result = await db.execute(
        select(ResearchSearchQuery).where(
            ResearchSearchQuery.run_id == run_id,
            ResearchSearchQuery.query_id == query.query_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    row = ResearchSearchQuery(
        run_id=run_id,
        query_id=query.query_id,
        family=query.family,
        source=query.source.value,
        query_text=query.query_text,
        date_from=query.date_from,
        date_to=query.date_to,
        query_json=query.model_dump(mode="json"),
    )
    db.add(row)
    await db.flush()
    return row


async def create_page_with_records(
    db: AsyncSession,
    *,
    run_id: UUID,
    query_row: ResearchSearchQuery,
    page_number: int,
    page: SourcePage,
    raw_object_key: str,
    raw_sha256: str,
) -> tuple[ResearchSourcePage, list[ResearchSourceRecord]]:
    result = await db.execute(
        select(ResearchSourcePage).where(
            ResearchSourcePage.run_id == run_id,
            ResearchSourcePage.request_fingerprint == page.request_fingerprint,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        records_result = await db.execute(
            select(ResearchSourceRecord).where(ResearchSourceRecord.source_page_id == existing.id)
        )
        return existing, list(records_result.scalars().all())

    page_row = ResearchSourcePage(
        run_id=run_id,
        search_query_id=query_row.id,
        source=page.source.value,
        page_number=page_number,
        cursor_in=page.cursor_in,
        cursor_out=page.cursor_out,
        request_fingerprint=page.request_fingerprint,
        http_status=page.http_status,
        retrieved_at=page.retrieved_at,
        record_count=len(page.records),
        raw_object_key=raw_object_key,
        raw_sha256=raw_sha256,
        response_etag=page.response_etag,
        response_last_modified=page.response_last_modified,
    )
    db.add(page_row)
    await db.flush()
    rows = [
        ResearchSourceRecord(
            run_id=run_id,
            source_page_id=page_row.id,
            source=record.source.value,
            source_id=record.source_id,
            retrieved_at=record.retrieved_at,
            raw_payload=record.raw,
            raw_sha256=raw_payload_hash(record.raw),
        )
        for record in page.records
    ]
    db.add_all(rows)
    await db.flush()
    return page_row, rows


async def create_failure(
    db: AsyncSession, *, run_id: UUID, failure: SourceFailure
) -> ResearchSourceFailure:
    row = ResearchSourceFailure(
        run_id=run_id,
        query_id=failure.query_id,
        source=failure.source.value,
        code=failure.code,
        message=failure.message,
        retryable=failure.retryable,
        occurred_at=failure.occurred_at,
    )
    db.add(row)
    await db.flush()
    return row


async def _get_or_create_venue(db: AsyncSession, paper: NormalizedPaper) -> ResearchVenue | None:
    venue = paper.venue
    if venue is None:
        return None
    result = await db.execute(
        select(ResearchVenue).where(
            ResearchVenue.normalized_name == venue.normalized_name,
            ResearchVenue.venue_type == venue.venue_type.value,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = ResearchVenue(
        name=venue.name,
        normalized_name=venue.normalized_name,
        venue_type=venue.venue_type.value,
        issn_l=venue.issn_l,
        issns_json=venue.issns,
        publisher=venue.publisher,
    )
    db.add(row)
    await db.flush()
    return row


async def persist_cluster(
    db: AsyncSession, *, run_id: UUID, cluster: ResolvedWorkCluster
) -> ResearchWork:
    existing_result = await db.execute(
        select(ResearchWork).where(
            ResearchWork.run_id == run_id,
            ResearchWork.cluster_key == cluster.cluster_key,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return existing
    preferred = cluster.preferred
    work = ResearchWork(
        run_id=run_id,
        cluster_key=cluster.cluster_key,
        canonical_title=preferred.title,
        normalized_title=preferred.title_normalized,
        abstract=preferred.abstract,
        document_type=preferred.document_type.value,
        language=preferred.language,
        authors_json=[item.model_dump(mode="json") for item in preferred.authors],
        field_provenance_json={
            key: value.model_dump(mode="json") for key, value in preferred.field_provenance.items()
        },
        duplicate_decisions_json=[item.model_dump(mode="json") for item in cluster.decisions],
    )
    db.add(work)
    await db.flush()

    preferred_version: ResearchWorkVersion | None = None
    for observations in version_observation_groups(cluster.versions):
        paper = choose_version_representative(observations)
        venue = await _get_or_create_venue(db, paper)
        dates = paper.dates
        ids = paper.identifiers
        version = ResearchWorkVersion(
            work_id=work.id,
            venue_id=venue.id if venue else None,
            source=paper.source.value,
            source_id=paper.source_id,
            version_type=paper.version_type.value,
            doi=ids.doi,
            arxiv_id=ids.arxiv_id,
            openalex_id=ids.openalex_id,
            semantic_scholar_id=ids.semantic_scholar_id,
            pmid=ids.pmid,
            published_online=dates.published_online,
            issued=dates.issued,
            published_print=dates.published_print,
            preprint_first_posted=dates.preprint_first_posted,
            accepted=dates.accepted,
            effective_publication_date=dates.effective_publication_date,
            effective_date_field=dates.effective_date_field,
            effective_date_source=(
                dates.effective_date_source.value if dates.effective_date_source else None
            ),
            canonical_url=str(paper.canonical_url) if paper.canonical_url else None,
            open_access_pdf_url=(
                str(paper.open_access_pdf_url) if paper.open_access_pdf_url else None
            ),
            volume=paper.volume,
            issue=paper.issue,
            pages=paper.pages,
            raw_sha256=paper.raw_sha256,
        )
        db.add(version)
        await db.flush()
        for observation in observations:
            await db.execute(
                update(ResearchSourceRecord)
                .where(
                    ResearchSourceRecord.run_id == run_id,
                    ResearchSourceRecord.source == observation.source.value,
                    ResearchSourceRecord.source_id == observation.source_id,
                )
                .values(version_id=version.id)
            )
        if any(
            item.source_id == preferred.source_id and item.source == preferred.source
            for item in observations
        ):
            preferred_version = version
    if preferred_version is None:
        raise RuntimeError("Resolved cluster did not contain its preferred paper")
    work.preferred_version_id = preferred_version.id
    work.updated_at = datetime.now(UTC)
    db.add(work)
    await db.flush()
    return work


async def list_raw_records(db: AsyncSession, run_id: UUID) -> list[ResearchSourceRecord]:
    result = await db.execute(
        select(ResearchSourceRecord)
        .where(ResearchSourceRecord.run_id == run_id)
        .order_by(ResearchSourceRecord.retrieved_at.asc())
    )
    return list(result.scalars().all())


async def list_source_snapshot_hashes(db: AsyncSession, *, run_id: UUID) -> list[str]:
    """Return stable hashes of the immutable raw response objects used by a run."""
    result = await db.execute(
        select(ResearchSourcePage.raw_sha256)
        .where(ResearchSourcePage.run_id == run_id)
        .distinct()
        .order_by(ResearchSourcePage.raw_sha256.asc())
    )
    return list(result.scalars().all())

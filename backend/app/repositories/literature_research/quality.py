"""Persistence for authorized venue metrics and immutable constraint ledgers."""

from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.quality import (
    ResearchConstraintEvaluation,
    ResearchMetricSnapshot,
    ResearchVenueMetricFact,
    ResearchWorkEligibility,
)
from app.domain.literature_research.normalization import normalize_venue_name
from app.schemas.literature_research.quality import (
    MetricFactInput,
    MetricObservation,
    MetricSnapshotCreate,
    WorkConstraintLedger,
)


async def create_snapshot(
    db: AsyncSession,
    *,
    imported_by: UUID,
    snapshot: MetricSnapshotCreate,
    facts: list[MetricFactInput],
) -> ResearchMetricSnapshot:
    row = ResearchMetricSnapshot(
        source_name=snapshot.source_name,
        source_version=snapshot.source_version,
        metric_names_json=snapshot.metric_names,
        effective_from=snapshot.effective_from,
        effective_to=snapshot.effective_to,
        license_reference=snapshot.license_reference,
        authorized_scope=snapshot.authorized_scope,
        license_attested=snapshot.license_attested,
        imported_by=imported_by,
        imported_at=snapshot.imported_at,
        payload_sha256=snapshot.payload_sha256,
        object_key=snapshot.object_key,
    )
    db.add(row)
    await db.flush()
    db.add_all(
        [
            ResearchVenueMetricFact(
                snapshot_id=row.id,
                venue_name=fact.venue_name,
                venue_normalized_name=normalize_venue_name(fact.venue_name),
                venue_type=fact.venue_type,
                issn_l=fact.issn_l,
                metric_name=fact.metric_name,
                metric_value=fact.metric_value,
                metric_year=fact.metric_year,
                source_row=fact.source_row,
            )
            for fact in facts
        ]
    )
    await db.flush()
    return row


async def list_snapshots(
    db: AsyncSession, *, skip: int = 0, limit: int = 100
) -> list[ResearchMetricSnapshot]:
    result = await db.execute(
        select(ResearchMetricSnapshot)
        .order_by(ResearchMetricSnapshot.imported_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_used_metric_snapshot_ids(db: AsyncSession, *, run_id: UUID) -> list[UUID]:
    """Return deterministic IDs for every licensed snapshot referenced by the run ledger."""
    result = await db.execute(
        select(ResearchConstraintEvaluation.metric_snapshot_id)
        .where(
            ResearchConstraintEvaluation.run_id == run_id,
            ResearchConstraintEvaluation.metric_snapshot_id.is_not(None),
        )
        .distinct()
        .order_by(ResearchConstraintEvaluation.metric_snapshot_id.asc())
    )
    return list(result.scalars().all())


async def list_preferred_work_rows(
    db: AsyncSession, *, run_id: UUID
) -> list[tuple[ResearchWork, ResearchWorkVersion, ResearchVenue | None]]:
    result = await db.execute(
        select(ResearchWork, ResearchWorkVersion, ResearchVenue)
        .join(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .where(ResearchWork.run_id == run_id)
        .order_by(ResearchWork.id.asc())
    )
    return [(work, version, venue) for work, version, venue in result.all()]


async def find_metric(
    db: AsyncSession,
    *,
    venue_name: str,
    venue_type: str,
    metric_name: str,
    as_of_date: date,
    issn_l: str | None = None,
) -> MetricObservation | None:
    venue_normalized = normalize_venue_name(venue_name)
    identity = ResearchVenueMetricFact.venue_normalized_name == venue_normalized
    if issn_l:
        identity = or_(ResearchVenueMetricFact.issn_l == issn_l, identity)
    result = await db.execute(
        select(ResearchVenueMetricFact, ResearchMetricSnapshot)
        .join(
            ResearchMetricSnapshot,
            ResearchMetricSnapshot.id == ResearchVenueMetricFact.snapshot_id,
        )
        .where(
            identity,
            ResearchVenueMetricFact.venue_type == venue_type,
            ResearchVenueMetricFact.metric_name == metric_name,
            ResearchVenueMetricFact.metric_year.is_not(None),
            ResearchVenueMetricFact.metric_year <= as_of_date.year,
            ResearchMetricSnapshot.status == "ACTIVE",
            ResearchMetricSnapshot.effective_from <= as_of_date,
            or_(
                ResearchMetricSnapshot.effective_to.is_(None),
                ResearchMetricSnapshot.effective_to >= as_of_date,
            ),
        )
        .order_by(
            ResearchMetricSnapshot.effective_from.desc(),
            ResearchMetricSnapshot.imported_at.desc(),
            ResearchVenueMetricFact.metric_year.desc(),
            ResearchVenueMetricFact.source_row.asc(),
        )
        .limit(1)
    )
    match = result.one_or_none()
    if match is None:
        return None
    fact, snapshot = match
    if fact.metric_year is None:  # guarded in SQL; keeps type narrowing explicit
        return None
    return MetricObservation(
        fact_id=fact.id,
        metric_name=fact.metric_name,
        value=fact.metric_value,
        metric_year=fact.metric_year,
        venue_id=fact.venue_id,
        venue_name=fact.venue_name,
        snapshot_id=snapshot.id,
        source_name=snapshot.source_name,
        source_version=snapshot.source_version,
        effective_from=snapshot.effective_from,
        effective_to=snapshot.effective_to,
        authorized=snapshot.license_attested and snapshot.status == "ACTIVE",
        evidence_reference=(
            f"metric-snapshot:{snapshot.id}:row:{fact.source_row}:sha256:{snapshot.payload_sha256}"
        ),
    )


async def persist_ledger(
    db: AsyncSession, *, run_id: UUID, ledger: WorkConstraintLedger
) -> ResearchWorkEligibility:
    result = await db.execute(
        select(ResearchWorkEligibility).where(
            ResearchWorkEligibility.run_id == run_id,
            ResearchWorkEligibility.work_id == ledger.work_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    evaluated_at = max(item.evaluated_at for item in ledger.evaluations)
    eligibility = ResearchWorkEligibility(
        run_id=run_id,
        work_id=ledger.work_id,
        version_id=ledger.version_id,
        protocol_hash=ledger.protocol_hash,
        eligible=ledger.eligible,
        hard_pass_count=ledger.hard_pass_count,
        hard_fail_count=ledger.hard_fail_count,
        hard_unknown_count=ledger.hard_unknown_count,
        evaluated_at=evaluated_at,
    )
    db.add(eligibility)
    db.add_all(
        [
            ResearchConstraintEvaluation(
                run_id=run_id,
                work_id=ledger.work_id,
                version_id=ledger.version_id,
                protocol_hash=ledger.protocol_hash,
                constraint_id=item.constraint_id,
                field=item.field,
                operator=item.operator.value,
                expected_value=item.expected_value,
                observed_value=item.observed_value,
                severity=item.severity.value,
                decision=item.decision.value,
                reason_code=item.reason_code.value,
                evidence_reference=item.evidence_reference,
                metric_snapshot_id=item.metric_snapshot_id,
                metric_fact_id=item.metric_fact_id,
                metric_year=item.metric_year,
                evaluated_at=item.evaluated_at,
            )
            for item in ledger.evaluations
        ]
    )
    await db.flush()
    return eligibility

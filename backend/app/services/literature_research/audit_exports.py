"""Build deterministic audit rows from the authoritative research ledgers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.analysis import ResearchPaperAnalysis
from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.evidence import (
    ResearchFullTextAcquisition,
    ResearchParsingResult,
)
from app.db.models.literature_research.quality import (
    ResearchConstraintEvaluation,
    ResearchMetricSnapshot,
)
from app.repositories.literature_research import catalog as catalog_repository
from app.schemas.literature_research.release import (
    ExclusionAuditRow,
    MetricSnapshotAuditRow,
)


def _relevance_score(row: object) -> float | None:
    return next(
        (
            value
            for value in (
                getattr(row, "cross_encoder_score", None),
                getattr(row, "semantic_score", None),
                getattr(row, "lexical_score", None),
            )
            if value is not None
        ),
        None,
    )


async def collect_exclusion_audit_rows(
    db: AsyncSession, *, run_id: UUID, included_work_ids: set[UUID]
) -> list[ExclusionAuditRow]:
    """Explain every canonical candidate omitted from the final report.

    Reasons stop at the first failed pipeline boundary. This prevents a hard
    constraint failure from also being misleadingly labelled as a missing PDF.
    """
    candidate_rows, _ = await catalog_repository.list_candidate_rows(
        db, run_id=run_id, skip=0, limit=100_000
    )
    excluded = [row for row in candidate_rows if row[0].id not in included_work_ids]
    work_ids = [row[0].id for row in excluded]
    constraints = await catalog_repository.list_constraints_for_works(
        db, run_id=run_id, work_ids=work_ids
    )

    acquisition_result = await db.execute(
        select(ResearchWorkVersion.work_id, ResearchFullTextAcquisition)
        .join(
            ResearchFullTextAcquisition,
            ResearchFullTextAcquisition.version_id == ResearchWorkVersion.id,
        )
        .where(
            ResearchFullTextAcquisition.run_id == run_id,
            ResearchWorkVersion.work_id.in_(work_ids),
        )
        .order_by(
            ResearchWorkVersion.work_id.asc(),
            ResearchFullTextAcquisition.created_at.asc(),
        )
    )
    acquisitions: dict[UUID, list[ResearchFullTextAcquisition]] = {
        work_id: [] for work_id in work_ids
    }
    for work_id, acquisition in acquisition_result.all():
        acquisitions[work_id].append(acquisition)

    parsing_result = await db.execute(
        select(ResearchWorkVersion.work_id, ResearchParsingResult)
        .join(
            ResearchParsingResult,
            ResearchParsingResult.version_id == ResearchWorkVersion.id,
        )
        .where(
            ResearchParsingResult.run_id == run_id,
            ResearchWorkVersion.work_id.in_(work_ids),
        )
        .order_by(ResearchWorkVersion.work_id.asc())
    )
    parsing: dict[UUID, list[ResearchParsingResult]] = {
        work_id: [] for work_id in work_ids
    }
    for work_id, result in parsing_result.all():
        parsing[work_id].append(result)
    analyzed_ids = set(
        (
            await db.execute(
                select(ResearchPaperAnalysis.work_id)
                .where(
                    ResearchPaperAnalysis.run_id == run_id,
                    ResearchPaperAnalysis.work_id.in_(work_ids),
                )
                .distinct()
            )
        ).scalars()
    )

    audit_rows: list[ExclusionAuditRow] = []
    for work, version, venue, eligibility, relevance in excluded:
        reason_codes: list[str] = []
        if eligibility is None:
            reason_codes.append("HARD_CONSTRAINT_NOT_EVALUATED")
        elif not eligibility.eligible:
            reason_codes.extend(
                item.reason_code
                for item in constraints[work.id]
                if item.decision in {"FAIL", "UNKNOWN"}
            )
            if not reason_codes:
                reason_codes.append("HARD_CONSTRAINT_NOT_PASSED")
        elif relevance is None:
            reason_codes.append("RELEVANCE_NOT_EVALUATED")
        elif relevance.decision != "PASS":
            reason_codes.extend(relevance.reasons_json)
            if not reason_codes:
                reason_codes.append(f"RELEVANCE_{relevance.decision}")
        else:
            work_acquisitions = acquisitions[work.id]
            usable_fulltext = any(
                item.allowed
                and item.malware_scan_status == "CLEAN"
                and item.object_key is not None
                for item in work_acquisitions
            )
            if not usable_fulltext:
                reason_codes.extend(item.reason_code for item in work_acquisitions)
                if not reason_codes:
                    reason_codes.append("FULLTEXT_NOT_ACQUIRED")
            else:
                work_parsing = parsing[work.id]
                if not any(item.status == "PASSED" for item in work_parsing):
                    reason_codes.extend(
                        code for item in work_parsing for code in item.error_codes_json
                    )
                    if not reason_codes:
                        reason_codes.append("PARSING_NOT_PASSED")
                elif work.id not in analyzed_ids:
                    reason_codes.append("ANALYSIS_NOT_PERSISTED")
                else:
                    reason_codes.append("NOT_IN_FINAL_REPORT")

        audit_rows.append(
            ExclusionAuditRow(
                work_id=work.id,
                version_id=version.id if version is not None else None,
                title=work.canonical_title,
                document_type=work.document_type,
                doi=version.doi if version is not None else None,
                venue=venue.name if venue is not None else None,
                hard_eligible=eligibility.eligible if eligibility is not None else None,
                hard_fail_count=(eligibility.hard_fail_count if eligibility is not None else 0),
                hard_unknown_count=(
                    eligibility.hard_unknown_count if eligibility is not None else 0
                ),
                relevance_decision=(relevance.decision if relevance is not None else None),
                relevance_score=(
                    _relevance_score(relevance) if relevance is not None else None
                ),
                reason_codes=sorted(set(reason_codes)),
            )
        )
    return sorted(audit_rows, key=lambda item: str(item.work_id))


async def collect_metric_snapshot_audit_rows(
    db: AsyncSession, *, run_id: UUID
) -> list[MetricSnapshotAuditRow]:
    """Export only licensed snapshot facts actually cited by this run's ledger."""
    result = await db.execute(
        select(
            ResearchConstraintEvaluation,
            ResearchMetricSnapshot,
            ResearchWork,
            ResearchVenue,
        )
        .join(
            ResearchMetricSnapshot,
            ResearchMetricSnapshot.id
            == ResearchConstraintEvaluation.metric_snapshot_id,
        )
        .join(ResearchWork, ResearchWork.id == ResearchConstraintEvaluation.work_id)
        .outerjoin(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchConstraintEvaluation.version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .where(
            ResearchConstraintEvaluation.run_id == run_id,
            ResearchConstraintEvaluation.metric_snapshot_id.is_not(None),
        )
        .order_by(
            ResearchMetricSnapshot.id.asc(),
            ResearchWork.id.asc(),
            ResearchConstraintEvaluation.constraint_id.asc(),
        )
    )
    return [
        MetricSnapshotAuditRow(
            snapshot_id=snapshot.id,
            metric_fact_id=evaluation.metric_fact_id,
            work_id=work.id,
            title=work.canonical_title,
            venue=venue.name if venue is not None else None,
            constraint_id=evaluation.constraint_id,
            field=evaluation.field,
            observed_value=evaluation.observed_value,
            metric_year=evaluation.metric_year,
            decision=evaluation.decision,
            reason_code=evaluation.reason_code,
            source_name=snapshot.source_name,
            source_version=snapshot.source_version,
            effective_from=snapshot.effective_from,
            effective_to=snapshot.effective_to,
            license_reference=snapshot.license_reference,
            authorized_scope=snapshot.authorized_scope,
            license_attested=snapshot.license_attested,
            snapshot_status=snapshot.status,
            payload_sha256=snapshot.payload_sha256,
            evidence_reference=evaluation.evidence_reference,
        )
        for evaluation, snapshot, work, venue in result.all()
    ]

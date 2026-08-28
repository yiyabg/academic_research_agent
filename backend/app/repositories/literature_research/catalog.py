"""Read-optimized candidate and paper queries."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.analysis import ResearchPaperAnalysis
from app.db.models.literature_research.discovery import (
    ResearchVenue,
    ResearchWork,
    ResearchWorkVersion,
)
from app.db.models.literature_research.evidence import ResearchRelevanceScore
from app.db.models.literature_research.quality import (
    ResearchConstraintEvaluation,
    ResearchWorkEligibility,
)

CandidateRow = tuple[
    ResearchWork,
    ResearchWorkVersion | None,
    ResearchVenue | None,
    ResearchWorkEligibility | None,
    ResearchRelevanceScore | None,
]


async def list_candidate_rows(
    db: AsyncSession, *, run_id: UUID, skip: int, limit: int
) -> tuple[list[CandidateRow], int]:
    total = int(
        await db.scalar(select(func.count(ResearchWork.id)).where(ResearchWork.run_id == run_id))
        or 0
    )
    result = await db.execute(
        select(
            ResearchWork,
            ResearchWorkVersion,
            ResearchVenue,
            ResearchWorkEligibility,
            ResearchRelevanceScore,
        )
        .outerjoin(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .outerjoin(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.run_id == run_id)
            & (ResearchWorkEligibility.work_id == ResearchWork.id),
        )
        .outerjoin(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.work_id == ResearchWork.id),
        )
        .where(ResearchWork.run_id == run_id)
        .order_by(ResearchWork.created_at.asc(), ResearchWork.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return [tuple(row) for row in result.all()], total  # type: ignore[misc]


async def get_candidate_row(
    db: AsyncSession, *, run_id: UUID, work_id: UUID
) -> CandidateRow | None:
    result = await db.execute(
        select(
            ResearchWork,
            ResearchWorkVersion,
            ResearchVenue,
            ResearchWorkEligibility,
            ResearchRelevanceScore,
        )
        .outerjoin(
            ResearchWorkVersion,
            ResearchWorkVersion.id == ResearchWork.preferred_version_id,
        )
        .outerjoin(ResearchVenue, ResearchVenue.id == ResearchWorkVersion.venue_id)
        .outerjoin(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.run_id == run_id)
            & (ResearchWorkEligibility.work_id == ResearchWork.id),
        )
        .outerjoin(
            ResearchRelevanceScore,
            (ResearchRelevanceScore.run_id == run_id)
            & (ResearchRelevanceScore.work_id == ResearchWork.id),
        )
        .where(ResearchWork.run_id == run_id, ResearchWork.id == work_id)
    )
    row = result.one_or_none()
    return tuple(row) if row else None  # type: ignore[return-value]


async def list_constraints_for_works(
    db: AsyncSession, *, run_id: UUID, work_ids: list[UUID]
) -> dict[UUID, list[ResearchConstraintEvaluation]]:
    grouped: dict[UUID, list[ResearchConstraintEvaluation]] = {work_id: [] for work_id in work_ids}
    if not work_ids:
        return grouped
    result = await db.execute(
        select(ResearchConstraintEvaluation)
        .where(
            ResearchConstraintEvaluation.run_id == run_id,
            ResearchConstraintEvaluation.work_id.in_(work_ids),
        )
        .order_by(
            ResearchConstraintEvaluation.work_id.asc(),
            ResearchConstraintEvaluation.constraint_id.asc(),
        )
    )
    for row in result.scalars().all():
        grouped[row.work_id].append(row)
    return grouped


async def get_work(db: AsyncSession, *, run_id: UUID, work_id: UUID) -> ResearchWork | None:
    return await db.scalar(
        select(ResearchWork).where(ResearchWork.run_id == run_id, ResearchWork.id == work_id)
    )


async def list_versions(db: AsyncSession, *, work_id: UUID) -> list[ResearchWorkVersion]:
    result = await db.execute(
        select(ResearchWorkVersion)
        .where(ResearchWorkVersion.work_id == work_id)
        .order_by(ResearchWorkVersion.created_at.asc())
    )
    return list(result.scalars().all())


async def get_latest_analysis(
    db: AsyncSession, *, run_id: UUID, work_id: UUID
) -> ResearchPaperAnalysis | None:
    return await db.scalar(
        select(ResearchPaperAnalysis)
        .where(
            ResearchPaperAnalysis.run_id == run_id,
            ResearchPaperAnalysis.work_id == work_id,
        )
        .order_by(ResearchPaperAnalysis.attempt.desc())
        .limit(1)
    )

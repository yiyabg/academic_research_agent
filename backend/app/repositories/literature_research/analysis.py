"""Persistence for audited analyses, artifacts, and release decisions."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.analysis import (
    ResearchArtifact,
    ResearchPaperAnalysis,
    ResearchReleaseCheck,
    ResearchSynthesis,
)
from app.db.models.literature_research.discovery import ResearchWork
from app.db.models.literature_research.quality import ResearchWorkEligibility
from app.db.models.literature_research.run import ResearchTaskExecution
from app.schemas.literature_research.analysis import AuditedPaperAnalysis, SynthesisOutput
from app.schemas.literature_research.release import (
    ReleaseDecision,
    ReleaseSnapshot,
    RenderedArtifact,
)


async def persist_analysis(
    db: AsyncSession,
    *,
    run_id: UUID,
    analysis: AuditedPaperAnalysis,
    model_versions: dict[str, str],
    attempt: int = 1,
    trigger: str = "INITIAL",
    requested_by: UUID | None = None,
    supersedes_analysis_id: UUID | None = None,
) -> ResearchPaperAnalysis:
    existing = await db.scalar(
        select(ResearchPaperAnalysis).where(
            ResearchPaperAnalysis.run_id == run_id,
            ResearchPaperAnalysis.work_id == analysis.work_id,
            ResearchPaperAnalysis.attempt == attempt,
        )
    )
    if existing is not None:
        if existing.analysis_json != analysis.model_dump(mode="json"):
            raise ValueError("Analysis retry produced different content; use reanalysis")
        return existing
    row = ResearchPaperAnalysis(
        run_id=run_id,
        work_id=analysis.work_id,
        attempt=attempt,
        trigger=trigger,
        requested_by=requested_by,
        supersedes_analysis_id=supersedes_analysis_id,
        analysis_json=analysis.model_dump(mode="json"),
        evidence_coverage=analysis.audit.evidence_coverage,
        contradicted_count=analysis.audit.contradicted_count,
        unsupported_count=analysis.audit.unsupported_count,
        requires_human_review=analysis.audit.requires_human_review,
        model_versions_json=model_versions,
    )
    db.add(row)
    await db.flush()
    return row


async def persist_synthesis(
    db: AsyncSession,
    *,
    run_id: UUID,
    synthesis: SynthesisOutput,
    model_version: str,
    generation: int = 1,
) -> ResearchSynthesis:
    existing = await db.scalar(
        select(ResearchSynthesis).where(
            ResearchSynthesis.run_id == run_id,
            ResearchSynthesis.generation == generation,
        )
    )
    if existing is not None:
        if existing.synthesis_json != synthesis.model_dump(mode="json"):
            raise ValueError("Synthesis retry produced different content")
        return existing
    row = ResearchSynthesis(
        run_id=run_id,
        generation=generation,
        synthesis_json=synthesis.model_dump(mode="json"),
        model_version=model_version,
    )
    db.add(row)
    await db.flush()
    return row


async def list_analyses(db: AsyncSession, *, run_id: UUID) -> list[ResearchPaperAnalysis]:
    result = await db.execute(
        select(ResearchPaperAnalysis)
        .where(ResearchPaperAnalysis.run_id == run_id)
        .order_by(ResearchPaperAnalysis.work_id.asc(), ResearchPaperAnalysis.attempt.desc())
    )
    latest: dict[UUID, ResearchPaperAnalysis] = {}
    for row in result.scalars().all():
        latest.setdefault(row.work_id, row)
    return list(latest.values())


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


async def get_synthesis(
    db: AsyncSession, *, run_id: UUID, generation: int | None = None
) -> ResearchSynthesis | None:
    query = select(ResearchSynthesis).where(ResearchSynthesis.run_id == run_id)
    if generation is not None:
        query = query.where(ResearchSynthesis.generation == generation)
    return await db.scalar(query.order_by(ResearchSynthesis.generation.desc()).limit(1))


async def next_output_generation(db: AsyncSession, *, run_id: UUID) -> int:
    current = await db.scalar(
        select(func.max(ResearchSynthesis.generation)).where(ResearchSynthesis.run_id == run_id)
    )
    return int(current or 0) + 1


async def persist_artifact(
    db: AsyncSession,
    *,
    run_id: UUID,
    artifact: RenderedArtifact,
    object_key: str,
    generation: int = 1,
) -> ResearchArtifact:
    existing = await db.scalar(
        select(ResearchArtifact).where(
            ResearchArtifact.run_id == run_id,
            ResearchArtifact.generation == generation,
            ResearchArtifact.format == artifact.format.value,
        )
    )
    if existing is not None:
        if existing.sha256 != artifact.sha256 or existing.object_key != object_key:
            raise ValueError(f"Artifact {artifact.format.value} retry produced different content")
        return existing
    row = ResearchArtifact(
        run_id=run_id,
        generation=generation,
        format=artifact.format.value,
        filename=artifact.filename,
        content_type=artifact.content_type,
        object_key=object_key,
        sha256=artifact.sha256,
        size_bytes=len(artifact.data),
    )
    db.add(row)
    await db.flush()
    return row


async def list_artifacts(
    db: AsyncSession,
    *,
    run_id: UUID,
    generation: int | None = None,
    released_only: bool = True,
) -> list[ResearchArtifact]:
    if generation is None:
        generation_query = select(func.max(ResearchArtifact.generation)).where(
            ResearchArtifact.run_id == run_id
        )
        if released_only:
            generation_query = generation_query.where(
                exists().where(
                    ResearchReleaseCheck.run_id == run_id,
                    ResearchReleaseCheck.generation == ResearchArtifact.generation,
                    ResearchReleaseCheck.allowed.is_(True),
                )
            )
        generation = await db.scalar(generation_query)
    if generation is None:
        return []
    conditions = [
        ResearchArtifact.run_id == run_id,
        ResearchArtifact.generation == generation,
    ]
    if released_only:
        conditions.append(
            exists().where(
                ResearchReleaseCheck.run_id == run_id,
                ResearchReleaseCheck.generation == generation,
                ResearchReleaseCheck.allowed.is_(True),
            )
        )
    result = await db.execute(
        select(ResearchArtifact).where(*conditions).order_by(ResearchArtifact.format.asc())
    )
    return list(result.scalars().all())


async def get_artifact(
    db: AsyncSession, *, run_id: UUID, artifact_id: UUID, released_only: bool = True
) -> ResearchArtifact | None:
    conditions = [
        ResearchArtifact.run_id == run_id,
        ResearchArtifact.id == artifact_id,
    ]
    if released_only:
        conditions.append(
            exists().where(
                ResearchReleaseCheck.run_id == run_id,
                ResearchReleaseCheck.generation == ResearchArtifact.generation,
                ResearchReleaseCheck.allowed.is_(True),
            )
        )
    return await db.scalar(select(ResearchArtifact).where(*conditions))


async def count_ineligible_analyses(db: AsyncSession, *, run_id: UUID) -> int:
    """Count final analyses missing a strict PASS ledger or explicitly ineligible."""
    value = await db.scalar(
        select(func.count(ResearchPaperAnalysis.id))
        .outerjoin(
            ResearchWorkEligibility,
            (ResearchWorkEligibility.run_id == ResearchPaperAnalysis.run_id)
            & (ResearchWorkEligibility.work_id == ResearchPaperAnalysis.work_id),
        )
        .where(
            ResearchPaperAnalysis.run_id == run_id,
            or_(
                ResearchWorkEligibility.id.is_(None),
                ResearchWorkEligibility.eligible.is_(False),
            ),
        )
    )
    return int(value or 0)


async def count_duplicate_conflicts(db: AsyncSession, *, run_id: UUID) -> int:
    """Count unresolved REVIEW decisions for works included in final analysis."""
    result = await db.execute(
        select(ResearchWork.duplicate_decisions_json)
        .join(ResearchPaperAnalysis, ResearchPaperAnalysis.work_id == ResearchWork.id)
        .where(ResearchPaperAnalysis.run_id == run_id)
    )
    return sum(
        item.get("decision") == "REVIEW"
        for decisions in result.scalars().all()
        for item in decisions
    )


async def persist_release_check(
    db: AsyncSession,
    *,
    run_id: UUID,
    snapshot: ReleaseSnapshot,
    decision: ReleaseDecision,
    generation: int = 1,
) -> ResearchReleaseCheck:
    row = ResearchReleaseCheck(
        run_id=run_id,
        generation=generation,
        allowed=decision.allowed,
        partial=decision.partial,
        blockers_json=[item.value for item in decision.blockers],
        snapshot_json=snapshot.model_dump(mode="json"),
        checked_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row


async def get_or_create_reanalysis_task(
    db: AsyncSession,
    *,
    run_id: UUID,
    work_id: UUID,
    input_hash: str,
) -> tuple[ResearchTaskExecution, bool]:
    existing = await db.scalar(
        select(ResearchTaskExecution).where(
            ResearchTaskExecution.run_id == run_id,
            ResearchTaskExecution.stage == "REANALYZE",
            ResearchTaskExecution.shard_key == str(work_id),
            ResearchTaskExecution.input_hash == input_hash,
        )
    )
    if existing is not None:
        return existing, False
    row = ResearchTaskExecution(
        run_id=run_id,
        stage="REANALYZE",
        shard_key=str(work_id),
        input_hash=input_hash,
        status="PENDING",
    )
    db.add(row)
    await db.flush()
    return row, True


async def get_or_create_initial_analysis_task(
    db: AsyncSession,
    *,
    run_id: UUID,
    work_id: UUID,
    input_hash: str,
) -> tuple[ResearchTaskExecution, bool]:
    """Create the durable, idempotent shard for one initial paper analysis."""
    existing = await db.scalar(
        select(ResearchTaskExecution).where(
            ResearchTaskExecution.run_id == run_id,
            ResearchTaskExecution.stage == "ANALYZE_PAPER",
            ResearchTaskExecution.shard_key == str(work_id),
            ResearchTaskExecution.input_hash == input_hash,
        )
    )
    if existing is not None:
        return existing, False
    row = ResearchTaskExecution(
        run_id=run_id,
        stage="ANALYZE_PAPER",
        shard_key=str(work_id),
        input_hash=input_hash,
        status="PENDING",
    )
    db.add(row)
    await db.flush()
    return row, True


async def list_initial_analysis_tasks(
    db: AsyncSession,
    *,
    run_id: UUID,
) -> list[ResearchTaskExecution]:
    result = await db.execute(
        select(ResearchTaskExecution)
        .where(
            ResearchTaskExecution.run_id == run_id,
            ResearchTaskExecution.stage == "ANALYZE_PAPER",
        )
        .order_by(ResearchTaskExecution.shard_key.asc())
    )
    return list(result.scalars().all())


async def summarize_initial_analysis_tasks(
    db: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, int]:
    """Return the PostgreSQL barrier counts for initial paper-analysis shards."""
    result = await db.execute(
        select(ResearchTaskExecution.status, func.count(ResearchTaskExecution.id))
        .where(
            ResearchTaskExecution.run_id == run_id,
            ResearchTaskExecution.stage == "ANALYZE_PAPER",
        )
        .group_by(ResearchTaskExecution.status)
    )
    counts = {str(status): int(count) for status, count in result.all()}
    return {
        "total": sum(counts.values()),
        "succeeded": counts.get("SUCCEEDED", 0),
        "failed_terminal": counts.get("FAILED_TERMINAL", 0),
        "blocked": counts.get("BLOCKED", 0),
        "pending": counts.get("PENDING", 0),
        "running": counts.get("RUNNING", 0),
        "failed_retryable": counts.get("FAILED_RETRYABLE", 0),
    }


async def get_or_create_artifact_regeneration_task(
    db: AsyncSession,
    *,
    run_id: UUID,
    input_hash: str,
) -> tuple[ResearchTaskExecution, bool]:
    existing = await db.scalar(
        select(ResearchTaskExecution).where(
            ResearchTaskExecution.run_id == run_id,
            ResearchTaskExecution.stage == "REGENERATE_ARTIFACTS",
            ResearchTaskExecution.shard_key == "main",
            ResearchTaskExecution.input_hash == input_hash,
        )
    )
    if existing is not None:
        return existing, False
    row = ResearchTaskExecution(
        run_id=run_id,
        stage="REGENERATE_ARTIFACTS",
        shard_key="main",
        input_hash=input_hash,
        status="PENDING",
    )
    db.add(row)
    await db.flush()
    return row, True


async def get_task_execution(
    db: AsyncSession, *, task_execution_id: UUID, for_update: bool = False
) -> ResearchTaskExecution | None:
    query = select(ResearchTaskExecution).where(ResearchTaskExecution.id == task_execution_id)
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)

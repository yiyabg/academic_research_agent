"""Persistence and source-observation queries for offline evaluation."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.discovery import ResearchWork, ResearchWorkVersion
from app.db.models.literature_research.evaluation import (
    ResearchEvaluationDataset,
    ResearchEvaluationResult,
)
from app.db.models.literature_research.run import ResearchTaskExecution
from app.schemas.literature_research.evaluation import (
    EvaluationDatasetCreate,
    EvaluationReport,
)


async def create_dataset(
    db: AsyncSession, *, created_by: UUID, body: EvaluationDatasetCreate
) -> ResearchEvaluationDataset:
    payload = {
        "cases": [item.model_dump(mode="json") for item in body.cases],
        "observations": [item.model_dump(mode="json") for item in body.observations],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = ResearchEvaluationDataset(
        project_id=body.project_id,
        name=body.name,
        version=body.version,
        description=body.description,
        cases_json=payload["cases"],
        observations_json=payload["observations"],
        payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        case_count=len(body.cases),
        status=body.status.value,
        provenance_json=(body.provenance.model_dump(mode="json") if body.provenance else None),
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def get_dataset(
    db: AsyncSession, *, dataset_id: UUID, project_id: UUID
) -> ResearchEvaluationDataset | None:
    return await db.scalar(
        select(ResearchEvaluationDataset).where(
            ResearchEvaluationDataset.id == dataset_id,
            ResearchEvaluationDataset.project_id == project_id,
        )
    )


async def list_datasets(db: AsyncSession, *, project_id: UUID) -> list[ResearchEvaluationDataset]:
    result = await db.execute(
        select(ResearchEvaluationDataset)
        .where(ResearchEvaluationDataset.project_id == project_id)
        .order_by(ResearchEvaluationDataset.created_at.desc())
    )
    return list(result.scalars().all())


async def list_source_clusters(db: AsyncSession, *, run_id: UUID) -> dict[tuple[str, str], str]:
    result = await db.execute(
        select(
            ResearchWorkVersion.source,
            ResearchWorkVersion.source_id,
            ResearchWork.cluster_key,
        )
        .join(ResearchWork, ResearchWork.id == ResearchWorkVersion.work_id)
        .where(ResearchWork.run_id == run_id)
    )
    return {(source, source_id): cluster for source, source_id, cluster in result.all()}


async def list_task_executions(db: AsyncSession, *, run_id: UUID) -> list[ResearchTaskExecution]:
    result = await db.execute(
        select(ResearchTaskExecution).where(ResearchTaskExecution.run_id == run_id)
    )
    return list(result.scalars().all())


async def persist_result(db: AsyncSession, *, report: EvaluationReport) -> ResearchEvaluationResult:
    row = ResearchEvaluationResult(
        dataset_id=report.dataset_id,
        run_id=report.run_id,
        dataset_hash=report.dataset_hash,
        metrics_json={key: value.model_dump(mode="json") for key, value in report.metrics.items()},
        passed=report.passed,
        failures_json=report.failures,
        details_json=report.details,
        evaluated_at=report.evaluated_at,
    )
    db.add(row)
    await db.flush()
    return row


async def list_results(db: AsyncSession, *, run_id: UUID) -> list[ResearchEvaluationResult]:
    result = await db.execute(
        select(ResearchEvaluationResult)
        .where(ResearchEvaluationResult.run_id == run_id)
        .order_by(ResearchEvaluationResult.evaluated_at.desc())
    )
    return list(result.scalars().all())

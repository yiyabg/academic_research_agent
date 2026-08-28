"""Versioned gold-dataset and offline evaluation endpoints."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, ResearchEvaluationSvc
from app.core.exceptions import ConflictError
from app.schemas.literature_research.evaluation import (
    EvaluationDatasetCreate,
    EvaluationDatasetRead,
    EvaluationReport,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/evaluation-datasets",
    response_model=EvaluationDatasetRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    project_id: UUID,
    body: EvaluationDatasetCreate,
    current_user: CurrentUser,
    service: ResearchEvaluationSvc,
) -> object:
    if body.project_id != project_id:
        raise ConflictError(message="Path and body project_id must match")
    dataset = await service.create_dataset(owner_id=current_user.id, body=body)
    await service.db.commit()
    return dataset


@router.get(
    "/projects/{project_id}/evaluation-datasets",
    response_model=list[EvaluationDatasetRead],
)
async def list_datasets(
    project_id: UUID,
    current_user: CurrentUser,
    service: ResearchEvaluationSvc,
) -> object:
    return await service.list_datasets(owner_id=current_user.id, project_id=project_id)


@router.post(
    "/runs/{run_id}/evaluations/{dataset_id}",
    response_model=EvaluationReport,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_run(
    run_id: UUID,
    dataset_id: UUID,
    current_user: CurrentUser,
    service: ResearchEvaluationSvc,
) -> object:
    report = await service.evaluate(owner_id=current_user.id, run_id=run_id, dataset_id=dataset_id)
    await service.db.commit()
    return report


@router.get("/runs/{run_id}/evaluations", response_model=list[EvaluationReport])
async def list_evaluations(
    run_id: UUID,
    current_user: CurrentUser,
    service: ResearchEvaluationSvc,
) -> object:
    return await service.list_results(owner_id=current_user.id, run_id=run_id)

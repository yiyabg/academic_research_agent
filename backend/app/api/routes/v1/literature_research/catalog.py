"""Candidate and paper-detail workbench endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, ResearchCatalogSvc
from app.schemas.literature_research.catalog import (
    CandidatePage,
    PaperDetailRead,
    ReanalysisAccepted,
    ReanalysisRequest,
)
from app.schemas.literature_research.evidence import EvidenceLocator
from app.worker.tasks.literature_research_tasks import reanalyze_research_paper

router = APIRouter()


@router.get("/{run_id}/candidates", response_model=CandidatePage)
async def list_candidates(
    run_id: UUID,
    current_user: CurrentUser,
    service: ResearchCatalogSvc,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> object:
    return await service.list_candidates(
        run_id=run_id, owner_id=current_user.id, skip=skip, limit=limit
    )


@router.get("/{run_id}/papers/{work_id}", response_model=PaperDetailRead)
async def get_paper(
    run_id: UUID,
    work_id: UUID,
    current_user: CurrentUser,
    service: ResearchCatalogSvc,
) -> object:
    return await service.get_paper(run_id=run_id, work_id=work_id, owner_id=current_user.id)


@router.get("/{run_id}/papers/{work_id}/evidence", response_model=list[EvidenceLocator])
async def get_paper_evidence(
    run_id: UUID,
    work_id: UUID,
    current_user: CurrentUser,
    service: ResearchCatalogSvc,
) -> object:
    return (await service.get_paper(
        run_id=run_id, work_id=work_id, owner_id=current_user.id
    )).evidence


@router.post(
    "/{run_id}/papers/{work_id}:reanalyze",
    response_model=ReanalysisAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reanalyze_paper(
    run_id: UUID,
    work_id: UUID,
    body: ReanalysisRequest,
    response: Response,
    current_user: CurrentUser,
    service: ResearchCatalogSvc,
) -> object:
    accepted = await service.request_reanalysis(
        run_id=run_id,
        work_id=work_id,
        owner_id=current_user.id,
        request=body,
    )
    await service.db.commit()
    if accepted.created:
        reanalyze_research_paper.apply_async(
            args=(str(accepted.task_execution_id),), queue="research-llm"
        )
    else:
        response.status_code = status.HTTP_200_OK
    return accepted

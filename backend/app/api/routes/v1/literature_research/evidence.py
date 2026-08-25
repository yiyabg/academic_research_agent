"""Owned-run evidence locator retrieval endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession, ResearchRunSvc
from app.repositories.literature_research import evidence as evidence_repository
from app.schemas.literature_research.evidence import EvidenceLocator

router = APIRouter()


@router.get("/{run_id}/evidence", response_model=list[EvidenceLocator])
async def list_run_evidence(
    run_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    db: DBSession,
    work_id: UUID | None = Query(default=None),
) -> object:
    await run_service.get_owned(run_id, current_user.id)
    return await evidence_repository.list_evidence(db, run_id=run_id, work_id=work_id)

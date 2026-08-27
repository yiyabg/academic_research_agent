"""Explicit user-memory and project-grant endpoints."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.literature_research.local_library import (
    LocalPaperMemoryCandidateConfirm,
    LocalPaperMemoryCandidateCreate,
    LocalPaperMemoryCandidateRead,
)
from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService

router = APIRouter()


@router.post("/analysis-jobs/{job_id}/memory-candidates", response_model=LocalPaperMemoryCandidateRead, status_code=status.HTTP_201_CREATED)
async def create_memory_candidate(job_id: UUID, body: LocalPaperMemoryCandidateCreate, db: DBSession, current_user: CurrentUser) -> object:
    row = await LocalPaperAnalysisService(db).create_memory_candidate(job_id=job_id, owner_id=current_user.id, body=body)
    await db.commit()
    return row


@router.post("/memory-candidates/{candidate_id}:confirm", response_model=LocalPaperMemoryCandidateRead)
async def confirm_memory_candidate(candidate_id: UUID, body: LocalPaperMemoryCandidateConfirm, db: DBSession, current_user: CurrentUser) -> object:
    row = await LocalPaperAnalysisService(db).confirm_memory_candidate(candidate_id=candidate_id, owner_id=current_user.id, confirmation_note=body.confirmation_note)
    await db.commit()
    return row


@router.post("/projects/{project_id}:grant", status_code=status.HTTP_204_NO_CONTENT)
async def grant_project(project_id: UUID, db: DBSession, current_user: CurrentUser) -> Response:
    await LocalPaperAnalysisService(db).grant_project(owner_id=current_user.id, project_id=project_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Search, grounded ask, and export endpoints for the private paper library."""

from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, DBSession
from app.schemas.literature_research.local_library import (
    LocalPaperAskRequest,
    LocalPaperAskResponse,
    LocalPaperExportRequest,
    LocalPaperSearchRequest,
    LocalPaperSearchResponse,
)
from app.services.literature_research.local_paper_library import LocalPaperLibraryService

router = APIRouter()


@router.post("/search", response_model=LocalPaperSearchResponse)
async def search_local_library(body: LocalPaperSearchRequest, db: DBSession, current_user: CurrentUser) -> object:
    return await LocalPaperLibraryService(db).search(owner_id=current_user.id, request=body)


@router.post("/ask", response_model=LocalPaperAskResponse)
async def ask_local_library(body: LocalPaperAskRequest, db: DBSession, current_user: CurrentUser) -> object:
    return await LocalPaperLibraryService(db).ask(
        owner_id=current_user.id,
        question=body.question,
        limit=body.limit,
        paper_ids=body.paper_ids,
        query_context=body.query_context,
    )


@router.post("/export")
async def export_local_library(body: LocalPaperExportRequest, db: DBSession, current_user: CurrentUser) -> Response:
    request = LocalPaperSearchRequest(**body.model_dump(exclude={"format"}))
    payload, media_type, filename = await LocalPaperLibraryService(db).export(
        owner_id=current_user.id, request=request, format=body.format
    )
    return Response(content=payload, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

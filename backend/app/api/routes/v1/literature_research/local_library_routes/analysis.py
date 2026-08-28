"""Durable analysis-session/job endpoints; no model work runs in the route."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.literature_research.local_library import (
    LocalPaperAnalysisCreate,
    LocalPaperAnalysisEventRead,
    LocalPaperAnalysisJobRead,
    LocalPaperAnalysisSessionCreate,
    LocalPaperAnalysisSessionRead,
    LocalPaperMindmapRequest,
)
from app.services.literature_research.local_paper_analysis import LocalPaperAnalysisService
from app.worker.tasks.local_paper_library_tasks import run_local_paper_analysis

router = APIRouter()


async def _enqueue(job_id: UUID) -> None:
    run_local_paper_analysis.apply_async(args=(str(job_id),), queue="research-llm")


@router.post(
    "/analysis-sessions",
    response_model=LocalPaperAnalysisSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: LocalPaperAnalysisSessionCreate, db: DBSession, current_user: CurrentUser
) -> object:
    row = await LocalPaperAnalysisService(db).create_session(owner_id=current_user.id, body=body)
    await db.commit()
    return row


@router.get("/analysis-sessions/{session_id}", response_model=LocalPaperAnalysisSessionRead)
async def get_session(session_id: UUID, db: DBSession, current_user: CurrentUser) -> object:
    return await LocalPaperAnalysisService(db).get_session(
        session_id=session_id, owner_id=current_user.id
    )


async def _create_job(
    body: LocalPaperAnalysisCreate, response: Response, db: DBSession, current_user: CurrentUser
) -> object:
    service = LocalPaperAnalysisService(db)
    try:
        job, created = await service.create_job(owner_id=current_user.id, body=body)
    except ValueError as exc:
        if str(exc) == "BACKGROUND_STORAGE_NOT_ALLOWED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="后台分析需要管理员显式允许模型服务临时保存任务。",
            ) from exc
        raise
    await db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
        return job
    try:
        await _enqueue(job.id)
    except Exception as exc:
        job.status, job.error_code, job.error_message = (
            "FAILED",
            "QUEUE_UNAVAILABLE",
            "分析队列不可用。",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="分析队列不可用"
        ) from exc
    return job


@router.post(
    "/analysis-jobs", response_model=LocalPaperAnalysisJobRead, status_code=status.HTTP_202_ACCEPTED
)
async def create_job(
    body: LocalPaperAnalysisCreate, response: Response, db: DBSession, current_user: CurrentUser
) -> object:
    return await _create_job(body, response, db, current_user)


@router.get("/analysis-jobs/{job_id}", response_model=LocalPaperAnalysisJobRead)
async def get_job(job_id: UUID, db: DBSession, current_user: CurrentUser) -> object:
    return await LocalPaperAnalysisService(db).get_job(job_id=job_id, owner_id=current_user.id)


@router.post("/analysis-jobs/{job_id}:cancel", response_model=LocalPaperAnalysisJobRead)
async def cancel_job(job_id: UUID, db: DBSession, current_user: CurrentUser) -> object:
    row = await LocalPaperAnalysisService(db).request_cancel(
        job_id=job_id, owner_id=current_user.id
    )
    await db.commit()
    await _enqueue(row.id)
    return row


@router.get("/analysis-jobs/{job_id}/events", response_model=list[LocalPaperAnalysisEventRead])
async def list_events(
    job_id: UUID, db: DBSession, current_user: CurrentUser, after_sequence: int = 0
) -> object:
    return await LocalPaperAnalysisService(db).list_events(
        job_id=job_id, owner_id=current_user.id, after_sequence=after_sequence
    )


@router.get("/analysis-jobs/{job_id}/artifact")
async def artifact(job_id: UUID, db: DBSession, current_user: CurrentUser) -> Response:
    content, output_format, digest = await LocalPaperAnalysisService(db).artifact(
        job_id=job_id, owner_id=current_user.id
    )
    extension = "opml" if output_format == "opml" else "md"
    return Response(
        content=content,
        media_type="text/x-opml; charset=utf-8"
        if extension == "opml"
        else "text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="local-paper-analysis-{job_id}.{extension}"',
            "X-Content-SHA256": digest,
        },
    )


@router.post(
    "/mindmap",
    deprecated=True,
    response_model=LocalPaperAnalysisJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def legacy_mindmap(
    body: LocalPaperMindmapRequest, response: Response, db: DBSession, current_user: CurrentUser
) -> object:
    return await _create_job(
        LocalPaperAnalysisCreate(
            question=body.question or body.query,
            query=body.query,
            limit=body.limit,
            output_format=body.output_format,
        ),
        response,
        db,
        current_user,
    )

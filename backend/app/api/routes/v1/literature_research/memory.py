"""Five-layer memory, policy, profile, and feedback endpoints."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    CurrentAppAdmin,
    CurrentUser,
    DBSession,
    Redis,
    ResearchProjectSvc,
    ResearchRunSvc,
)
from app.core.exceptions import NotFoundError
from app.repositories.literature_research import catalog as catalog_repository
from app.repositories.literature_research import memory as memory_repository
from app.schemas.literature_research.memory import (
    FeedbackAccepted,
    MemorySource,
    MemoryType,
    PolicyVersionCreate,
    PolicyVersionRead,
    ProjectMemoryCreate,
    ProjectMemoryRead,
    ResearchFeedbackCreate,
    ResearchProfileConfirm,
    ResearchProfileRead,
    SessionMemoryRead,
    SessionMemoryWrite,
)
from app.services.literature_research.session_memory import ResearchSessionMemoryService
from app.worker.tasks.literature_research_tasks import index_research_project_memory

router = APIRouter()
logger = logging.getLogger(__name__)


def _enqueue_memory_index(memory_id: UUID) -> None:
    try:
        index_research_project_memory.apply_async(
            args=(str(memory_id),), queue="research-cpu"
        )
    except Exception:
        logger.exception("Project memory %s persisted but could not be indexed", memory_id)


@router.post(
    "/projects/{project_id}/memories",
    response_model=ProjectMemoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_memory(
    project_id: UUID,
    body: ProjectMemoryCreate,
    current_user: CurrentUser,
    project_service: ResearchProjectSvc,
    db: DBSession,
) -> object:
    await project_service.get_owned(project_id, current_user.id)
    row = await memory_repository.create_project_memory(
        db, project_id=project_id, created_by=current_user.id, body=body
    )
    await db.commit()
    _enqueue_memory_index(row.id)
    return row


@router.get("/projects/{project_id}/memories", response_model=list[ProjectMemoryRead])
async def list_project_memories(
    project_id: UUID,
    current_user: CurrentUser,
    project_service: ResearchProjectSvc,
    db: DBSession,
) -> object:
    await project_service.get_owned(project_id, current_user.id)
    return await memory_repository.list_project_memories(db, project_id=project_id)


@router.post("/me/profile", response_model=ResearchProfileRead)
async def confirm_profile(
    body: ResearchProfileConfirm,
    current_user: CurrentUser,
    db: DBSession,
) -> object:
    profile = await memory_repository.confirm_profile(
        db, user_id=current_user.id, body=body
    )
    await db.commit()
    return profile


@router.get("/me/profile", response_model=ResearchProfileRead | None)
async def get_profile(current_user: CurrentUser, db: DBSession) -> object:
    return await memory_repository.get_latest_profile(db, user_id=current_user.id)


@router.put("/sessions/{session_id}/memory", response_model=SessionMemoryRead)
async def put_session_memory(
    session_id: UUID,
    body: SessionMemoryWrite,
    current_user: CurrentUser,
    redis: Redis,
) -> object:
    return await ResearchSessionMemoryService(redis).put(
        user_id=current_user.id, session_id=session_id, body=body
    )


@router.get("/sessions/{session_id}/memory", response_model=SessionMemoryRead | None)
async def get_session_memory(
    session_id: UUID,
    current_user: CurrentUser,
    redis: Redis,
) -> object:
    return await ResearchSessionMemoryService(redis).get(
        user_id=current_user.id, session_id=session_id
    )


@router.post(
    "/admin/policies",
    response_model=PolicyVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy_version(
    body: PolicyVersionCreate,
    _admin: CurrentAppAdmin,
    db: DBSession,
) -> object:
    policy = await memory_repository.create_policy_version(db, body=body)
    await db.commit()
    return policy


@router.get("/policies", response_model=list[PolicyVersionRead])
async def list_policy_versions(
    _current_user: CurrentUser,
    db: DBSession,
    policy_key: str | None = None,
) -> object:
    return await memory_repository.list_policy_versions(db, policy_key=policy_key)


@router.post(
    "/runs/{run_id}/feedback",
    response_model=FeedbackAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def create_feedback(
    run_id: UUID,
    body: ResearchFeedbackCreate,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    db: DBSession,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    candidate = None
    if body.work_id is not None:
        candidate = await catalog_repository.get_candidate_row(
            db, run_id=run_id, work_id=body.work_id
        )
        if candidate is None:
            raise NotFoundError(message="Feedback paper not found in this run")
    row = await memory_repository.create_feedback(
        db, run_id=run_id, user_id=current_user.id, body=body
    )
    project_memory_id = None
    if body.feedback_type.value != "ARTIFACT_RATING":
        memory_type = (
            MemoryType.EXCLUSION_DECISION
            if body.feedback_type.value == "RELEVANCE_CORRECTION"
            and body.payload.get("decision") == "EXCLUDE"
            else MemoryType.CORRECTION
        )
        work = candidate[0] if candidate is not None else None
        version = candidate[1] if candidate is not None else None
        memory = await memory_repository.create_project_memory(
            db,
            project_id=run.project_id,
            created_by=current_user.id,
            body=ProjectMemoryCreate(
                memory_type=memory_type,
                content={
                    "work_id": str(body.work_id) if body.work_id else None,
                    "feedback_type": body.feedback_type.value,
                    "correction": body.payload,
                    "paper_identity": (
                        {
                            "title": work.canonical_title,
                            "normalized_title": work.normalized_title,
                            "doi": version.doi if version is not None else None,
                            "arxiv_id": version.arxiv_id if version is not None else None,
                        }
                        if work is not None
                        else None
                    ),
                },
                source=MemorySource.USER_FEEDBACK,
                source_id=str(row.id),
                confidence=1.0,
                valid_from=datetime.now(UTC),
            ),
        )
        project_memory_id = memory.id
    await db.commit()
    if project_memory_id is not None:
        _enqueue_memory_index(project_memory_id)
    return FeedbackAccepted(feedback_id=row.id, project_memory_id=project_memory_id)

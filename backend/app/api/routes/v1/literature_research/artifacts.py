"""Owned research artifact listing, regeneration, and download endpoints."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DBSession, ResearchRunSvc
from app.core.exceptions import ConflictError, ExternalServiceError
from app.repositories.literature_research import analysis as analysis_repository
from app.schemas.literature_research.release import (
    ArtifactRead,
    ArtifactRegenerationAccepted,
    ArtifactRegenerationRequest,
)
from app.schemas.literature_research.run import ExecutionMode, RunState
from app.services.literature_research.object_store import get_research_object_store
from app.services.llm_provider import llm_is_configured, probe_llm_provider
from app.worker.tasks.literature_research_tasks import regenerate_research_artifacts

router = APIRouter()


def _catalog_artifacts_are_downloadable(run) -> bool:
    """Search-only outputs are immutable metadata catalogs, not release-gated analyses."""
    return (
        ExecutionMode(run.execution_mode) == ExecutionMode.SEARCH_ONLY
        and RunState(run.state) in {RunState.COMPLETED, RunState.PARTIALLY_COMPLETED}
    )


@router.get("/{run_id}/artifacts", response_model=list[ArtifactRead])
async def list_artifacts(
    run_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    db: DBSession,
    generation: int | None = Query(default=None, ge=1),
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    return await analysis_repository.list_artifacts(
        db,
        run_id=run_id,
        generation=generation,
        released_only=not _catalog_artifacts_are_downloadable(run),
    )


@router.post(
    "/{run_id}/artifacts:regenerate",
    response_model=ArtifactRegenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_artifacts(
    run_id: UUID,
    body: ArtifactRegenerationRequest,
    response: Response,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    db: DBSession,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    if RunState(run.state) not in {RunState.COMPLETED, RunState.PARTIALLY_COMPLETED}:
        raise ConflictError(
            message="Only a terminal research run can regenerate artifacts",
            code="ARTIFACT_REGENERATION_NOT_ALLOWED",
        )
    if ExecutionMode(run.execution_mode) != ExecutionMode.FULL_RESEARCH:
        raise ConflictError(
            message="Search-only and validation runs do not have analysis artifacts",
            code="ARTIFACTS_NOT_AVAILABLE_FOR_EXECUTION_MODE",
        )
    if not llm_is_configured():
        raise ExternalServiceError(
            message="Artifact regeneration requires an LLM provider credential",
            code="RESEARCH_LLM_NOT_CONFIGURED",
        )
    if (await probe_llm_provider())["status"] != "healthy":
        raise ExternalServiceError(
            message="Artifact regeneration requires a reachable configured LLM provider",
            code="RESEARCH_LLM_UNAVAILABLE",
        )
    digest = hashlib.sha256(f"{run.id}:{body.client_request_id}".encode()).hexdigest()
    task, created = await analysis_repository.get_or_create_artifact_regeneration_task(
        db,
        run_id=run.id,
        input_hash=f"sha256:{digest}",
    )
    await db.commit()
    if created:
        regenerate_research_artifacts.apply_async(args=(str(task.id),), queue="research-llm")
    else:
        response.status_code = status.HTTP_200_OK
    return ArtifactRegenerationAccepted(
        task_execution_id=task.id,
        run_id=run.id,
        status=task.status,
        created=created,
    )


@router.get("/{run_id}/artifacts/{artifact_id}", response_model=None)
async def download_artifact(
    run_id: UUID,
    artifact_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    db: DBSession,
) -> Response:
    run = await run_service.get_owned(run_id, current_user.id)
    artifact = await analysis_repository.get_artifact(
        db,
        run_id=run_id,
        artifact_id=artifact_id,
        released_only=not _catalog_artifacts_are_downloadable(run),
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    data = await get_research_object_store().get(artifact.object_key)
    return Response(
        content=data,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Content-SHA256": artifact.sha256,
        },
    )

"""Research run endpoints."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import (
    ActiveResearchOrganizationId,
    CurrentUser,
    ResearchRunSvc,
    ResearchWorkflowSvc,
)
from app.core.exceptions import ConflictError
from app.repositories.literature_research import run as run_repository
from app.schemas.literature_research.run import (
    ResearchRunCreate,
    ResearchRunRead,
    RunState,
    RunUserAction,
    ShortfallActionRequest,
    ShortfallReport,
)
from app.worker.tasks.literature_research_tasks import (
    execute_research_stage,
    research_queue_for_state,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _enqueue(run_id: UUID, state: RunState) -> None:
    try:
        execute_research_stage.apply_async(
            args=(str(run_id), state.value), queue=research_queue_for_state(state)
        )
    except Exception:
        logger.exception("Research run %s persisted but could not be enqueued", run_id)


@router.post("", response_model=ResearchRunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: ResearchRunCreate,
    response: Response,
    current_user: CurrentUser,
    service: ResearchRunSvc,
    active_organization_id: ActiveResearchOrganizationId,
) -> object:
    """Create an idempotent run; repeated client_request_id returns HTTP 200."""
    run, created = await service.create(
        body, current_user.id, active_organization_id=active_organization_id
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    else:
        await service.db.commit()
        _enqueue(run.id, RunState.QUEUED)
    return run


@router.get("", response_model=list[ResearchRunRead])
async def list_runs(
    current_user: CurrentUser,
    service: ResearchRunSvc,
    active_organization_id: ActiveResearchOrganizationId,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> object:
    return await service.list_owned(
        current_user.id,
        organization_id=active_organization_id,
        skip=skip,
        limit=limit,
    )


@router.get("/{run_id}", response_model=ResearchRunRead)
async def get_run(
    run_id: UUID,
    current_user: CurrentUser,
    service: ResearchRunSvc,
) -> object:
    return await service.get_owned(run_id, current_user.id)


@router.get("/{run_id}/shortfall", response_model=ShortfallReport)
async def get_shortfall_report(
    run_id: UUID,
    current_user: CurrentUser,
    service: ResearchRunSvc,
) -> object:
    run = await service.get_owned(run_id, current_user.id)
    if run.shortage_report_json is None:
        raise ConflictError(
            message="This run does not have a shortfall report",
            code="SHORTFALL_NOT_AVAILABLE",
        )
    return run.shortage_report_json


@router.post("/{run_id}/shortage-actions", response_model=ResearchRunRead)
async def apply_shortfall_action(
    run_id: UUID,
    body: ShortfallActionRequest,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    workflow_service: ResearchWorkflowSvc,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    if RunState(run.state) != RunState.AWAITING_RELAXATION_AUTHORIZATION:
        raise ConflictError(
            message="Run is not awaiting a shortfall decision",
            code="SHORTFALL_ACTION_NOT_ALLOWED",
        )
    if body.action == RunUserAction.CREATE_NEW_PROTOCOL_VERSION:
        raise ConflictError(
            message="Compile and approve a new protocol version before starting a new run",
            code="NEW_PROTOCOL_VERSION_REQUIRED",
        )
    next_state = (
        RunState.ANALYZING
        if body.action == RunUserAction.ACCEPT_STRICT_SHORTFALL
        else RunState.CANCEL_REQUESTED
    )
    transitioned = await workflow_service.transition(
        run_id=run.id,
        owner_id=run.owner_id,
        expected_state=RunState.AWAITING_RELAXATION_AUTHORIZATION,
        expected_version=run.state_version,
        next_state=next_state,
        progress={
            "stage": next_state.value,
            "shortfall_action": body.action.value,
            "strict_count": run.strict_count,
            "target_count": run.target_count,
        },
    )
    await workflow_service.db.commit()
    if next_state == RunState.ANALYZING:
        _enqueue(run.id, next_state)
    return transitioned


@router.post(
    "/{run_id}:cancel", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED
)
async def cancel_run(
    run_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    workflow_service: ResearchWorkflowSvc,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    if RunState(run.state) in {
        RunState.COMPLETED,
        RunState.PARTIALLY_COMPLETED,
        RunState.FAILED_TERMINAL,
        RunState.CANCELLED,
    }:
        raise ConflictError(message="A terminal run cannot be cancelled", code="RUN_TERMINAL")
    await run_repository.request_control(
        workflow_service.db,
        run_id=run.id,
        requested_by=current_user.id,
        action="cancel",
        requested_at=datetime.now(UTC),
    )
    await workflow_service.db.commit()
    _enqueue(run.id, RunState(run.state))
    return run


@router.post(
    "/{run_id}:pause", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED
)
async def pause_run(
    run_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    workflow_service: ResearchWorkflowSvc,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    if RunState(run.state) in {
        RunState.PAUSED,
        RunState.AWAITING_RELAXATION_AUTHORIZATION,
        RunState.COMPLETED,
        RunState.PARTIALLY_COMPLETED,
        RunState.FAILED_RETRYABLE,
        RunState.FAILED_TERMINAL,
        RunState.CANCELLED,
    }:
        raise ConflictError(message="This run cannot be paused", code="RUN_PAUSE_NOT_ALLOWED")
    await run_repository.request_control(
        workflow_service.db,
        run_id=run.id,
        requested_by=current_user.id,
        action="pause",
        requested_at=datetime.now(UTC),
    )
    await workflow_service.db.commit()
    _enqueue(run.id, RunState(run.state))
    return run


@router.post(
    "/{run_id}:resume", response_model=ResearchRunRead, status_code=status.HTTP_202_ACCEPTED
)
async def resume_run(
    run_id: UUID,
    current_user: CurrentUser,
    run_service: ResearchRunSvc,
    workflow_service: ResearchWorkflowSvc,
) -> object:
    run = await run_service.get_owned(run_id, current_user.id)
    current_state = RunState(run.state)
    if current_state not in {RunState.PAUSED, RunState.FAILED_RETRYABLE}:
        raise ConflictError(message="This run cannot be resumed", code="RUN_RESUME_NOT_ALLOWED")
    if current_state == RunState.PAUSED:
        resume_value = run.progress_json.get("paused_from", RunState.QUEUED.value)
    else:
        failure = run.progress_json.get("failure", {})
        resume_value = (
            failure.get("stage", RunState.QUEUED.value)
            if isinstance(failure, dict)
            else RunState.QUEUED.value
        )
    try:
        resume_state = RunState(str(resume_value))
    except ValueError:
        resume_state = RunState.QUEUED
    transitioned = await workflow_service.transition(
        run_id=run.id,
        owner_id=run.owner_id,
        expected_state=current_state,
        expected_version=run.state_version,
        next_state=resume_state,
        progress={
            **run.progress_json,
            "stage": resume_state.value,
            "resumed": True,
            "resumed_from": current_state.value,
        },
    )
    await workflow_service.db.commit()
    _enqueue(run.id, resume_state)
    return transitioned

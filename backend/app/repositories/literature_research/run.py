"""Data access for research runs and optimistic state changes."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.organization import ResearchOrganizationMember
from app.db.models.literature_research.run import ResearchRun, ResearchRunControl
from app.schemas.literature_research.run import ExecutionMode, RunState


def _accessible_by(user_id: UUID):  # type: ignore[no-untyped-def]
    is_member = exists().where(
        ResearchOrganizationMember.organization_id == ResearchRun.organization_id,
        ResearchOrganizationMember.user_id == user_id,
    )
    return or_(
        and_(ResearchRun.organization_id.is_(None), ResearchRun.owner_id == user_id),
        and_(ResearchRun.organization_id.is_not(None), is_member),
    )


async def get_owned(
    db: AsyncSession, run_id: UUID, owner_id: UUID, *, for_update: bool = False
) -> ResearchRun | None:
    query = select(ResearchRun).where(
        ResearchRun.id == run_id,
        _accessible_by(owner_id),
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_by_client_request(
    db: AsyncSession, owner_id: UUID, client_request_id: str
) -> ResearchRun | None:
    result = await db.execute(
        select(ResearchRun).where(
            ResearchRun.owner_id == owner_id,
            ResearchRun.client_request_id == client_request_id,
        )
    )
    return result.scalar_one_or_none()


async def list_owned(
    db: AsyncSession,
    owner_id: UUID,
    *,
    organization_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[ResearchRun]:
    query = select(ResearchRun).where(_accessible_by(owner_id))
    if organization_id is not None:
        query = query.where(ResearchRun.organization_id == organization_id)
    result = await db.execute(
        query.order_by(ResearchRun.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())


async def create(
    db: AsyncSession,
    *,
    project_id: UUID,
    protocol_version_id: UUID,
    owner_id: UUID,
    organization_id: UUID | None,
    execution_mode: ExecutionMode,
    client_request_id: str,
    protocol_hash: str,
    target_count: int,
) -> ResearchRun:
    run = ResearchRun(
        project_id=project_id,
        protocol_version_id=protocol_version_id,
        owner_id=owner_id,
        organization_id=organization_id,
        state=RunState.QUEUED.value,
        execution_mode=execution_mode.value,
        client_request_id=client_request_id,
        protocol_hash=protocol_hash,
        target_count=target_count,
        progress_json={"stage": RunState.QUEUED.value, "completed_units": 0},
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def transition(
    db: AsyncSession,
    *,
    run_id: UUID,
    owner_id: UUID,
    expected_state: RunState,
    expected_version: int,
    next_state: RunState,
    progress: dict[str, Any],
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> ResearchRun | None:
    values: dict[str, Any] = {
        "state": next_state.value,
        "state_version": expected_version + 1,
        "progress_json": progress,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    if started_at is not None:
        values["started_at"] = started_at
    if finished_at is not None:
        values["finished_at"] = finished_at
    result = await db.execute(
        update(ResearchRun)
        .where(
            ResearchRun.id == run_id,
            ResearchRun.owner_id == owner_id,
            ResearchRun.state == expected_state.value,
            ResearchRun.state_version == expected_version,
        )
        .values(**values)
        .returning(ResearchRun)
    )
    run = result.scalar_one_or_none()
    if run is not None:
        await db.flush()
    return run


async def request_control(
    db: AsyncSession,
    *,
    run_id: UUID,
    requested_by: UUID,
    action: str,
    requested_at: datetime,
) -> None:
    statement = insert(ResearchRunControl).values(
        run_id=run_id,
        requested_by=requested_by,
        requested_action=action,
        requested_at=requested_at,
    )
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[ResearchRunControl.run_id],
            set_={
                "requested_by": requested_by,
                "requested_action": action,
                "requested_at": requested_at,
                "updated_at": requested_at,
            },
        )
    )


async def get_control(db: AsyncSession, *, run_id: UUID) -> ResearchRunControl | None:
    return await db.scalar(select(ResearchRunControl).where(ResearchRunControl.run_id == run_id))


async def clear_control(db: AsyncSession, *, run_id: UUID) -> None:
    await db.execute(delete(ResearchRunControl).where(ResearchRunControl.run_id == run_id))


async def set_shortfall_report(
    db: AsyncSession,
    *,
    run: ResearchRun,
    report: dict[str, object],
) -> ResearchRun:
    run.shortage_report_json = report
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def set_counts_and_progress(
    db: AsyncSession,
    *,
    run: ResearchRun,
    candidate_count: int | None = None,
    strict_count: int | None = None,
    analyzed_count: int | None = None,
    progress: dict[str, Any] | None = None,
) -> ResearchRun:
    if candidate_count is not None:
        run.candidate_count = candidate_count
    if strict_count is not None:
        run.strict_count = strict_count
    if analyzed_count is not None:
        run.analyzed_count = analyzed_count
    if progress is not None:
        run.progress_json = {**run.progress_json, **progress}
    db.add(run)
    await db.flush()
    return run

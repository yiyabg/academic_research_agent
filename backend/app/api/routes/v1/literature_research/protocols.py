"""Research protocol compilation, listing, and explicit approval endpoints."""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, ResearchProtocolSvc
from app.schemas.literature_research.protocol import (
    ProtocolApproveRequest,
    ProtocolCompileRequest,
    ResearchProtocolVersionRead,
)

router = APIRouter()


@router.post(
    "/{project_id}/protocols:compile",
    response_model=ResearchProtocolVersionRead,
)
async def compile_protocol(
    project_id: UUID,
    body: ProtocolCompileRequest,
    current_user: CurrentUser,
    service: ResearchProtocolSvc,
) -> object:
    """Compile and persist a draft; this does not approve or execute it."""
    protocol = await service.compile(project_id, current_user.id, body)
    # Dependency finalizers run after the response may already be visible to a
    # client.  Commit write endpoints explicitly so an immediate approve/read
    # request cannot race the transaction teardown.
    await service.db.commit()
    return protocol


@router.post(
    "/{project_id}/protocols:advise-and-compile",
    response_model=ResearchProtocolVersionRead,
)
async def advise_and_compile_protocol(
    project_id: UUID,
    body: ProtocolCompileRequest,
    current_user: CurrentUser,
    service: ResearchProtocolSvc,
) -> object:
    """Explicit paid drafting call; it still only persists an unapproved DRAFT."""
    protocol = await service.advise_and_compile(project_id, current_user.id, body)
    await service.db.commit()
    return protocol


@router.get(
    "/{project_id}/protocols",
    response_model=list[ResearchProtocolVersionRead],
)
async def list_protocols(
    project_id: UUID,
    current_user: CurrentUser,
    service: ResearchProtocolSvc,
) -> object:
    return await service.list(project_id, current_user.id)


@router.get(
    "/{project_id}/protocols/{version}",
    response_model=ResearchProtocolVersionRead,
)
async def get_protocol(
    project_id: UUID,
    version: int,
    current_user: CurrentUser,
    service: ResearchProtocolSvc,
) -> object:
    return await service.get(project_id, version, current_user.id)


@router.post(
    "/{project_id}/protocols/{version}:approve",
    response_model=ResearchProtocolVersionRead,
)
async def approve_protocol(
    project_id: UUID,
    version: int,
    body: ProtocolApproveRequest,
    current_user: CurrentUser,
    service: ResearchProtocolSvc,
) -> object:
    """Explicitly freeze a protocol version after hash verification."""
    protocol = await service.approve(
        project_id, version, current_user.id, expected_hash=body.protocol_hash
    )
    await service.db.commit()
    return protocol

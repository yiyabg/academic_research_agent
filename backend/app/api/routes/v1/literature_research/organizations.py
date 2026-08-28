"""Research organization and membership endpoints."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, ResearchOrganizationSvc
from app.schemas.literature_research.organization import (
    ResearchOrganizationCreate,
    ResearchOrganizationMemberAdd,
    ResearchOrganizationMemberRead,
    ResearchOrganizationRead,
)

router = APIRouter()


@router.post("", response_model=ResearchOrganizationRead, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: ResearchOrganizationCreate,
    current_user: CurrentUser,
    service: ResearchOrganizationSvc,
) -> object:
    organization = await service.create(body, created_by=current_user.id)
    # The response is an authorization boundary: a follow-up request must see
    # the organization and its OWNER membership immediately.
    await service.db.commit()
    return organization


@router.get("", response_model=list[ResearchOrganizationRead])
async def list_organizations(current_user: CurrentUser, service: ResearchOrganizationSvc) -> object:
    return await service.list_for_user(current_user.id)


@router.get(
    "/{organization_id}/members",
    response_model=list[ResearchOrganizationMemberRead],
)
async def list_members(
    organization_id: UUID,
    current_user: CurrentUser,
    service: ResearchOrganizationSvc,
) -> object:
    return await service.list_members(organization_id, requested_by=current_user.id)


@router.post(
    "/{organization_id}/members",
    response_model=ResearchOrganizationMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    organization_id: UUID,
    body: ResearchOrganizationMemberAdd,
    current_user: CurrentUser,
    service: ResearchOrganizationSvc,
) -> object:
    member = await service.add_member(
        organization_id, email=str(body.email), requested_by=current_user.id
    )
    await service.db.commit()
    return member


@router.delete(
    "/{organization_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    organization_id: UUID,
    member_user_id: UUID,
    current_user: CurrentUser,
    service: ResearchOrganizationSvc,
) -> Response:
    await service.remove_member(organization_id, member_user_id, requested_by=current_user.id)
    # Revocation must take effect before 204 is observable; relying on FastAPI
    # dependency finalization permits a just-revoked caller to win a race.
    await service.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

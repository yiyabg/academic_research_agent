"""Research project endpoints."""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ActiveResearchOrganizationId, CurrentUser, ResearchProjectSvc
from app.core.exceptions import ValidationError
from app.schemas.literature_research.project import ResearchProjectCreate, ResearchProjectRead

router = APIRouter()


@router.post("", response_model=ResearchProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ResearchProjectCreate,
    current_user: CurrentUser,
    service: ResearchProjectSvc,
    active_organization_id: ActiveResearchOrganizationId,
) -> object:
    """Create a personal project or a project in the selected member organization."""
    if (
        body.organization_id is not None
        and active_organization_id is not None
        and body.organization_id != active_organization_id
    ):
        raise ValidationError(
            message="Body and X-Research-Organization-ID must identify the same organization"
        )
    organization_id = active_organization_id or body.organization_id
    project = await service.create(
        body, owner_id=current_user.id, organization_id=organization_id
    )
    await service.db.commit()
    return project


@router.get("", response_model=list[ResearchProjectRead])
async def list_projects(
    current_user: CurrentUser,
    service: ResearchProjectSvc,
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


@router.get("/{project_id}", response_model=ResearchProjectRead)
async def get_project(
    project_id: UUID,
    current_user: CurrentUser,
    service: ResearchProjectSvc,
) -> object:
    return await service.get_owned(project_id, current_user.id)

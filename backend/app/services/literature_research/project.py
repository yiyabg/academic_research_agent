"""Research project application service."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models.literature_research.project import ResearchProject
from app.repositories.literature_research import project as project_repo
from app.schemas.literature_research.project import ResearchProjectCreate
from app.services.literature_research.organization import ResearchOrganizationService


class ResearchProjectService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.organizations = ResearchOrganizationService(db)

    async def create(
        self,
        data: ResearchProjectCreate,
        *,
        owner_id: UUID,
        organization_id: UUID | None = None,
    ) -> ResearchProject:
        if organization_id is not None:
            await self.organizations.require_member(organization_id, owner_id, lock=True)
        return await project_repo.create(
            self.db,
            owner_id=owner_id,
            organization_id=organization_id,
            title=data.title,
            description=data.description,
        )

    async def get_owned(self, project_id: UUID, owner_id: UUID) -> ResearchProject:
        """Return a personal project owned by the user or an organization project they can access."""
        project = await project_repo.get_owned(self.db, project_id, owner_id)
        if project is None:
            raise NotFoundError(
                message="Research project not found",
                details={"project_id": str(project_id)},
            )
        return project

    async def list_owned(
        self,
        owner_id: UUID,
        *,
        organization_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[ResearchProject]:
        if organization_id is not None:
            await self.organizations.require_member(organization_id, owner_id)
        return await project_repo.list_owned(
            self.db,
            owner_id,
            organization_id=organization_id,
            skip=skip,
            limit=limit,
        )

"""Data access for research projects."""

from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.organization import ResearchOrganizationMember
from app.db.models.literature_research.project import ResearchProject


def _accessible_by(user_id: UUID):  # type: ignore[no-untyped-def]
    is_member = exists().where(
        ResearchOrganizationMember.organization_id == ResearchProject.organization_id,
        ResearchOrganizationMember.user_id == user_id,
    )
    return or_(
        and_(ResearchProject.organization_id.is_(None), ResearchProject.owner_id == user_id),
        and_(ResearchProject.organization_id.is_not(None), is_member),
    )


async def create(
    db: AsyncSession,
    *,
    owner_id: UUID,
    title: str,
    description: str,
    organization_id: UUID | None = None,
) -> ResearchProject:
    project = ResearchProject(
        owner_id=owner_id,
        organization_id=organization_id,
        title=title,
        description=description,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


async def get_owned(db: AsyncSession, project_id: UUID, owner_id: UUID) -> ResearchProject | None:
    result = await db.execute(
        select(ResearchProject).where(
            ResearchProject.id == project_id,
            _accessible_by(owner_id),
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
) -> list[ResearchProject]:
    query = select(ResearchProject).where(_accessible_by(owner_id))
    if organization_id is not None:
        query = query.where(ResearchProject.organization_id == organization_id)
    result = await db.execute(
        query.order_by(
            ResearchProject.updated_at.desc().nullslast(), ResearchProject.created_at.desc()
        )
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())

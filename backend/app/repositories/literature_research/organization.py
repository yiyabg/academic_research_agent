"""Persistence for research organizations and memberships."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.literature_research.organization import (
    ResearchOrganization,
    ResearchOrganizationMember,
)
from app.db.models.user import User
from app.schemas.literature_research.organization import ResearchOrganizationRole


async def create(
    db: AsyncSession, *, name: str, slug: str, created_by: UUID
) -> ResearchOrganization | None:
    statement = (
        insert(ResearchOrganization)
        .values(name=name, slug=slug, created_by=created_by)
        .on_conflict_do_nothing(index_elements=[ResearchOrganization.slug])
        .returning(ResearchOrganization)
    )
    organization = (await db.execute(statement)).scalar_one_or_none()
    if organization is None:
        return None
    membership = ResearchOrganizationMember(
        organization_id=organization.id,
        user_id=created_by,
        role=ResearchOrganizationRole.OWNER.value,
    )
    db.add(membership)
    await db.flush()
    await db.refresh(organization)
    return organization


async def get_by_slug(db: AsyncSession, slug: str) -> ResearchOrganization | None:
    return await db.scalar(
        select(ResearchOrganization).where(ResearchOrganization.slug == slug)
    )


async def get_membership(
    db: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    for_key_share: bool = False,
) -> ResearchOrganizationMember | None:
    query = select(ResearchOrganizationMember).where(
            ResearchOrganizationMember.organization_id == organization_id,
            ResearchOrganizationMember.user_id == user_id,
        )
    if for_key_share:
        query = query.with_for_update(read=True, key_share=True)
    return await db.scalar(query)


async def list_for_user(
    db: AsyncSession, *, user_id: UUID
) -> list[tuple[ResearchOrganization, ResearchOrganizationMember]]:
    rows = await db.execute(
        select(ResearchOrganization, ResearchOrganizationMember)
        .join(
            ResearchOrganizationMember,
            ResearchOrganizationMember.organization_id == ResearchOrganization.id,
        )
        .where(ResearchOrganizationMember.user_id == user_id)
        .order_by(ResearchOrganization.name.asc())
    )
    return list(rows.tuples().all())


async def list_members(
    db: AsyncSession, *, organization_id: UUID
) -> list[tuple[ResearchOrganizationMember, User]]:
    rows = await db.execute(
        select(ResearchOrganizationMember, User)
        .join(User, User.id == ResearchOrganizationMember.user_id)
        .where(ResearchOrganizationMember.organization_id == organization_id)
        .order_by(ResearchOrganizationMember.created_at.asc())
    )
    return list(rows.tuples().all())


async def add_member(
    db: AsyncSession, *, organization_id: UUID, user_id: UUID
) -> ResearchOrganizationMember | None:
    statement = (
        insert(ResearchOrganizationMember)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            role=ResearchOrganizationRole.MEMBER.value,
        )
        .on_conflict_do_nothing(
            index_elements=[
                ResearchOrganizationMember.organization_id,
                ResearchOrganizationMember.user_id,
            ]
        )
        .returning(ResearchOrganizationMember)
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def remove_member(db: AsyncSession, membership: ResearchOrganizationMember) -> bool:
    result = await db.execute(
        delete(ResearchOrganizationMember)
        .where(ResearchOrganizationMember.id == membership.id)
        .returning(ResearchOrganizationMember.id)
    )
    return result.scalar_one_or_none() is not None

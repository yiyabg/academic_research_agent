"""Research organization membership and authorization service."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, AuthorizationError, NotFoundError
from app.repositories import user_repo
from app.repositories.literature_research import organization as organization_repo
from app.schemas.literature_research.organization import (
    ResearchOrganizationCreate,
    ResearchOrganizationMemberRead,
    ResearchOrganizationRead,
    ResearchOrganizationRole,
)


class ResearchOrganizationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, body: ResearchOrganizationCreate, *, created_by: UUID
    ) -> ResearchOrganizationRead:
        normalized_slug = body.slug.lower()
        organization = await organization_repo.create(
            self.db,
            name=body.name.strip(),
            slug=normalized_slug,
            created_by=created_by,
        )
        if organization is None:
            raise AlreadyExistsError(
                message="Research organization slug already exists",
                details={"slug": normalized_slug},
            )
        return ResearchOrganizationRead.model_validate(organization).model_copy(
            update={"current_user_role": ResearchOrganizationRole.OWNER}
        )

    async def list_for_user(self, user_id: UUID) -> list[ResearchOrganizationRead]:
        rows = await organization_repo.list_for_user(self.db, user_id=user_id)
        return [
            ResearchOrganizationRead.model_validate(organization).model_copy(
                update={"current_user_role": ResearchOrganizationRole(membership.role)},
            )
            for organization, membership in rows
        ]

    async def require_member(
        self, organization_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> None:
        membership = await organization_repo.get_membership(
            self.db,
            organization_id=organization_id,
            user_id=user_id,
            for_key_share=lock,
        )
        if membership is None:
            # Deliberately hide whether the organization exists from non-members.
            raise NotFoundError(message="Research organization not found")

    async def _require_owner(self, organization_id: UUID, user_id: UUID) -> None:
        membership = await organization_repo.get_membership(
            self.db, organization_id=organization_id, user_id=user_id
        )
        if membership is None:
            raise NotFoundError(message="Research organization not found")
        if membership.role != ResearchOrganizationRole.OWNER.value:
            raise AuthorizationError(
                message="Research organization owner privileges required"
            )

    async def list_members(
        self, organization_id: UUID, *, requested_by: UUID
    ) -> list[ResearchOrganizationMemberRead]:
        await self.require_member(organization_id, requested_by)
        rows = await organization_repo.list_members(
            self.db, organization_id=organization_id
        )
        return [
            ResearchOrganizationMemberRead(
                organization_id=membership.organization_id,
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=ResearchOrganizationRole(membership.role),
                created_at=membership.created_at,
            )
            for membership, user in rows
        ]

    async def add_member(
        self, organization_id: UUID, *, email: str, requested_by: UUID
    ) -> ResearchOrganizationMemberRead:
        await self._require_owner(organization_id, requested_by)
        user = await user_repo.get_by_email(self.db, email.lower())
        if user is None or not user.is_active:
            raise NotFoundError(message="Active user account not found")
        membership = await organization_repo.add_member(
            self.db, organization_id=organization_id, user_id=user.id
        )
        if membership is None:
            raise AlreadyExistsError(message="User is already an organization member")
        return ResearchOrganizationMemberRead(
            organization_id=membership.organization_id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=ResearchOrganizationRole(membership.role),
            created_at=membership.created_at,
        )

    async def remove_member(
        self, organization_id: UUID, member_user_id: UUID, *, requested_by: UUID
    ) -> None:
        await self._require_owner(organization_id, requested_by)
        membership = await organization_repo.get_membership(
            self.db, organization_id=organization_id, user_id=member_user_id
        )
        if membership is None:
            raise NotFoundError(message="Research organization member not found")
        if membership.role == ResearchOrganizationRole.OWNER.value:
            raise AuthorizationError(message="The organization owner cannot be removed")
        if not await organization_repo.remove_member(self.db, membership):
            raise NotFoundError(message="Research organization member not found")

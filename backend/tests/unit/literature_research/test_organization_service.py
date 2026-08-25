"""Organization membership and project authorization regression tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AlreadyExistsError, AuthorizationError, NotFoundError
from app.schemas.literature_research.organization import (
    ResearchOrganizationCreate,
    ResearchOrganizationRole,
)
from app.schemas.literature_research.project import ResearchProjectCreate
from app.services.literature_research.organization import ResearchOrganizationService
from app.services.literature_research.project import ResearchProjectService


@pytest.mark.anyio
async def test_create_organization_also_creates_owner_membership() -> None:
    creator_id = uuid4()
    organization = SimpleNamespace(
        id=uuid4(),
        name="Evidence Lab",
        slug="evidence-lab",
        created_by=creator_id,
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    with (
        patch(
            "app.services.literature_research.organization.organization_repo.get_by_slug",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.literature_research.organization.organization_repo.create",
            new=AsyncMock(return_value=organization),
        ) as create,
    ):
        result = await ResearchOrganizationService(AsyncMock()).create(
            ResearchOrganizationCreate(name="Evidence Lab", slug="evidence-lab"),
            created_by=creator_id,
        )

    assert result.current_user_role == ResearchOrganizationRole.OWNER
    create.assert_awaited_once_with(
        create.call_args.args[0],
        name="Evidence Lab",
        slug="evidence-lab",
        created_by=creator_id,
    )


@pytest.mark.anyio
async def test_non_member_sees_organization_as_not_found() -> None:
    with patch(
        "app.services.literature_research.organization.organization_repo.get_membership",
        new=AsyncMock(return_value=None),
    ), pytest.raises(NotFoundError):
        await ResearchOrganizationService(AsyncMock()).require_member(uuid4(), uuid4())


@pytest.mark.anyio
async def test_only_owner_can_manage_members() -> None:
    membership = SimpleNamespace(role=ResearchOrganizationRole.MEMBER.value)
    with patch(
        "app.services.literature_research.organization.organization_repo.get_membership",
        new=AsyncMock(return_value=membership),
    ), pytest.raises(AuthorizationError):
        await ResearchOrganizationService(AsyncMock()).add_member(
            uuid4(), email="member@example.com", requested_by=uuid4()
        )


@pytest.mark.anyio
async def test_organization_project_creation_requires_current_membership() -> None:
    service = ResearchProjectService(AsyncMock())
    service.organizations.require_member = AsyncMock()  # type: ignore[method-assign]
    organization_id = uuid4()
    owner_id = uuid4()
    created = SimpleNamespace(id=uuid4())
    with patch(
        "app.services.literature_research.project.project_repo.create",
        new=AsyncMock(return_value=created),
    ) as create:
        result = await service.create(
            ResearchProjectCreate(
                title="Traceable evidence", organization_id=organization_id
            ),
            owner_id=owner_id,
            organization_id=organization_id,
        )

    assert result is created
    service.organizations.require_member.assert_awaited_once_with(
        organization_id, owner_id, lock=True
    )
    assert create.await_args.kwargs["organization_id"] == organization_id


@pytest.mark.anyio
async def test_duplicate_member_insert_is_a_domain_conflict() -> None:
    owner_membership = SimpleNamespace(role=ResearchOrganizationRole.OWNER.value)
    user = SimpleNamespace(id=uuid4(), email="member@example.com", full_name=None, is_active=True)
    with (
        patch(
            "app.services.literature_research.organization.organization_repo.get_membership",
            new=AsyncMock(return_value=owner_membership),
        ),
        patch(
            "app.services.literature_research.organization.user_repo.get_by_email",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "app.services.literature_research.organization.organization_repo.add_member",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(AlreadyExistsError, match="already an organization member"),
    ):
        await ResearchOrganizationService(AsyncMock()).add_member(
            uuid4(), email=user.email, requested_by=uuid4()
        )

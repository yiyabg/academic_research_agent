"""SQL-level regression checks for personal and organization isolation predicates."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.repositories.literature_research import project as project_repository
from app.repositories.literature_research import run as run_repository


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("repository", "resource_table"),
    [
        (project_repository, "research_projects"),
        (run_repository, "research_runs"),
    ],
)
async def test_access_query_requires_personal_owner_or_current_org_membership(
    repository: object, resource_table: str
) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    await repository.get_owned(db, uuid4(), uuid4())  # type: ignore[attr-defined]

    statement = str(db.execute.await_args.args[0])
    assert f"{resource_table}.organization_id IS NULL" in statement
    assert f"{resource_table}.owner_id" in statement
    assert "research_organization_members.organization_id" in statement
    assert "research_organization_members.user_id" in statement

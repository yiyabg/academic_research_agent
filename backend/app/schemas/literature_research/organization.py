"""Organization and membership schemas for collaborative research."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.base import BaseSchema


class ResearchOrganizationRole(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class ResearchOrganizationCreate(BaseSchema):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )


class ResearchOrganizationRead(BaseSchema):
    id: UUID
    name: str
    slug: str
    created_by: UUID
    current_user_role: ResearchOrganizationRole | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ResearchOrganizationMemberAdd(BaseSchema):
    email: EmailStr


class ResearchOrganizationMemberRead(BaseSchema):
    organization_id: UUID
    user_id: UUID
    email: EmailStr
    full_name: str | None = None
    role: ResearchOrganizationRole
    created_at: datetime

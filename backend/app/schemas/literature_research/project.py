"""Research project API schemas."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class ResearchProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ResearchProjectCreate(BaseSchema):
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(default="", max_length=4000)
    organization_id: UUID | None = None


class ResearchProjectRead(BaseSchema):
    id: UUID
    owner_id: UUID
    organization_id: UUID | None = None
    title: str
    description: str
    status: ResearchProjectStatus
    created_at: datetime
    updated_at: datetime | None = None

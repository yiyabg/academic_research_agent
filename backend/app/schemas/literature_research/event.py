"""Persisted research run event schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.schemas.base import BaseSchema


class ResearchEventType(StrEnum):
    RUN_STATE_CHANGED = "RUN_STATE_CHANGED"
    STAGE_PROGRESS = "STAGE_PROGRESS"
    SOURCE_PROGRESS = "SOURCE_PROGRESS"
    FUNNEL_UPDATED = "FUNNEL_UPDATED"
    PAPER_STATE_CHANGED = "PAPER_STATE_CHANGED"
    QUALITY_ALERT = "QUALITY_ALERT"
    SHORTAGE_REQUIRES_ACTION = "SHORTAGE_REQUIRES_ACTION"
    ARTIFACT_READY = "ARTIFACT_READY"
    RUN_FAILED = "RUN_FAILED"


class ResearchRunEventRead(BaseSchema):
    id: UUID
    event_id: UUID
    run_id: UUID
    sequence: int
    event_type: ResearchEventType
    stage: str
    occurred_at: datetime
    payload: dict[str, Any]
    published_at: datetime | None = None

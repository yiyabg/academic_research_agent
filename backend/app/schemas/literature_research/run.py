"""Research run schemas and state definitions."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class ExecutionMode(StrEnum):
    VALIDATE_ONLY = "validate_only"
    SEARCH_ONLY = "search_only"
    FULL_RESEARCH = "full_research"


class RunState(StrEnum):
    DRAFT = "DRAFT"
    PROTOCOL_VALIDATING = "PROTOCOL_VALIDATING"
    AWAITING_PROTOCOL_APPROVAL = "AWAITING_PROTOCOL_APPROVAL"
    QUEUED = "QUEUED"
    DISCOVERING = "DISCOVERING"
    NORMALIZING = "NORMALIZING"
    ENRICHING_METRICS = "ENRICHING_METRICS"
    DEDUPLICATING = "DEDUPLICATING"
    HARD_FILTERING = "HARD_FILTERING"
    RELEVANCE_SCORING = "RELEVANCE_SCORING"
    FULLTEXT_ACQUIRING = "FULLTEXT_ACQUIRING"
    PARSING = "PARSING"
    SELECTING = "SELECTING"
    ANALYZING = "ANALYZING"
    EVIDENCE_AUDITING = "EVIDENCE_AUDITING"
    SYNTHESIZING = "SYNTHESIZING"
    RENDERING = "RENDERING"
    RELEASE_CHECKING = "RELEASE_CHECKING"
    COMPLETED = "COMPLETED"
    AWAITING_USER_CLARIFICATION = "AWAITING_USER_CLARIFICATION"
    AWAITING_RELAXATION_AUTHORIZATION = "AWAITING_RELAXATION_AUTHORIZATION"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class RunUserAction(StrEnum):
    ACCEPT_STRICT_SHORTFALL = "accept_strict_shortfall"
    CREATE_NEW_PROTOCOL_VERSION = "create_new_protocol_version"
    CANCEL = "cancel"


class SearchExhaustion(BaseSchema):
    all_query_families_executed: bool
    all_sources_paginated_to_stop_rule: bool
    citation_neighbors_explored: bool
    keyword_neighbors_explored: bool


class ShortfallReport(BaseSchema):
    run_id: UUID
    target_count: int
    strict_count: int
    search_exhaustion: SearchExhaustion
    loss_funnel: dict[str, int]
    strict_papers: list[UUID] = Field(default_factory=list)
    candidate_buckets: dict[str, list[UUID]] = Field(default_factory=dict)
    allowed_actions: list[RunUserAction] = Field(
        default_factory=lambda: [
            RunUserAction.ACCEPT_STRICT_SHORTFALL,
            RunUserAction.CREATE_NEW_PROTOCOL_VERSION,
            RunUserAction.CANCEL,
        ]
    )


class ResearchRunCreate(BaseSchema):
    project_id: UUID
    protocol_version: int = Field(ge=1)
    execution_mode: ExecutionMode = ExecutionMode.FULL_RESEARCH
    force_refresh_sources: bool = False
    client_request_id: str = Field(min_length=8, max_length=128)


class ResearchRunRead(BaseSchema):
    id: UUID
    project_id: UUID
    protocol_version_id: UUID
    owner_id: UUID
    organization_id: UUID | None = None
    state: RunState
    state_version: int
    execution_mode: ExecutionMode
    client_request_id: str
    protocol_hash: str
    target_count: int
    strict_count: int
    candidate_count: int
    analyzed_count: int
    progress: dict[str, Any] = Field(validation_alias="progress_json")
    shortage_report: ShortfallReport | None = Field(
        default=None, validation_alias="shortage_report_json"
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failed_code: str | None = None
    failed_detail: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None


class RunTransitionRequest(BaseSchema):
    expected_state: RunState
    expected_version: int = Field(ge=0)
    next_state: RunState
    progress: dict[str, Any] | None = None


class ShortfallActionRequest(BaseSchema):
    action: RunUserAction

"""Explicitly scoped research memories, profiles, policies, and feedback."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema

_HARD_MEMORY_KEYS = {
    "constraints",
    "time_scope",
    "quantity_policy",
    "quality_floor",
    "approved_protocol_hash",
}
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "password",
    "cookie",
    "secret",
    "token",
    "credential",
)


def _matching_paths(
    value: Any, *, markers: set[str] | tuple[str, ...], contains: bool, path: str = "content"
) -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.lower()
            child_path = f"{path}.{key}"
            matched = (
                any(marker in normalized for marker in markers)
                if contains
                else normalized in markers
            )
            if matched:
                matches.append(child_path)
            matches.extend(
                _matching_paths(
                    child,
                    markers=markers,
                    contains=contains,
                    path=child_path,
                )
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(
                _matching_paths(
                    child,
                    markers=markers,
                    contains=contains,
                    path=f"{path}[{index}]",
                )
            )
    return matches


def _reject_sensitive_keys(value: Any) -> None:
    paths = _matching_paths(value, markers=_SENSITIVE_KEY_MARKERS, contains=True)
    if paths:
        raise ValueError(f"memory cannot store credential-shaped fields: {', '.join(paths)}")


class MemoryType(StrEnum):
    QUERY_TERM = "QUERY_TERM"
    EXCLUSION_DECISION = "EXCLUSION_DECISION"
    CORRECTION = "CORRECTION"
    DISPLAY_PREFERENCE = "DISPLAY_PREFERENCE"
    ARTIFACT_NOTE = "ARTIFACT_NOTE"


class MemorySource(StrEnum):
    USER_FEEDBACK = "USER_FEEDBACK"
    APPROVED_PROTOCOL = "APPROVED_PROTOCOL"
    VERIFIED_SYSTEM_EVENT = "VERIFIED_SYSTEM_EVENT"


class ProjectMemoryCreate(BaseSchema):
    memory_type: MemoryType
    content: dict[str, Any]
    source: MemorySource
    source_id: str = Field(min_length=1, max_length=255)
    confidence: float = Field(ge=0, le=1)
    valid_from: datetime
    valid_to: datetime | None = None
    supersedes: UUID | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "ProjectMemoryCreate":
        if self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        hard_paths = _matching_paths(self.content, markers=_HARD_MEMORY_KEYS, contains=False)
        if hard_paths:
            raise ValueError(
                "memory cannot override approved protocol hard semantics: " + ", ".join(hard_paths)
            )
        _reject_sensitive_keys(self.content)
        return self


class ProjectMemoryRead(ProjectMemoryCreate):
    content: dict[str, Any] = Field(validation_alias="content_json")
    id: UUID
    project_id: UUID
    created_by: UUID
    created_at: datetime


class ResearchProfileConfirm(BaseSchema):
    preferences: dict[str, Any]
    confirmation_note: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def contains_no_credentials(self) -> "ResearchProfileConfirm":
        _reject_sensitive_keys(self.preferences)
        return self


class ResearchProfileRead(BaseSchema):
    id: UUID
    user_id: UUID
    version: int
    preferences: dict[str, Any] = Field(validation_alias="preferences_json")
    confirmation_note: str
    confirmed_at: datetime
    created_at: datetime


class FeedbackType(StrEnum):
    RELEVANCE_CORRECTION = "RELEVANCE_CORRECTION"
    ANALYSIS_CORRECTION = "ANALYSIS_CORRECTION"
    EVIDENCE_CORRECTION = "EVIDENCE_CORRECTION"
    ARTIFACT_RATING = "ARTIFACT_RATING"


class ResearchFeedbackCreate(BaseSchema):
    work_id: UUID | None = None
    feedback_type: FeedbackType
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_feedback_contract(self) -> "ResearchFeedbackCreate":
        if self.feedback_type != FeedbackType.ARTIFACT_RATING and self.work_id is None:
            raise ValueError("paper-level feedback requires work_id")
        if self.feedback_type == FeedbackType.RELEVANCE_CORRECTION:
            decision = self.payload.get("decision")
            if decision not in {"INCLUDE", "EXCLUDE", "REVIEW"}:
                raise ValueError(
                    "relevance correction decision must be INCLUDE, EXCLUDE, or REVIEW"
                )
        if self.feedback_type == FeedbackType.ARTIFACT_RATING:
            rating = self.payload.get("rating")
            if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
                raise ValueError("artifact rating must be an integer from 1 to 5")
        return self


class ResolvedMemoryContext(BaseSchema):
    values: dict[str, Any]
    provenance: dict[str, str]
    ignored: list[str] = Field(default_factory=list)


class SessionMemoryWrite(BaseSchema):
    project_id: UUID | None = None
    active_run_id: UUID | None = None
    draft_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list)
    source_message_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def remains_unapproved(self) -> "SessionMemoryWrite":
        forbidden = {"approved", "approved_protocol_hash", "protocol_status"}
        if forbidden & set(self.draft_slots):
            raise ValueError("session memory cannot represent protocol approval")
        return self


class SessionMemoryRead(SessionMemoryWrite):
    session_id: UUID
    user_id: UUID
    updated_at: datetime
    expires_in_seconds: int


class PolicyVersionCreate(BaseSchema):
    policy_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    content: dict[str, Any]
    valid_from: datetime
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "PolicyVersionCreate":
        if self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        hard_paths = _matching_paths(self.content, markers=_HARD_MEMORY_KEYS, contains=False)
        if hard_paths:
            raise ValueError(
                "policy cannot override approved protocol hard semantics: " + ", ".join(hard_paths)
            )
        _reject_sensitive_keys(self.content)
        return self


class PolicyVersionRead(BaseSchema):
    id: UUID
    policy_key: str
    version: int
    content: dict[str, Any] = Field(validation_alias="content_json")
    content_hash: str
    valid_from: datetime
    valid_to: datetime | None = None
    status: str
    created_at: datetime


class FeedbackAccepted(BaseSchema):
    feedback_id: UUID
    project_memory_id: UUID | None = None

"""Authorized venue metrics and deterministic constraint ledger schemas."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema
from app.schemas.literature_research.protocol import (
    ConstraintOperator,
    ConstraintSeverity,
    DocumentType,
)


class MetricSnapshotStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class MetricSnapshotCreate(BaseSchema):
    source_name: str = Field(min_length=2, max_length=200)
    source_version: str = Field(min_length=1, max_length=100)
    metric_names: list[str] = Field(min_length=1, max_length=50)
    effective_from: date
    effective_to: date | None = None
    license_reference: str = Field(min_length=3, max_length=1000)
    authorized_scope: str = Field(min_length=3, max_length=1000)
    license_attested: bool
    imported_at: datetime
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_key: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_authorization_and_window(self) -> "MetricSnapshotCreate":
        if not self.license_attested:
            raise ValueError("metric snapshot import requires an explicit license attestation")
        if self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("effective_from must not be after effective_to")
        return self


class MetricSnapshotRead(BaseSchema):
    id: UUID
    source_name: str
    source_version: str
    metric_names: list[str] = Field(validation_alias="metric_names_json")
    effective_from: date
    effective_to: date | None = None
    license_reference: str
    authorized_scope: str
    license_attested: bool
    status: MetricSnapshotStatus
    imported_by: UUID
    imported_at: datetime
    payload_sha256: str
    object_key: str
    created_at: datetime


class MetricFactInput(BaseSchema):
    venue_name: str = Field(min_length=1, max_length=1000)
    venue_type: str = Field(min_length=1, max_length=32)
    issn_l: str | None = Field(default=None, max_length=32)
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: Any
    metric_year: int = Field(ge=1900, le=2200)
    source_row: int = Field(ge=2)


class MetricObservation(BaseSchema):
    fact_id: UUID
    metric_name: str
    value: Any
    metric_year: int = Field(ge=1900, le=2200)
    venue_id: UUID | None = None
    venue_name: str
    snapshot_id: UUID
    source_name: str
    source_version: str
    effective_from: date
    effective_to: date | None = None
    authorized: bool
    evidence_reference: str

    def is_effective_on(self, as_of_date: date) -> bool:
        return self.effective_from <= as_of_date and (
            self.effective_to is None or as_of_date <= self.effective_to
        )


class ConstraintDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ConstraintReasonCode(StrEnum):
    COMPARISON_PASSED = "COMPARISON_PASSED"
    COMPARISON_FAILED = "COMPARISON_FAILED"
    VALUE_MISSING = "VALUE_MISSING"
    METRIC_NOT_AUTHORIZED = "METRIC_NOT_AUTHORIZED"
    METRIC_SNAPSHOT_OUT_OF_WINDOW = "METRIC_SNAPSHOT_OUT_OF_WINDOW"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConstraintEvaluation(BaseSchema):
    constraint_id: str
    field: str
    operator: ConstraintOperator
    expected_value: Any
    observed_value: Any = None
    severity: ConstraintSeverity
    decision: ConstraintDecision
    reason_code: ConstraintReasonCode
    evidence_reference: str | None = None
    metric_snapshot_id: UUID | None = None
    metric_fact_id: UUID | None = None
    metric_year: int | None = Field(default=None, ge=1900, le=2200)
    evaluated_at: datetime


class WorkEvaluationContext(BaseSchema):
    work_id: UUID
    version_id: UUID | None = None
    as_of_date: date
    document_type: DocumentType
    work_fields: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, MetricObservation] = Field(default_factory=dict)


class WorkConstraintLedger(BaseSchema):
    work_id: UUID
    version_id: UUID | None = None
    protocol_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    eligible: bool
    hard_pass_count: int = Field(ge=0)
    hard_fail_count: int = Field(ge=0)
    hard_unknown_count: int = Field(ge=0)
    evaluations: list[ConstraintEvaluation]


class QualityEvaluationOutcome(BaseSchema):
    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    hard_fail_count: int = Field(ge=0)
    hard_unknown_count: int = Field(ge=0)
    reason_counts: dict[ConstraintReasonCode, int] = Field(default_factory=dict)

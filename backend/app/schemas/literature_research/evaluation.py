"""Versioned human gold sets and reproducible offline evaluation reports."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema


class GoldPaperCase(BaseSchema):
    case_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=2, max_length=4000)
    doi: str | None = None
    relevant: bool
    relevance_grade: int | None = Field(default=None, ge=0, le=3)
    expected_date: date | None = None
    expected_venue: str | None = None
    allowed_quote_sha256: list[str] = Field(default_factory=list)
    expected_numeric_values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_relevance_grade(self) -> "GoldPaperCase":
        if self.relevance_grade is None:
            self.relevance_grade = 3 if self.relevant else 0
        if self.relevant != (self.relevance_grade > 0):
            raise ValueError("relevant must agree with relevance_grade > 0")
        return self


class GoldSourceObservation(BaseSchema):
    source: str
    source_id: str
    expected_cluster_id: str


class GoldDatasetStatus(StrEnum):
    DRAFT = "DRAFT"
    EXTERNAL_BENCHMARK = "EXTERNAL_BENCHMARK"
    ADJUDICATED = "ADJUDICATED"


class GoldDatasetProvenance(BaseSchema):
    source_name: str = Field(min_length=2, max_length=255)
    source_url: str = Field(min_length=8, max_length=2000)
    license: str = Field(min_length=2, max_length=255)
    annotator_count: int = Field(ge=1)
    judgment_method: str = Field(min_length=3, max_length=2000)
    completed_at: datetime
    domain_coverage: list[str] = Field(min_length=1)
    language_coverage: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class EvaluationDatasetCreate(BaseSchema):
    project_id: UUID
    name: str = Field(min_length=3, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=4000)
    cases: list[GoldPaperCase] = Field(min_length=1)
    observations: list[GoldSourceObservation] = Field(default_factory=list)
    status: GoldDatasetStatus = GoldDatasetStatus.DRAFT
    provenance: GoldDatasetProvenance | None = None

    @model_validator(mode="after")
    def unique_cases(self) -> "EvaluationDatasetCreate":
        ids = [item.case_id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("gold case_id values must be unique")
        if self.status != GoldDatasetStatus.DRAFT and self.provenance is None:
            raise ValueError("evaluation-ready gold datasets require annotation provenance")
        if (
            self.status == GoldDatasetStatus.ADJUDICATED
            and self.provenance is not None
            and self.provenance.annotator_count < 2
        ):
            raise ValueError("adjudicated gold datasets require at least two annotators")
        return self


class EvaluationDatasetRead(BaseSchema):
    id: UUID
    project_id: UUID
    name: str
    version: str
    description: str
    payload_hash: str
    case_count: int
    status: GoldDatasetStatus
    provenance: GoldDatasetProvenance | None = Field(
        default=None, validation_alias="provenance_json"
    )
    created_by: UUID
    created_at: datetime


class MetricStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


class EvaluationMetric(BaseSchema):
    value: float | None = Field(default=None, ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    sample_size: int = Field(ge=0)
    status: MetricStatus


class EvaluationReport(BaseSchema):
    id: UUID | None = None
    dataset_id: UUID
    run_id: UUID
    dataset_hash: str
    metrics: dict[str, EvaluationMetric]
    passed: bool
    failures: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime

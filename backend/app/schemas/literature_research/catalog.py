"""Candidate and paper-detail read models for the research workbench."""

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.literature_research.analysis import AuditedPaperAnalysis, FigureArtifact
from app.schemas.literature_research.evidence import EvidenceLocator, RelevanceDecision


class ConstraintDecisionRead(BaseSchema):
    constraint_id: str
    field: str
    operator: str
    expected_value: Any | None
    observed_value: Any | None
    severity: str
    decision: str
    reason_code: str
    evidence_reference: str | None = None


class CandidateRead(BaseSchema):
    work_id: UUID
    version_id: UUID | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    document_type: str
    venue: str | None = None
    effective_publication_date: date | None = None
    doi: str | None = None
    source_url: str | None = None
    duplicate_decisions: list[dict[str, Any]] = Field(default_factory=list)
    hard_eligible: bool | None = None
    hard_fail_count: int = 0
    hard_unknown_count: int = 0
    relevance_decision: RelevanceDecision | None = None
    relevance_score: float | None = None
    relevance_reasons: list[str] = Field(default_factory=list)
    relevance_facet_judgement: dict[str, Any] | None = None
    constraints: list[ConstraintDecisionRead] = Field(default_factory=list)


class CandidatePage(BaseSchema):
    items: list[CandidateRead]
    total: int
    skip: int
    limit: int


class WorkVersionRead(BaseSchema):
    id: UUID
    source: str
    source_id: str
    version_type: str
    doi: str | None = None
    arxiv_id: str | None = None
    effective_publication_date: date | None = None
    canonical_url: str | None = None
    raw_sha256: str


class PaperDetailRead(BaseSchema):
    candidate: CandidateRead
    versions: list[WorkVersionRead]
    analysis: AuditedPaperAnalysis | None = None
    evidence: list[EvidenceLocator] = Field(default_factory=list)
    figures: list[FigureArtifact] = Field(default_factory=list)
    analysis_attempt: int | None = None


class ReanalysisRequest(BaseSchema):
    client_request_id: str = Field(min_length=8, max_length=128)


class ReanalysisAccepted(BaseSchema):
    task_execution_id: UUID
    run_id: UUID
    work_id: UUID
    status: str
    created: bool

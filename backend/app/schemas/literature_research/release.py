"""Canonical report, artifact, manifest, and deterministic release gate schemas."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.literature_research.analysis import AuditedPaperAnalysis, SynthesisOutput


class ReportPaper(BaseSchema):
    work_id: UUID
    version_id: UUID
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    source_url: str | None = None
    document_type: str
    venue: str | None = None
    relevance_score: float = Field(ge=0, le=1)
    hard_constraints_passed: bool
    analysis: AuditedPaperAnalysis


class CanonicalResearchReport(BaseSchema):
    run_id: UUID
    project_id: UUID
    protocol_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    title: str
    target_count: int = Field(ge=1)
    strict_count: int = Field(ge=0)
    shortfall_disclosed: bool = False
    synthesis: SynthesisOutput
    papers: list[ReportPaper]


class CatalogPaper(BaseSchema):
    """A strict metadata-only selection, intentionally without full-text analysis."""

    work_id: UUID
    version_id: UUID
    rank: int = Field(ge=1)
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    source_url: str | None = None
    document_type: str
    venue: str | None = None
    relevance_score: float = Field(ge=0, le=1)


class CatalogResearchReport(BaseSchema):
    """Canonical input for the no-PDF/no-LLM catalog exports."""

    run_id: UUID
    project_id: UUID
    protocol_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    title: str
    target_count: int = Field(ge=1)
    strict_count: int = Field(ge=0)
    shortfall_disclosed: bool = False
    papers: list[CatalogPaper]


class ArtifactFormat(StrEnum):
    MARKDOWN = "markdown"
    OPML = "opml"
    BIBTEX = "bibtex"
    JSONL = "jsonl"
    CSV = "csv"
    EXCLUSIONS_CSV = "exclusions_csv"
    VENUE_METRICS_CSV = "venue_metrics_csv"
    MANIFEST = "manifest"


class ExclusionAuditRow(BaseSchema):
    work_id: UUID
    version_id: UUID | None = None
    title: str
    document_type: str
    doi: str | None = None
    venue: str | None = None
    hard_eligible: bool | None = None
    hard_fail_count: int = Field(default=0, ge=0)
    hard_unknown_count: int = Field(default=0, ge=0)
    relevance_decision: str | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(min_length=1)


class MetricSnapshotAuditRow(BaseSchema):
    snapshot_id: UUID
    metric_fact_id: UUID
    work_id: UUID
    title: str
    venue: str | None = None
    constraint_id: str
    field: str
    observed_value: Any | None = None
    metric_year: int = Field(ge=1900, le=2200)
    decision: str
    reason_code: str
    source_name: str
    source_version: str
    effective_from: date
    effective_to: date | None = None
    license_reference: str
    authorized_scope: str
    license_attested: bool
    snapshot_status: str
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_reference: str | None = None


class RenderedArtifact(BaseSchema):
    format: ArtifactFormat
    filename: str
    content_type: str
    data: bytes = Field(exclude=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactRead(BaseSchema):
    id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    format: ArtifactFormat
    filename: str
    content_type: str
    object_key: str
    sha256: str
    size_bytes: int
    created_at: datetime


class ArtifactRegenerationRequest(BaseSchema):
    client_request_id: str = Field(min_length=8, max_length=128)


class ArtifactRegenerationAccepted(BaseSchema):
    task_execution_id: UUID
    run_id: UUID
    status: str
    created: bool


class RunManifest(BaseSchema):
    schema_version: str = "1.0"
    run_id: UUID
    generation: int = Field(default=1, ge=1)
    project_id: UUID
    protocol_hash: str
    template_commit: str
    model_versions: dict[str, str] = Field(default_factory=dict)
    llm_usage: dict[str, Any] = Field(default_factory=dict)
    source_snapshot_hashes: list[
        Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    ] = Field(default_factory=list)
    metric_snapshot_ids: list[UUID] = Field(default_factory=list)
    artifact_hashes: dict[ArtifactFormat, str]
    target_count: int
    strict_count: int
    shortfall_disclosed: bool


class ReleaseBlocker(StrEnum):
    PROTOCOL_CHANGED = "PROTOCOL_CHANGED"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    UNRESOLVED_DUPLICATES = "UNRESOLVED_DUPLICATES"
    RELEVANCE_BELOW_THRESHOLD = "RELEVANCE_BELOW_THRESHOLD"
    EVIDENCE_COVERAGE_LOW = "EVIDENCE_COVERAGE_LOW"
    CONTRADICTED_CLAIMS = "CONTRADICTED_CLAIMS"
    UNSUPPORTED_CLAIMS = "UNSUPPORTED_CLAIMS"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    DOCUMENT_SAFETY_FAILED = "DOCUMENT_SAFETY_FAILED"
    FIGURE_AUDIT_INCOMPLETE = "FIGURE_AUDIT_INCOMPLETE"
    SHORTFALL_NOT_DISCLOSED = "SHORTFALL_NOT_DISCLOSED"


class ReleaseSnapshot(BaseSchema):
    protocol_hash: str
    approved_protocol_hash: str
    constraint_violation_count: int = Field(ge=0)
    duplicate_cluster_conflicts: int = Field(ge=0)
    min_relevance_score: float = Field(ge=0, le=1)
    min_evidence_coverage: float = Field(ge=0, le=1)
    contradicted_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    artifact_validation_errors: list[str] = Field(default_factory=list)
    document_safety_failure_count: int = Field(default=0, ge=0)
    figure_audit_failure_count: int = Field(default=0, ge=0)
    target_count: int = Field(ge=1)
    strict_count: int = Field(ge=0)
    shortfall_disclosed: bool


class ReleaseDecision(BaseSchema):
    allowed: bool
    partial: bool
    blockers: list[ReleaseBlocker]

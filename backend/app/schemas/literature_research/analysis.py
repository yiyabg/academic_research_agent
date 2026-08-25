"""Strict outputs for the six bounded semantic research experts."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema
from app.schemas.literature_research.protocol import TopicFacet


class UnknownAwareStatus(StrEnum):
    REPORTED = "REPORTED"
    NOT_REPORTED = "NOT_REPORTED"
    UNKNOWN = "UNKNOWN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ClaimKind(StrEnum):
    AUTHOR_STATED = "AUTHOR_STATED"
    EXPERIMENT_OBSERVED = "EXPERIMENT_OBSERVED"
    REVIEWER_INFERRED = "REVIEWER_INFERRED"


class EvidenceGroundedClaim(BaseSchema):
    claim_id: str = Field(pattern=r"^C_[A-Z0-9_-]{4,64}$")
    text: str = Field(min_length=3, max_length=4000)
    kind: ClaimKind
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    confidence: float = Field(ge=0, le=1)


class ProtocolDraftAdvice(BaseSchema):
    topic_definition: str = Field(min_length=3, max_length=2000)
    research_questions: list[str] = Field(min_length=1, max_length=30)
    must_have_facets: list[TopicFacet] = Field(min_length=1, max_length=20)
    ambiguities: list[str] = Field(default_factory=list, max_length=30)
    approval_requested: bool = False

    @model_validator(mode="after")
    def cannot_approve(self) -> "ProtocolDraftAdvice":
        if self.approval_requested:
            raise ValueError("ProtocolAgent may draft but may not approve protocols")
        return self


class FacetStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    UNCERTAIN = "UNCERTAIN"


class Centrality(StrEnum):
    CENTRAL = "CENTRAL"
    SUPPORTING = "SUPPORTING"
    INCIDENTAL = "INCIDENTAL"
    UNRELATED = "UNRELATED"


class FacetJudgementItem(BaseSchema):
    facet_id: str
    status: FacetStatus
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=3, max_length=2000)


class FacetJudgement(BaseSchema):
    work_id: UUID
    facets: list[FacetJudgementItem]
    centrality: Centrality
    score: float = Field(ge=0, le=1)
    exclusion_triggered: bool
    evidence_ids: list[str] = Field(default_factory=list)


class FacetJudgementBatch(BaseSchema):
    judgements: list[FacetJudgement] = Field(min_length=1, max_length=20)


class AnalysisSection(BaseSchema):
    work_id: UUID
    section_id: str
    status: UnknownAwareStatus
    summary: str = Field(max_length=12000)
    claims: list[EvidenceGroundedClaim] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def not_reported_has_no_claims(self) -> "AnalysisSection":
        if self.status != UnknownAwareStatus.REPORTED and self.claims:
            raise ValueError("UNKNOWN/NOT_REPORTED sections cannot contain factual claims")
        return self


class NumericSource(StrEnum):
    TABLE_EXACT = "table_exact"
    TEXT_EXACT = "text_exact"
    PLOT_DIGITIZED = "plot_digitized"
    NOT_EXTRACTED = "not_extracted"


class FigureArtifact(BaseSchema):
    """Auditable figure/table input extracted from a licensed document."""

    figure_id: str = Field(pattern=r"^F_[A-F0-9]{16}$")
    label: str = Field(min_length=2, max_length=100)
    caption: str = Field(min_length=2, max_length=4000)
    page_number: int | None = Field(default=None, ge=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    image_object_key: str | None = None
    image_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    bbox: tuple[float, float, float, float] | None = None
    artifact_kind: str = "figure"
    extraction_status: str = "CAPTION_ONLY"
    table_cells: list[list[str]] = Field(default_factory=list)
    exact_numeric_values: list[str] = Field(default_factory=list)
    license_scope: str = "project"

    @model_validator(mode="after")
    def verified_artifact_has_a_hash_bound_crop(self) -> "FigureArtifact":
        if self.extraction_status == "VERIFIED" and (
            self.bbox is None or not self.image_object_key or not self.image_sha256
        ):
            raise ValueError("verified figure artifacts require bbox and hash-bound crop")
        return self


class PlotDigitization(BaseSchema):
    x_pixel_range: tuple[float, float]
    x_value_range: tuple[float, float]
    y_pixel_range: tuple[float, float]
    y_value_range: tuple[float, float]
    estimated_error: float = Field(ge=0)


class FigureInterpretation(BaseSchema):
    work_id: UUID
    figure_id: str
    figure_kind: str
    caption_summary: str
    observations: list[EvidenceGroundedClaim] = Field(default_factory=list)
    numeric_source: NumericSource
    extracted_values: list[str] = Field(default_factory=list)
    digitization: PlotDigitization | None = None
    uncertainty: str | None = None

    @model_validator(mode="after")
    def no_values_without_source(self) -> "FigureInterpretation":
        if self.numeric_source == NumericSource.NOT_EXTRACTED and self.extracted_values:
            raise ValueError("not_extracted figures cannot report numeric values")
        if self.numeric_source == NumericSource.PLOT_DIGITIZED and self.digitization is None:
            raise ValueError("plot_digitized values require pixel-coordinate calibration")
        if self.numeric_source != NumericSource.PLOT_DIGITIZED and self.digitization is not None:
            raise ValueError("digitization metadata is only valid for plot_digitized values")
        return self


class AuditDecision(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class ClaimAudit(BaseSchema):
    claim_id: str
    decision: AuditDecision
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class AuditReport(BaseSchema):
    work_id: UUID
    claims: list[ClaimAudit]
    evidence_coverage: float = Field(ge=0, le=1)
    contradicted_count: int = Field(ge=0)
    unsupported_count: int = Field(ge=0)
    requires_human_review: bool = False

    @model_validator(mode="after")
    def counts_match_claims(self) -> "AuditReport":
        contradicted = sum(item.decision == AuditDecision.CONTRADICTED for item in self.claims)
        unsupported = sum(item.decision == AuditDecision.UNSUPPORTED for item in self.claims)
        if self.contradicted_count != contradicted or self.unsupported_count != unsupported:
            raise ValueError("audit summary counts must match claim decisions")
        return self


class SynthesisTheme(BaseSchema):
    theme_id: str
    title: str
    work_ids: list[UUID] = Field(min_length=1)
    claims: list[EvidenceGroundedClaim] = Field(default_factory=list)


class SynthesisOutput(BaseSchema):
    overview: str
    themes: list[SynthesisTheme]
    method_matrix: list[dict[str, str]] = Field(default_factory=list)
    experiment_matrix: list[dict[str, str]] = Field(default_factory=list)
    research_gaps: list[EvidenceGroundedClaim] = Field(default_factory=list)
    included_work_ids: list[UUID]


class PaperAnalysisTask(BaseSchema):
    work_id: UUID
    metadata: dict[str, object]
    evidence: list[dict[str, object]] = Field(min_length=1)
    section_ids: list[str] = Field(min_length=1)
    figures: list[FigureArtifact] = Field(default_factory=list)


class AuditedPaperAnalysis(BaseSchema):
    work_id: UUID
    sections: list[AnalysisSection]
    figures: list[FigureInterpretation]
    audit: AuditReport

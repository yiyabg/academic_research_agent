"""Research protocol schemas and invariants.

The approved protocol is the immutable executable contract for a run. Agents
may help draft topic facets, but these models and deterministic validators own
all dates, enums, quality scope, and hard-constraint semantics.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.schemas.base import BaseSchema


class AmbiguityStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_CLARIFICATION = "needs_clarification"


class ProtocolStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class DocumentType(StrEnum):
    UNKNOWN = "unknown"
    JOURNAL_ARTICLE = "journal_article"
    CONFERENCE_PAPER = "conference_paper"
    PREPRINT = "preprint"
    REVIEW = "review"
    SYSTEMATIC_REVIEW = "systematic_review"
    BOOK_CHAPTER = "book_chapter"
    STANDARD = "standard"
    THESIS = "thesis"


class ConstraintOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    EXISTS = "exists"


class ConstraintSeverity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class MissingValuePolicy(StrEnum):
    FAIL = "fail"
    REVIEW = "review"
    ALLOW = "allow"


class ShortfallAction(StrEnum):
    RETURN_STRICT_ONLY = "return_strict_only"
    ASK_USER_BEFORE_RELAXATION = "ask_user_before_relaxation"
    STRICT_PLUS_SEPARATE_CANDIDATES = "strict_plus_separate_candidates"


class OutputFormat(StrEnum):
    MARKDOWN = "markdown"
    OPML = "opml"
    BIBTEX = "bibtex"
    JSONL = "jsonl"
    CSV = "csv"
    MANIFEST = "manifest"


class TopicFacet(BaseSchema):
    facet_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=3, max_length=1000)
    minimum_score: float = Field(default=0.65, ge=0, le=1)
    weight: float = Field(default=1.0, gt=0, le=10)


class ExclusionFacet(BaseSchema):
    facet_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = Field(min_length=3, max_length=1000)


class SynonymGroup(BaseSchema):
    concept: str = Field(min_length=1, max_length=200)
    terms: list[str] = Field(min_length=1, max_length=50)


class TopicModel(BaseSchema):
    must_have_facets: list[TopicFacet] = Field(min_length=1, max_length=20)
    should_have_facets: list[TopicFacet] = Field(default_factory=list, max_length=20)
    exclude_facets: list[ExclusionFacet] = Field(default_factory=list, max_length=20)
    synonym_groups: list[SynonymGroup] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def facet_ids_are_unique(self) -> "TopicModel":
        ids = [f.facet_id for f in self.must_have_facets + self.should_have_facets]
        ids.extend(f.facet_id for f in self.exclude_facets)
        if len(ids) != len(set(ids)):
            raise ValueError("facet_id values must be unique")
        return self


class TimeScope(BaseSchema):
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    timezone: str = "Asia/Shanghai"
    date_field_priority: list[
        Literal[
            "published_online",
            "issued",
            "published_print",
            "preprint_first_posted",
        ]
    ] = Field(
        default_factory=lambda: [
            "published_online",
            "issued",
            "published_print",
            "preprint_first_posted",
        ],
        min_length=1,
    )  # ty: ignore[invalid-assignment]
    start_inclusive: bool = True
    end_inclusive: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> "TimeScope":
        if self.date_from > self.date_to:
            raise ValueError("time_scope.from must not be after time_scope.to")
        if len(self.date_field_priority) != len(set(self.date_field_priority)):
            raise ValueError("date_field_priority values must be unique")
        return self


class DocumentScope(BaseSchema):
    allowed_types: list[DocumentType] = Field(min_length=1)
    allowed_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    version_policy: Literal[
        "prefer_version_of_record", "keep_distinct_versions", "latest_available"
    ] = "prefer_version_of_record"

    @model_validator(mode="after")
    def unknown_is_not_an_eligible_protocol_type(self) -> "DocumentScope":
        if DocumentType.UNKNOWN in self.allowed_types:
            raise ValueError("unknown cannot be an allowed document type")
        return self


class SourcePolicy(BaseSchema):
    required_sources: list[str] = Field(default_factory=list)
    optional_sources: list[str] = Field(default_factory=lambda: ["crossref", "openalex", "arxiv"])
    minimum_source_families: int = Field(default=2, ge=1, le=20)
    publisher_verification_required: bool = True


class ProtocolConstraint(BaseSchema):
    constraint_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    field: str = Field(min_length=1, max_length=200)
    operator: ConstraintOperator
    value: Any = None
    severity: ConstraintSeverity = ConstraintSeverity.HARD
    verification_source: str = Field(min_length=1, max_length=200)
    missing_value_policy: MissingValuePolicy = MissingValuePolicy.FAIL
    applies_to: list[DocumentType] | None = None

    @model_validator(mode="after")
    def hard_constraints_fail_closed(self) -> "ProtocolConstraint":
        if (
            self.severity == ConstraintSeverity.HARD
            and self.missing_value_policy == MissingValuePolicy.ALLOW
        ):
            raise ValueError("hard constraints cannot allow missing values")
        if self.operator == ConstraintOperator.EXISTS and self.value is not None:
            raise ValueError("exists constraints must use a null value")
        return self


class QuantityPolicy(BaseSchema):
    target_count: int = Field(default=20, ge=1, le=200)
    quality_floor_locked: Literal[True] = True
    shortfall_action: ShortfallAction = ShortfallAction.ASK_USER_BEFORE_RELAXATION


class AnalysisTemplateSection(BaseSchema):
    section_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    required: bool = True
    evidence_required: bool = True
    instructions: str = Field(default="", max_length=2000)


def default_analysis_template() -> list[AnalysisTemplateSection]:
    return [
        AnalysisTemplateSection(section_id="background", title="研究背景及意义"),
        AnalysisTemplateSection(section_id="innovations", title="创新点"),
        AnalysisTemplateSection(section_id="process", title="研究内容与详细流程"),
        AnalysisTemplateSection(section_id="architecture", title="系统架构"),
        AnalysisTemplateSection(section_id="experiments", title="实验设计与结果"),
        AnalysisTemplateSection(section_id="conclusion", title="论文结论"),
        AnalysisTemplateSection(section_id="limitations", title="尚存不足"),
    ]


class OutputPolicy(BaseSchema):
    formats: list[OutputFormat] = Field(
        default_factory=lambda: [
            OutputFormat.MARKDOWN,
            OutputFormat.OPML,
            OutputFormat.BIBTEX,
            OutputFormat.JSONL,
            OutputFormat.CSV,
            OutputFormat.MANIFEST,
        ],
        min_length=1,
    )
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    citation_style: Literal["IEEE", "APA"] = "IEEE"
    evidence_required: Literal[True] = True
    include_figures: bool = True
    include_tables: bool = True


class LLMBudgetPolicy(BaseSchema):
    """User-approved hard ceiling for one bounded research LLM operation."""

    max_requests: int = Field(default=64, ge=1, le=256)
    max_input_tokens: int = Field(default=1_500_000, ge=1_000, le=10_000_000)
    max_output_tokens: int = Field(default=100_000, ge=1_000, le=1_000_000)
    max_total_tokens: int = Field(default=1_600_000, ge=2_000, le=11_000_000)
    max_cost_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=6)

    @model_validator(mode="after")
    def total_covers_input_and_output_limits(self) -> "LLMBudgetPolicy":
        if self.max_total_tokens < self.max_input_tokens + self.max_output_tokens:
            raise ValueError("max_total_tokens must cover input plus output token ceilings")
        return self


class ProtocolIssue(BaseSchema):
    code: str
    message: str
    field: str | None = None
    blocking: bool = True


class ProtocolAdviceMemoryProvenance(BaseSchema):
    """Identifiers proving which authoritative memories informed a draft."""

    retrieval_mode: Literal["semantic_plus_recent", "postgres_fallback", "none"]
    project_memory_ids: list[UUID] = Field(default_factory=list, max_length=10)
    profile_id: UUID | None = None
    profile_version: int | None = Field(default=None, ge=1)
    policy_versions: dict[str, int] = Field(default_factory=dict)
    policy_hashes: dict[str, str] = Field(default_factory=dict)
    approved_protocol_hash: str | None = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$"
    )
    ignored_memory_keys: list[str] = Field(default_factory=list)
    retrieval_error_type: str | None = Field(default=None, max_length=200)


class ProtocolAdviceProvenance(BaseSchema):
    """Immutable provenance for an explicitly requested LLM drafting operation."""

    provider: Literal["openai", "deepseek", "openai_compatible"]
    model_identifier: str = Field(min_length=3, max_length=500)
    prompt_version: str = Field(min_length=1, max_length=100)
    schema_version: Literal["ProtocolDraftAdvice@1"] = "ProtocolDraftAdvice@1"
    llm_usage: dict[str, Any]
    memory_context: ProtocolAdviceMemoryProvenance | None = None


class ResearchProtocol(BaseSchema):
    schema_version: Literal["3.0"] = "3.0"
    protocol_id: UUID = Field(default_factory=uuid4)
    topic: str = Field(min_length=3, max_length=500)
    topic_definition: str = Field(default="", max_length=2000)
    research_questions: list[str] = Field(min_length=1, max_length=30)
    topic_model: TopicModel
    time_scope: TimeScope
    document_scope: DocumentScope
    source_policy: SourcePolicy
    constraints: list[ProtocolConstraint] = Field(min_length=1, max_length=100)
    quantity_policy: QuantityPolicy
    analysis_template: list[AnalysisTemplateSection] = Field(min_length=1, max_length=30)
    output_policy: OutputPolicy
    llm_budget: LLMBudgetPolicy = Field(default_factory=LLMBudgetPolicy)
    ambiguity_status: AmbiguityStatus
    issues: list[ProtocolIssue] = Field(default_factory=list)
    draft_advice_provenance: ProtocolAdviceProvenance | None = None

    @model_validator(mode="after")
    def validate_protocol_invariants(self) -> "ResearchProtocol":
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint_id values must be unique")
        section_ids = [item.section_id for item in self.analysis_template]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("analysis section_id values must be unique")
        blocking = any(issue.blocking for issue in self.issues)
        if blocking and self.ambiguity_status != AmbiguityStatus.NEEDS_CLARIFICATION:
            raise ValueError("blocking issues require needs_clarification status")
        return self


class ProtocolCompileRequest(BaseSchema):
    topic: str = Field(min_length=3, max_length=500)
    topic_definition: str = Field(default="", max_length=2000)
    research_questions: list[str] = Field(default_factory=list, max_length=30)
    as_of_date: date = Field(default_factory=date.today)
    rolling_months: int = Field(default=3, ge=1, le=120)
    date_from: date | None = None
    date_to: date | None = None
    timezone: str = "Asia/Shanghai"
    allowed_types: list[DocumentType] = Field(
        default_factory=lambda: [
            DocumentType.JOURNAL_ARTICLE,
            DocumentType.CONFERENCE_PAPER,
        ],
        min_length=1,
    )
    allowed_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    required_sources: list[str] = Field(default_factory=list)
    optional_sources: list[str] = Field(default_factory=lambda: ["crossref", "openalex", "arxiv"])
    minimum_source_families: int = Field(default=2, ge=1, le=20)
    publisher_verification_required: bool = True
    constraints: list[ProtocolConstraint] = Field(default_factory=list, max_length=100)
    target_count: int = Field(default=20, ge=1, le=200)
    shortfall_action: ShortfallAction = ShortfallAction.ASK_USER_BEFORE_RELAXATION
    must_have_facets: list[TopicFacet] = Field(default_factory=list, max_length=20)
    should_have_facets: list[TopicFacet] = Field(default_factory=list, max_length=20)
    exclude_facets: list[ExclusionFacet] = Field(default_factory=list, max_length=20)
    synonym_groups: list[SynonymGroup] = Field(default_factory=list, max_length=30)
    analysis_template: list[AnalysisTemplateSection] = Field(
        default_factory=default_analysis_template,
        min_length=1,
    )
    output_policy: OutputPolicy = Field(default_factory=OutputPolicy)
    llm_budget: LLMBudgetPolicy = Field(default_factory=LLMBudgetPolicy)

    @model_validator(mode="after")
    def explicit_dates_are_complete(self) -> "ProtocolCompileRequest":
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("date_from and date_to must be provided together")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        if DocumentType.UNKNOWN in self.allowed_types:
            raise ValueError("unknown cannot be an allowed document type")
        return self


class ProtocolCompileResponse(BaseSchema):
    protocol: ResearchProtocol
    protocol_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    executable: bool


class ProtocolApproveRequest(BaseSchema):
    protocol_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class ResearchProtocolVersionRead(BaseSchema):
    id: UUID
    project_id: UUID
    version: int
    protocol: ResearchProtocol = Field(validation_alias="protocol_json")
    protocol_hash: str
    status: ProtocolStatus
    approved_at: datetime | None = None
    approved_by: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None

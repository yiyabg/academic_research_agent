"""Relevance, lawful full text, parsed blocks, and evidence locator schemas."""

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import Field, HttpUrl, model_validator

from app.schemas.base import BaseSchema


class RelevanceDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"


class RelevanceScore(BaseSchema):
    work_id: UUID
    lexical_score: float = Field(ge=0, le=1)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    cross_encoder_score: float | None = Field(default=None, ge=0, le=1)
    facet_scores: dict[str, float] = Field(default_factory=dict)
    decision: RelevanceDecision
    model_versions: dict[str, str] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    facet_judgement: dict[str, Any] | None = None


class FullTextSource(StrEnum):
    PUBLISHER = "publisher"
    UNPAYWALL = "unpaywall"
    ARXIV = "arxiv"
    PUBMED_CENTRAL = "pubmed_central"
    USER_UPLOAD = "user_upload"


class LicenseDecision(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"


class FullTextCandidate(BaseSchema):
    version_id: UUID
    source: FullTextSource
    url: HttpUrl
    license_decision: LicenseDecision
    license_reference: str | None = None
    content_type: str | None = None
    is_open_access: bool = False

    @model_validator(mode="after")
    def allowed_requires_evidence(self) -> "FullTextCandidate":
        if self.license_decision == LicenseDecision.ALLOWED and not self.license_reference:
            raise ValueError("allowed full text requires a license reference")
        return self


class FullTextAcquisitionDecision(BaseSchema):
    version_id: UUID
    allowed: bool
    selected: FullTextCandidate | None = None
    reason_code: str
    rejected: list[FullTextCandidate] = Field(default_factory=list)


class AcquiredFullText(BaseSchema):
    version_id: UUID
    source: FullTextSource
    url: HttpUrl
    license_reference: str
    content_type: str
    size_bytes: int = Field(gt=0)
    object_key: str
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolved_ips: list[str] = Field(default_factory=list)
    redirect_chain: list[str] = Field(default_factory=list)
    malware_scan_status: str = "NOT_SCANNED"
    malware_scan_engine: str | None = None
    malware_signature: str | None = None


class ParsedBlock(BaseSchema):
    block_id: str = Field(min_length=1, max_length=100)
    page_number: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bbox: tuple[float, float, float, float] | None = None
    extraction_method: str = "native"

    @model_validator(mode="after")
    def validate_span(self) -> "ParsedBlock":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be after char_start")
        return self


class ParsingQuality(BaseSchema):
    status: str
    page_count: int = Field(ge=1)
    parsed_page_count: int = Field(ge=0)
    text_coverage: float = Field(ge=0, le=1)
    page_count_match: bool
    section_detection_f1_estimate: float = Field(ge=0, le=1)
    table_count: int = Field(ge=0)
    figure_count: int = Field(ge=0)
    caption_count: int = Field(ge=0)
    caption_link_rate: float = Field(ge=0, le=1)
    ocr_page_count: int = Field(ge=0)
    ocr_page_ratio: float = Field(ge=0, le=1)
    total_characters: int = Field(ge=0)
    parser_versions: dict[str, str] = Field(default_factory=dict)
    error_codes: list[str] = Field(default_factory=list)
    blocks_object_key: str | None = None


class ParsedDocument(BaseSchema):
    blocks: list[ParsedBlock]
    quality: ParsingQuality


class EvidenceLocator(BaseSchema):
    evidence_id: str
    work_id: UUID
    version_id: UUID
    block_id: str
    page_number: int | None = None
    section_path: list[str] = Field(default_factory=list, validation_alias="section_path_json")
    quote: str = Field(min_length=1, max_length=4000)
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)
    block_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, validation_alias="bbox_json"
    )
    extraction_method: str = "native"


class AsyncScoreModel(Protocol):
    version: str

    async def score(self, query: str, documents: list[str]) -> list[float]: ...

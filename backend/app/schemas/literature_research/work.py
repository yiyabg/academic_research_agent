"""Normalized scholarly work, version, provenance, and resolution schemas."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, HttpUrl, model_validator

from app.schemas.base import BaseSchema
from app.schemas.literature_research.discovery import ScholarlySourceName
from app.schemas.literature_research.protocol import DocumentType


class WorkVersionType(StrEnum):
    VERSION_OF_RECORD = "version_of_record"
    CONFERENCE_VERSION = "conference_version"
    ACCEPTED_MANUSCRIPT = "accepted_manuscript"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


class VenueType(StrEnum):
    JOURNAL = "journal"
    CONFERENCE = "conference"
    REPOSITORY = "repository"
    OTHER = "other"


class NormalizedAuthor(BaseSchema):
    name: str = Field(min_length=1, max_length=500)
    given_name: str | None = Field(default=None, max_length=255)
    family_name: str | None = Field(default=None, max_length=255)
    orcid: str | None = Field(default=None, max_length=100)
    affiliations: list[str] = Field(default_factory=list)


class NormalizedVenue(BaseSchema):
    name: str = Field(min_length=1, max_length=1000)
    normalized_name: str = Field(min_length=1, max_length=1000)
    venue_type: VenueType
    issn_l: str | None = None
    issns: list[str] = Field(default_factory=list)
    publisher: str | None = None


class WorkIdentifiers(BaseSchema):
    doi: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None


class WorkDates(BaseSchema):
    published_online: date | None = None
    issued: date | None = None
    published_print: date | None = None
    preprint_first_posted: date | None = None
    accepted: date | None = None
    effective_publication_date: date | None = None
    effective_date_field: str | None = None
    effective_date_source: ScholarlySourceName | None = None


class FieldCandidate(BaseSchema):
    value: Any
    source: ScholarlySourceName
    source_id: str
    retrieved_at: datetime
    confidence: float = Field(ge=0, le=1)


class FieldProvenance(BaseSchema):
    field: str
    chosen: Any
    status: str
    candidates: list[FieldCandidate] = Field(min_length=1)
    resolution_rule: str


class NormalizedPaper(BaseSchema):
    source: ScholarlySourceName
    source_id: str
    retrieved_at: datetime
    title: str = Field(min_length=1, max_length=4000)
    title_normalized: str = Field(min_length=1, max_length=4000)
    abstract: str | None = None
    authors: list[NormalizedAuthor] = Field(default_factory=list)
    document_type: DocumentType
    version_type: WorkVersionType
    venue: NormalizedVenue | None = None
    dates: WorkDates
    identifiers: WorkIdentifiers
    canonical_url: HttpUrl | None = None
    open_access_pdf_url: HttpUrl | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    language: str | None = None
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def choose_effective_date(self) -> "NormalizedPaper":
        if self.dates.effective_publication_date is None:
            for field in (
                "published_online",
                "issued",
                "published_print",
                "preprint_first_posted",
            ):
                value = getattr(self.dates, field)
                if value is not None:
                    self.dates.effective_publication_date = value
                    self.dates.effective_date_field = field
                    self.dates.effective_date_source = self.source
                    break
        return self


class DuplicateDecisionType(StrEnum):
    MERGE = "MERGE"
    REVIEW = "REVIEW"
    KEEP_SEPARATE = "KEEP_SEPARATE"


class DuplicateDecision(BaseSchema):
    left_source_id: str
    right_source_id: str
    decision: DuplicateDecisionType
    confidence: float = Field(ge=0, le=1)
    reason: str


class ResolvedWorkCluster(BaseSchema):
    cluster_key: str
    preferred: NormalizedPaper
    versions: list[NormalizedPaper] = Field(min_length=1)
    decisions: list[DuplicateDecision] = Field(default_factory=list)

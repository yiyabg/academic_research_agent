"""Schemas shared by query planning and scholarly source adapters."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import BaseSchema
from app.schemas.literature_research.protocol import DocumentType


class ScholarlySourceName(StrEnum):
    CROSSREF = "crossref"
    OPENALEX = "openalex"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    PUBMED = "pubmed"


class SourceQuery(BaseSchema):
    query_id: str = Field(min_length=1, max_length=80)
    family: str = Field(min_length=1, max_length=80)
    source: ScholarlySourceName
    query_text: str = Field(min_length=2, max_length=1000)
    date_from: date
    date_to: date
    publication_types: list[DocumentType] = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    facet_coverage: list[str] = Field(default_factory=list)
    origin: str = "protocol_compiler"
    expansion_depth: int = Field(default=0, ge=0, le=5)
    result_limit: int = Field(default=100, ge=1, le=100)


class QueryPlan(BaseSchema):
    queries: list[SourceQuery] = Field(min_length=1, max_length=200)
    strategy: Literal["federated_search", "doi_seeded"] = "federated_search"
    candidate_limit: int = Field(default=35, ge=1, le=100)
    stop_after_empty_pages: int = Field(default=2, ge=1, le=10)
    max_pages_per_query: int = Field(default=20, ge=1, le=100)
    saturation_rounds: int = Field(default=2, ge=1, le=10)


class RawSourceRecord(BaseSchema):
    source: ScholarlySourceName
    source_id: str = Field(min_length=1, max_length=1000)
    retrieved_at: datetime
    raw: dict[str, Any]


class SourcePage(BaseSchema):
    source: ScholarlySourceName
    query_id: str
    cursor_in: str | None = None
    cursor_out: str | None = None
    request_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    http_status: int = Field(ge=100, le=599)
    retrieved_at: datetime
    records: list[RawSourceRecord]
    raw_body: bytes = Field(exclude=True)
    response_etag: str | None = None
    response_last_modified: str | None = None


class SourceFailure(BaseSchema):
    source: ScholarlySourceName
    query_id: str
    code: str
    message: str
    retryable: bool
    occurred_at: datetime


class DiscoveryOutcome(BaseSchema):
    query_count: int = Field(ge=0)
    successful_query_count: int = Field(ge=0)
    exhausted_query_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    raw_record_count: int = Field(ge=0)
    unique_record_count: int = Field(ge=0)
    work_count: int = Field(ge=0)
    version_count: int = Field(ge=0)
    source_counts: dict[ScholarlySourceName, int] = Field(default_factory=dict)
    keyword_search_count: int = Field(default=0, ge=0)
    candidate_doi_count: int = Field(default=0, ge=0)
    exact_doi_lookup_count: int = Field(default=0, ge=0)
    exact_doi_match_count: int = Field(default=0, ge=0)
    failures: list[SourceFailure] = Field(default_factory=list)

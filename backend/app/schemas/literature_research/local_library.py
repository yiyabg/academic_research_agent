"""API contracts for the private local Zotero library."""
# ruff: noqa: RUF001 - User-facing Chinese schema descriptions use Chinese punctuation.

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema


class LocalPaperMindmapRequest(BaseSchema):
    query: str = Field(min_length=1, max_length=1000, description="检索关键词/研究课题")
    question: str | None = Field(
        default=None, max_length=2000, description="分析问题（留空则与query相同）"
    )
    limit: int = Field(default=10, ge=1, le=50, description="检索K篇论文")
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    output_format: Literal["markdown", "opml"] = Field(default="markdown")


class LocalLibrarySyncAccepted(BaseSchema):
    sync_run_id: UUID
    status: str


class LocalLibrarySyncRunRead(BaseSchema):
    id: UUID
    status: str
    summary_json: dict[str, object]
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LocalLibraryQuarantineRead(BaseSchema):
    item_kind: str
    relative_path: str | None = None
    citekey: str | None = None
    detail: str


class LocalLibraryStatusRead(BaseSchema):
    configured: bool
    owner_id: UUID | None = None
    status: str
    source_root: str | None = None
    indexed_papers: int = 0
    current_indexed_papers: int = 0
    missing_papers: int = 0
    quarantined_items: int = 0
    # Explicit, non-overlapping status counters.  The legacy fields above are
    # retained for API compatibility but must not be used as a sync total.
    catalogued_papers: int = 0
    searchable_papers: int = 0
    stale_indexed_papers: int = 0
    missing_source_papers: int = 0
    latest_quarantine_items: int = 0
    last_sync_summary: dict[str, object] = Field(default_factory=dict)
    latest_sync: LocalLibrarySyncRunRead | None = None
    quarantine: list[LocalLibraryQuarantineRead] = Field(default_factory=list)


class LocalPaperSearchRequest(BaseSchema):
    query: str = Field(default="", max_length=1000)
    author: str | None = Field(default=None, max_length=300)
    doi: str | None = Field(default=None, max_length=500)
    bibtex_type: str | None = Field(default=None, max_length=64)
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    # Internal/UI scope restriction used by grounded question answering.  The
    # service still applies the owner and INDEXED predicates, so IDs never
    # bypass private-library authorization.
    paper_ids: list[UUID] | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=100)


class LocalPaperEvidenceRead(BaseSchema):
    page_number: int
    chunk_index: int
    text: str
    score: float | None = None
    vector_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    section_heading: str | None = None
    paragraph_index: int | None = None
    bbox: list[float] | None = None
    figure_id: UUID | None = None
    parent_text: str | None = None


class LocalPaperRead(BaseSchema):
    id: UUID
    citekey: str
    doi: str | None = None
    title: str
    authors: list[str]
    publication_year: int | None = None
    bibtex_type: str
    source_kind: Literal["pdf", "html"]
    relative_source_path: str
    evidence: list[LocalPaperEvidenceRead] = Field(default_factory=list)
    # Structured sections for deep analysis
    abstract_text: str | None = None
    introduction_text: str | None = None
    conclusion_text: str | None = None


class LocalPaperSearchResponse(BaseSchema):
    items: list[LocalPaperRead]
    total: int
    retrieval_mode: Literal["hybrid", "metadata"]
    candidate_chunks: int = 0
    candidate_papers: int = 0
    rejected_by_score: int = 0
    insufficient_evidence: bool = False


class LocalPaperAskRequest(BaseSchema):
    question: str = Field(min_length=3, max_length=4000)
    limit: int = Field(default=8, ge=1, le=16)
    # A pronoun such as “these papers” has a deterministic scope: the paper
    # IDs returned by the immediately preceding local-library search.
    paper_ids: list[UUID] = Field(min_length=1, max_length=50)
    query_context: str | None = Field(default=None, max_length=1000)


class LocalPaperCitationRead(BaseSchema):
    paper_id: UUID
    citekey: str
    title: str
    doi: str | None = None
    authors: list[str] = Field(default_factory=list)
    publication_year: int | None = None
    page_number: int
    text: str


class LocalPaperAskResponse(BaseSchema):
    answer: str
    generated_by_llm: bool
    citations: list[LocalPaperCitationRead]


class LocalPaperExportRequest(LocalPaperSearchRequest):
    format: Literal["markdown", "csv", "bibtex", "opml"]

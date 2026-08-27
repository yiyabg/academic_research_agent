"""Private, deployment-managed Zotero paper-library models.

This domain intentionally has no relation to ``rag_documents``: its source is
an administrator-owned read-only directory and its evidence remains page-bound.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class LocalPaperLibrary(Base, TimestampMixin):
    __tablename__ = "local_paper_libraries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_SYNCED")
    last_sync_summary_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    owner: Mapped["User"] = relationship("User")
    papers: Mapped[list["LocalPaper"]] = relationship(
        "LocalPaper", back_populates="library", cascade="all, delete-orphan"
    )
    sync_runs: Mapped[list["LocalPaperSyncRun"]] = relationship(
        "LocalPaperSyncRun", back_populates="library", cascade="all, delete-orphan"
    )


class LocalPaper(Base, TimestampMixin):
    __tablename__ = "local_papers"
    __table_args__ = (
        UniqueConstraint("library_id", "citekey", name="uq_local_paper_library_citekey"),
        UniqueConstraint("library_id", "source_sha256", name="uq_local_paper_library_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    citekey: Mapped[str] = mapped_column(String(255), nullable=False)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bibtex_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relative_source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf | html
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy-fixed-chunk-v1"
    )
    bibtex_entry: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="INDEXED", index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Normalized metadata fields
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # DEPRECATED: Structured key sections - use LocalPaperSection with section_type instead
    abstract_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    introduction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    library: Mapped["LocalPaperLibrary"] = relationship(
        "LocalPaperLibrary", back_populates="papers"
    )
    chunks: Mapped[list["LocalPaperChunk"]] = relationship(
        "LocalPaperChunk", back_populates="paper", cascade="all, delete-orphan"
    )
    sections: Mapped[list["LocalPaperSection"]] = relationship(
        "LocalPaperSection", back_populates="paper", cascade="all, delete-orphan"
    )
    figures: Mapped[list["LocalPaperFigure"]] = relationship(
        "LocalPaperFigure", back_populates="paper", cascade="all, delete-orphan"
    )
    tables: Mapped[list["LocalPaperTable"]] = relationship(
        "LocalPaperTable", back_populates="paper", cascade="all, delete-orphan"
    )
    document_versions: Mapped[list["LocalPaperDocumentVersion"]] = relationship(
        "LocalPaperDocumentVersion", back_populates="paper", cascade="all, delete-orphan"
    )


class LocalPaperDocumentVersion(Base, TimestampMixin):
    """Immutable parser/chunker output for one local-paper source revision."""

    __tablename__ = "local_paper_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "source_sha256",
            "parser_version",
            "chunker_version",
            name="uq_local_paper_document_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="BUILDING", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    paper: Mapped["LocalPaper"] = relationship("LocalPaper", back_populates="document_versions")
    sections: Mapped[list["LocalPaperSection"]] = relationship(
        "LocalPaperSection", back_populates="document_version", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["LocalPaperChunk"]] = relationship(
        "LocalPaperChunk", back_populates="document_version", cascade="all, delete-orphan"
    )


class LocalPaperSection(Base, TimestampMixin):
    """Large, page-bound parent document preserving structure and location."""

    __tablename__ = "local_paper_sections"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "page_number",
            "section_index",
            name="uq_local_paper_section_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_document_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False, default="正文")
    heading_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    section_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    heading_path_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    section_type: Mapped[str] = mapped_column(String(48), nullable=False, default="BODY", index=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    paper: Mapped["LocalPaper"] = relationship("LocalPaper", back_populates="sections")
    document_version: Mapped["LocalPaperDocumentVersion | None"] = relationship(
        "LocalPaperDocumentVersion", back_populates="sections"
    )
    chunks: Mapped[list["LocalPaperChunk"]] = relationship(
        "LocalPaperChunk", back_populates="section", cascade="all, delete-orphan"
    )


class LocalPaperChunk(Base, TimestampMixin):
    __tablename__ = "local_paper_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "page_number",
            "chunk_index",
            name="uq_local_paper_chunk_version",
        ),
        UniqueConstraint(
            "document_version_id",
            "content_sha256",
            name="uq_local_paper_chunk_content_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_document_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_sections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Links a figure OCR/caption/body-reference child to its authoritative
    # page-bound figure evidence record.
    figure_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_figures.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading: Mapped[str] = mapped_column(Text, nullable=False, default="正文")
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    chunk_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lexical_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    lexical_tsv: Mapped[object | None] = mapped_column(TSVECTOR, nullable=True)

    paper: Mapped["LocalPaper"] = relationship("LocalPaper", back_populates="chunks")
    document_version: Mapped["LocalPaperDocumentVersion | None"] = relationship(
        "LocalPaperDocumentVersion", back_populates="chunks"
    )
    section: Mapped["LocalPaperSection | None"] = relationship(
        "LocalPaperSection", back_populates="chunks"
    )
    figure: Mapped["LocalPaperFigure | None"] = relationship(
        "LocalPaperFigure", back_populates="chunks"
    )
    locators: Mapped[list["LocalPaperChunkLocator"]] = relationship(
        "LocalPaperChunkLocator", back_populates="chunk", cascade="all, delete-orphan"
    )


class LocalPaperChunkLocator(Base, TimestampMixin):
    """Every source occurrence contributing to one semantic child chunk."""

    __tablename__ = "local_paper_chunk_locators"
    __table_args__ = (
        UniqueConstraint("chunk_id", "page_number", "ordinal", name="uq_local_paper_chunk_locator"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="text")

    chunk: Mapped["LocalPaperChunk"] = relationship("LocalPaperChunk", back_populates="locators")


class LocalPaperTable(Base, TimestampMixin):
    """Lossless table structure; Markdown is only a retrieval serialization."""

    __tablename__ = "local_paper_tables"
    __table_args__ = (
        UniqueConstraint("document_version_id", "table_index", name="uq_local_paper_table_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    caption_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    html_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    paper: Mapped["LocalPaper"] = relationship("LocalPaper", back_populates="tables")


class LocalPaperFigure(Base, TimestampMixin):
    """Figure region and OCR evidence. Pixels remain in the original PDF."""

    __tablename__ = "local_paper_figures"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "page_number",
            "figure_index",
            name="uq_local_paper_figure_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_document_versions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_index: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bbox_json: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    caption_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_sections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="figure")
    reference_texts_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    perceptual_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    paper: Mapped["LocalPaper"] = relationship("LocalPaper", back_populates="figures")
    chunks: Mapped[list["LocalPaperChunk"]] = relationship(
        "LocalPaperChunk", back_populates="figure"
    )


class LocalPaperRetrievalRun(Base, TimestampMixin):
    """Durable summary of an explainable local-library retrieval request."""

    __tablename__ = "local_paper_retrieval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index_version: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    summary_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_cache_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

class LocalPaperSyncRun(Base, TimestampMixin):
    __tablename__ = "local_paper_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    summary_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    library: Mapped["LocalPaperLibrary"] = relationship(
        "LocalPaperLibrary", back_populates="sync_runs"
    )


class LocalPaperSyncEvent(Base, TimestampMixin):
    """Durable, ordered progress event for one local-paper sync run.

    PostgreSQL remains the replay authority; Redis only fans this payload out
    to currently connected SSE/WebSocket clients.
    """

    __tablename__ = "local_paper_sync_events"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "sequence", name="uq_local_paper_sync_event_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_sync_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class LocalPaperQuarantineItem(Base, TimestampMixin):
    __tablename__ = "local_paper_quarantine_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("local_paper_sync_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    citekey: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)

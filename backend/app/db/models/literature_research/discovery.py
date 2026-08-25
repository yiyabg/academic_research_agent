"""Auditable discovery, raw-source, canonical-work, and version models."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.literature_research.run import ResearchRun


class ResearchSearchQuery(Base, TimestampMixin):
    __tablename__ = "research_search_queries"
    __table_args__ = (
        UniqueConstraint("run_id", "query_id", name="uq_research_query_run_query_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    query_id: Mapped[str] = mapped_column(String(80), nullable=False)
    family: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    query_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    pages: Mapped[list["ResearchSourcePage"]] = relationship(
        "ResearchSourcePage", back_populates="query", cascade="all, delete-orphan"
    )


class ResearchSourcePage(Base, TimestampMixin):
    __tablename__ = "research_source_pages"
    __table_args__ = (
        UniqueConstraint("run_id", "request_fingerprint", name="uq_source_page_run_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    search_query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_search_queries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor_in: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_out: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)

    query: Mapped["ResearchSearchQuery"] = relationship(
        "ResearchSearchQuery", back_populates="pages"
    )


class ResearchSourceFailure(Base):
    __tablename__ = "research_source_failures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    query_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchVenue(Base, TimestampMixin):
    __tablename__ = "research_venues"
    __table_args__ = (
        UniqueConstraint("normalized_name", "venue_type", name="uq_research_venue_name_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    venue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    issn_l: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    issns_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchWork(Base, TimestampMixin):
    __tablename__ = "research_works"
    __table_args__ = (
        UniqueConstraint("run_id", "cluster_key", name="uq_research_work_run_cluster"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cluster_key: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str] = mapped_column(String(48), nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authors_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    field_provenance_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    duplicate_decisions_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    preferred_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "research_work_versions.id",
            name="fk_research_work_preferred_version",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    run: Mapped["ResearchRun"] = relationship("ResearchRun", back_populates="works")
    versions: Mapped[list["ResearchWorkVersion"]] = relationship(
        "ResearchWorkVersion",
        back_populates="work",
        cascade="all, delete-orphan",
        foreign_keys="ResearchWorkVersion.work_id",
    )
    preferred_version: Mapped["ResearchWorkVersion | None"] = relationship(
        "ResearchWorkVersion", foreign_keys=[preferred_version_id], post_update=True
    )


class ResearchWorkVersion(Base, TimestampMixin):
    __tablename__ = "research_work_versions"
    __table_args__ = (
        UniqueConstraint("work_id", "source", "source_id", name="uq_work_version_source_record"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_venues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_type: Mapped[str] = mapped_column(String(48), nullable=False)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pmid: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    published_online: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_print: Mapped[date | None] = mapped_column(Date, nullable=True)
    preprint_first_posted: Mapped[date | None] = mapped_column(Date, nullable=True)
    accepted: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_access_pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    volume: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issue: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    work: Mapped["ResearchWork"] = relationship(
        "ResearchWork", back_populates="versions", foreign_keys=[work_id]
    )
    venue: Mapped["ResearchVenue | None"] = relationship("ResearchVenue")
    source_records: Mapped[list["ResearchSourceRecord"]] = relationship(
        "ResearchSourceRecord", back_populates="version"
    )


class ResearchSourceRecord(Base):
    __tablename__ = "research_source_records"
    __table_args__ = (
        UniqueConstraint(
            "source_page_id",
            "source",
            "source_id",
            name="uq_research_source_record_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_source_pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    version: Mapped["ResearchWorkVersion | None"] = relationship(
        "ResearchWorkVersion", back_populates="source_records"
    )

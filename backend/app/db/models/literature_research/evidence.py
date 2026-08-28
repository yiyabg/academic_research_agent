"""Relevance, full-text acquisition, parsed blocks, and evidence audit models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ResearchRelevanceScore(Base, TimestampMixin):
    __tablename__ = "research_relevance_scores"
    __table_args__ = (UniqueConstraint("run_id", "work_id", name="uq_relevance_run_work"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_works.id", ondelete="CASCADE"), nullable=False
    )
    lexical_score: Mapped[float] = mapped_column(Float, nullable=False)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_encoder_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    facet_scores_json: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    model_versions_json: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    facet_judgement_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


class ResearchFullTextAcquisition(Base, TimestampMixin):
    __tablename__ = "research_fulltext_acquisitions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    license_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    license_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_ips_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    redirect_chain_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    malware_scan_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NOT_SCANNED"
    )
    malware_scan_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    malware_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    malware_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResearchParsedBlock(Base):
    __tablename__ = "research_parsed_blocks"
    __table_args__ = (
        UniqueConstraint("version_id", "block_id", name="uq_parsed_block_version_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(24), nullable=False, default="native")


class ResearchParsingResult(Base, TimestampMixin):
    __tablename__ = "research_parsing_results"
    __table_args__ = (
        UniqueConstraint("run_id", "version_id", name="uq_parsing_result_run_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parsed_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_coverage: Mapped[float] = mapped_column(Float, nullable=False)
    page_count_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    section_detection_f1_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    caption_count: Mapped[int] = mapped_column(Integer, nullable=False)
    caption_link_rate: Mapped[float] = mapped_column(Float, nullable=False)
    ocr_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ocr_page_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    total_characters: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_versions_json: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    error_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    blocks_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Migration 0038 deliberately created this audit-ledger timestamp as
    # NOT NULL with a database default; override the nullable mixin field so
    # SQLAlchemy omits it on INSERT instead of binding NULL explicitly.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResearchFigureArtifact(Base, TimestampMixin):
    __tablename__ = "research_figure_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "figure_id", name="uq_figure_artifact_run_figure"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    figure_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_json: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    image_object_key: Mapped[str] = mapped_column(Text, nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    table_cells_json: Mapped[list[list[str]]] = mapped_column(JSONB, nullable=False)
    exact_numeric_values_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(24), nullable=False)
    license_scope: Mapped[str] = mapped_column(String(24), nullable=False)


class ResearchEvidenceLocator(Base, TimestampMixin):
    __tablename__ = "research_evidence_locators"
    __table_args__ = (UniqueConstraint("run_id", "evidence_id", name="uq_evidence_run_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_works.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_path_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_start: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_end: Mapped[int] = mapped_column(Integer, nullable=False)
    block_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(24), nullable=False, default="native")

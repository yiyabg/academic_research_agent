"""Audited analyses, synthesis, immutable artifacts, and release decisions."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ResearchPaperAnalysis(Base, TimestampMixin):
    __tablename__ = "research_paper_analyses"
    __table_args__ = (
        UniqueConstraint("run_id", "work_id", "attempt", name="uq_analysis_run_work_attempt"),
    )
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
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="INITIAL")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_paper_analyses.id", ondelete="SET NULL"),
        nullable=True,
    )
    analysis_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_coverage: Mapped[float] = mapped_column(nullable=False)
    contradicted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unsupported_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_versions_json: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)


class ResearchSynthesis(Base, TimestampMixin):
    __tablename__ = "research_syntheses"
    __table_args__ = (UniqueConstraint("run_id", "generation", name="uq_synthesis_run_generation"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    synthesis_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)


class ResearchArtifact(Base, TimestampMixin):
    __tablename__ = "research_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "generation", "format", name="uq_artifact_run_generation_format"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    format: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class ResearchReleaseCheck(Base):
    __tablename__ = "research_release_checks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blockers_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

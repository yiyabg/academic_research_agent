"""Licensed metric snapshots and immutable constraint decision ledger."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.literature_research.discovery import ResearchVenue


class ResearchMetricSnapshot(Base, TimestampMixin):
    __tablename__ = "research_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "source_version", "payload_sha256", name="uq_metric_snapshot_payload"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_names_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    license_reference: Mapped[str] = mapped_column(Text, nullable=False)
    authorized_scope: Mapped[str] = mapped_column(Text, nullable=False)
    license_attested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE", index=True)
    imported_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)

    facts: Mapped[list["ResearchVenueMetricFact"]] = relationship(
        "ResearchVenueMetricFact", back_populates="snapshot", cascade="all, delete-orphan"
    )


class ResearchVenueMetricFact(Base):
    __tablename__ = "research_venue_metric_facts"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "venue_normalized_name",
            "venue_type",
            "metric_name",
            "metric_year",
            name="uq_metric_fact_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_metric_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_venues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    venue_name: Mapped[str] = mapped_column(Text, nullable=False)
    venue_normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    venue_type: Mapped[str] = mapped_column(String(32), nullable=False)
    issn_l: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    metric_value: Mapped[object] = mapped_column(JSONB, nullable=False)
    metric_year: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)

    snapshot: Mapped["ResearchMetricSnapshot"] = relationship(
        "ResearchMetricSnapshot", back_populates="facts"
    )
    venue: Mapped["ResearchVenue | None"] = relationship("ResearchVenue")


class ResearchWorkEligibility(Base):
    __tablename__ = "research_work_eligibility"
    __table_args__ = (UniqueConstraint("run_id", "work_id", name="uq_work_eligibility_run_work"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_works.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    protocol_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    hard_pass_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_fail_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchConstraintEvaluation(Base):
    __tablename__ = "research_constraint_evaluations"
    __table_args__ = (
        UniqueConstraint("run_id", "work_id", "constraint_id", name="uq_constraint_ledger_entry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_works.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_work_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    protocol_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    constraint_id: Mapped[str] = mapped_column(String(100), nullable=False)
    field: Mapped[str] = mapped_column(String(200), nullable=False)
    operator: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    observed_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_metric_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    metric_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_venue_metric_facts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    metric_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

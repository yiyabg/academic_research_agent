"""Research workflow run and idempotent task execution models."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.schemas.literature_research.run import ExecutionMode, RunState

if TYPE_CHECKING:
    from app.db.models.literature_research.discovery import ResearchWork
    from app.db.models.literature_research.outbox import ResearchOutboxEvent
    from app.db.models.literature_research.project import ResearchProject
    from app.db.models.literature_research.protocol import ResearchProtocolVersion


class ResearchRun(Base, TimestampMixin):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_request_id", name="uq_run_owner_client_request"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_protocol_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(48), nullable=False, default=RunState.QUEUED.value, index=True
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ExecutionMode.FULL_RESEARCH.value
    )
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    strict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    shortage_report_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failed_detail: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    project: Mapped["ResearchProject"] = relationship("ResearchProject", back_populates="runs")
    protocol_version: Mapped["ResearchProtocolVersion"] = relationship(
        "ResearchProtocolVersion", back_populates="runs"
    )
    events: Mapped[list["ResearchOutboxEvent"]] = relationship(
        "ResearchOutboxEvent", back_populates="run", cascade="all, delete-orphan"
    )
    works: Mapped[list["ResearchWork"]] = relationship(
        "ResearchWork", back_populates="run", cascade="all, delete-orphan"
    )


class ResearchTaskExecution(Base, TimestampMixin):
    __tablename__ = "research_task_executions"
    __table_args__ = (
        UniqueConstraint("run_id", "stage", "shard_key", "input_hash", name="uq_task_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    shard_key: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    input_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchRunControl(Base, TimestampMixin):
    """Durable control request independent of the long-running stage row lock."""

    __tablename__ = "research_run_controls"
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    requested_action: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

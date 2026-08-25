"""Immutable versioned research protocol model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.schemas.literature_research.protocol import ProtocolStatus

if TYPE_CHECKING:
    from app.db.models.literature_research.project import ResearchProject
    from app.db.models.literature_research.run import ResearchRun


class ResearchProtocolVersion(Base, TimestampMixin):
    __tablename__ = "research_protocol_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_protocol_project_version"),
        UniqueConstraint("project_id", "protocol_hash", name="uq_protocol_project_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ProtocolStatus.DRAFT.value, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    project: Mapped["ResearchProject"] = relationship("ResearchProject", back_populates="protocols")
    runs: Mapped[list["ResearchRun"]] = relationship(
        "ResearchRun", back_populates="protocol_version"
    )

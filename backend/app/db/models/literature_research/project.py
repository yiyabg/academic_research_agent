"""Research project aggregate root."""

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.schemas.literature_research.project import ResearchProjectStatus


class ResearchProject(Base, TimestampMixin):
    __tablename__ = "research_projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ResearchProjectStatus.ACTIVE.value, index=True
    )

    protocols: Mapped[list["ResearchProtocolVersion"]] = relationship(
        "ResearchProtocolVersion", back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ResearchRun"]] = relationship(
        "ResearchRun", back_populates="project", cascade="all, delete-orphan"
    )


from app.db.models.literature_research.protocol import ResearchProtocolVersion
from app.db.models.literature_research.run import ResearchRun

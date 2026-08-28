"""Research organizations and durable user memberships."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.schemas.literature_research.organization import ResearchOrganizationRole

if TYPE_CHECKING:
    from app.db.models.user import User


class ResearchOrganization(Base, TimestampMixin):
    __tablename__ = "research_organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    memberships: Mapped[list["ResearchOrganizationMember"]] = relationship(
        "ResearchOrganizationMember",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class ResearchOrganizationMember(Base, TimestampMixin):
    __tablename__ = "research_organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_research_organization_member"),
        CheckConstraint("role IN ('OWNER', 'MEMBER')", name="ck_research_org_member_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ResearchOrganizationRole.MEMBER.value
    )

    organization: Mapped["ResearchOrganization"] = relationship(
        "ResearchOrganization", back_populates="memberships"
    )
    user: Mapped["User"] = relationship("User")

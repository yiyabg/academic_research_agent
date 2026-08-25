"""add real research organizations and memberships

Revision ID: 0041_research_orgs
Revises: 0040_gold_provenance
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0041_research_orgs"
down_revision = "0040_gold_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_organizations",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "research_organizations_created_by_idx",
        "research_organizations",
        ["created_by"],
    )
    op.create_index(
        "research_organizations_slug_idx",
        "research_organizations",
        ["slug"],
        unique=True,
    )
    op.create_table(
        "research_organization_members",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('OWNER', 'MEMBER')", name="ck_research_org_member_role"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["research_organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_research_organization_member"
        ),
    )
    op.create_index(
        "research_organization_members_organization_id_idx",
        "research_organization_members",
        ["organization_id"],
    )
    op.create_index(
        "research_organization_members_user_id_idx",
        "research_organization_members",
        ["user_id"],
    )
    op.create_foreign_key(
        "research_projects_organization_id_fkey",
        "research_projects",
        "research_organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "research_runs_organization_id_fkey",
        "research_runs",
        "research_organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "research_runs_organization_id_fkey", "research_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "research_projects_organization_id_fkey",
        "research_projects",
        type_="foreignkey",
    )
    op.drop_index(
        "research_organization_members_user_id_idx",
        table_name="research_organization_members",
    )
    op.drop_index(
        "research_organization_members_organization_id_idx",
        table_name="research_organization_members",
    )
    op.drop_table("research_organization_members")
    op.drop_index("research_organizations_slug_idx", table_name="research_organizations")
    op.drop_index(
        "research_organizations_created_by_idx", table_name="research_organizations"
    )
    op.drop_table("research_organizations")

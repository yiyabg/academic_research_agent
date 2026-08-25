"""add five-layer research memory persistence and feedback

Revision ID: 0033_research_memory_feedback
Revises: 0032_analysis_artifacts_release
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0033_research_memory_feedback"
down_revision = "0032_analysis_artifacts_release"
branch_labels = None
depends_on = None


def _id():
    return sa.Column(
        "id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _timestamps():
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column("content_type", sa.String(100), nullable=True),
    )
    op.create_table(
        "research_project_memories",
        _id(),
        sa.Column(
            "project_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memory_type", sa.String(64), nullable=False),
        sa.Column("content_json", JSONB(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_project_memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        *_timestamps(),
    )
    op.create_index(
        "ix_research_project_memories_project_id", "research_project_memories", ["project_id"]
    )
    op.create_index(
        "ix_research_project_memories_memory_type", "research_project_memories", ["memory_type"]
    )
    op.create_table(
        "user_research_profiles",
        _id(),
        sa.Column(
            "user_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("preferences_json", JSONB(), nullable=False),
        sa.Column("confirmation_note", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "version", name="uq_research_profile_version"),
    )
    op.create_index("ix_user_research_profiles_user_id", "user_research_profiles", ["user_id"])
    op.create_table(
        "research_policy_versions",
        _id(),
        sa.Column("policy_key", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        *_timestamps(),
        sa.UniqueConstraint("policy_key", "version", name="uq_research_policy_version"),
    )
    op.create_index(
        "ix_research_policy_versions_policy_key", "research_policy_versions", ["policy_key"]
    )
    op.create_table(
        "research_feedback_samples",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("feedback_type", sa.String(64), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_research_feedback_samples_run_id", "research_feedback_samples", ["run_id"])
    op.create_index(
        "ix_research_feedback_samples_user_id", "research_feedback_samples", ["user_id"]
    )
    op.create_index(
        "ix_research_feedback_samples_feedback_type", "research_feedback_samples", ["feedback_type"]
    )


def downgrade() -> None:
    op.drop_table("research_feedback_samples")
    op.drop_table("research_policy_versions")
    op.drop_table("user_research_profiles")
    op.drop_table("research_project_memories")
    op.drop_column("research_fulltext_acquisitions", "content_type")

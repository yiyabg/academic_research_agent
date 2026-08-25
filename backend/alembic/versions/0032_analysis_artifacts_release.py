"""add analyses artifacts and release checks

Revision ID: 0032_analysis_artifacts_release
Revises: 0031_relevance_fulltext_evidence
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0032_analysis_artifacts_release"
down_revision = "0031_relevance_fulltext_evidence"
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
    op.create_table(
        "research_paper_analyses",
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
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_json", JSONB(), nullable=False),
        sa.Column("evidence_coverage", sa.Float(), nullable=False),
        sa.Column("contradicted_count", sa.Integer(), nullable=False),
        sa.Column("unsupported_count", sa.Integer(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column(
            "model_versions_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "work_id", name="uq_analysis_run_work"),
    )
    op.create_index("ix_research_paper_analyses_run_id", "research_paper_analyses", ["run_id"])
    op.create_table(
        "research_syntheses",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("synthesis_json", JSONB(), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", name="uq_synthesis_run"),
    )
    op.create_index("ix_research_syntheses_run_id", "research_syntheses", ["run_id"])
    op.create_table(
        "research_artifacts",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("format", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "format", name="uq_artifact_run_format"),
    )
    op.create_index("ix_research_artifacts_run_id", "research_artifacts", ["run_id"])
    op.create_table(
        "research_release_checks",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False),
        sa.Column("blockers_json", JSONB(), nullable=False),
        sa.Column("snapshot_json", JSONB(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_release_checks_run_id", "research_release_checks", ["run_id"])


def downgrade() -> None:
    op.drop_table("research_release_checks")
    op.drop_table("research_artifacts")
    op.drop_table("research_syntheses")
    op.drop_table("research_paper_analyses")

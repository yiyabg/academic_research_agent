"""add versioned offline evaluation datasets and results

Revision ID: 0035_offline_evaluation
Revises: 0034_analysis_attempts
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0035_offline_evaluation"
down_revision = "0034_analysis_attempts"
branch_labels = None
depends_on = None


def _id():
    return sa.Column(
        "id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "research_evaluation_datasets",
        _id(),
        sa.Column(
            "project_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("cases_json", JSONB(), nullable=False),
        sa.Column("observations_json", JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_eval_dataset_version"),
    )
    op.create_index(
        "ix_research_evaluation_datasets_project_id",
        "research_evaluation_datasets",
        ["project_id"],
    )
    op.create_table(
        "research_evaluation_results",
        _id(),
        sa.Column(
            "dataset_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_evaluation_datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("metrics_json", JSONB(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("failures_json", JSONB(), nullable=False),
        sa.Column("details_json", JSONB(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_research_evaluation_results_dataset_id",
        "research_evaluation_results",
        ["dataset_id"],
    )
    op.create_index(
        "ix_research_evaluation_results_run_id",
        "research_evaluation_results",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_table("research_evaluation_results")
    op.drop_table("research_evaluation_datasets")

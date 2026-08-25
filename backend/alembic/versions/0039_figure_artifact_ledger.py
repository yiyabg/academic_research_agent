"""add hash-bound figure and table artifact ledger

Revision ID: 0039_figure_artifacts
Revises: 0038_parsing_quality
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0039_figure_artifacts"
down_revision = "0038_parsing_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_figure_artifacts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("figure_id", sa.String(32), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("work_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("bbox_json", JSONB(), nullable=False),
        sa.Column("image_object_key", sa.Text(), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_ids_json", JSONB(), nullable=False),
        sa.Column("table_cells_json", JSONB(), nullable=False),
        sa.Column("exact_numeric_values_json", JSONB(), nullable=False),
        sa.Column("extraction_status", sa.String(24), nullable=False),
        sa.Column("license_scope", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["research_works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["research_work_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "figure_id", name="uq_figure_artifact_run_figure"),
    )
    for column in ("run_id", "work_id", "version_id"):
        op.create_index(
            f"ix_research_figure_artifacts_{column}",
            "research_figure_artifacts",
            [column],
        )


def downgrade() -> None:
    for column in ("version_id", "work_id", "run_id"):
        op.drop_index(
            f"ix_research_figure_artifacts_{column}",
            table_name="research_figure_artifacts",
        )
    op.drop_table("research_figure_artifacts")

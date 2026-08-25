"""add immutable generations for syntheses and artifacts

Revision ID: 0036_versioned_outputs
Revises: 0035_offline_evaluation
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0036_versioned_outputs"
down_revision = "0035_offline_evaluation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_synthesis_run", "research_syntheses", type_="unique")
    op.add_column(
        "research_syntheses",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_synthesis_run_generation",
        "research_syntheses",
        ["run_id", "generation"],
    )

    op.drop_constraint("uq_artifact_run_format", "research_artifacts", type_="unique")
    op.add_column(
        "research_artifacts",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_artifact_run_generation_format",
        "research_artifacts",
        ["run_id", "generation", "format"],
    )
    op.add_column(
        "research_release_checks",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "research_run_controls",
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("requested_action", sa.String(24), nullable=False),
        sa.Column(
            "requested_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("research_run_controls")
    # The old schema can represent only one output generation per run.
    op.execute("DELETE FROM research_release_checks WHERE generation > 1")
    op.drop_column("research_release_checks", "generation")

    op.execute("DELETE FROM research_artifacts WHERE generation > 1")
    op.drop_constraint("uq_artifact_run_generation_format", "research_artifacts", type_="unique")
    op.drop_column("research_artifacts", "generation")
    op.create_unique_constraint(
        "uq_artifact_run_format", "research_artifacts", ["run_id", "format"]
    )

    op.execute("DELETE FROM research_syntheses WHERE generation > 1")
    op.drop_constraint("uq_synthesis_run_generation", "research_syntheses", type_="unique")
    op.drop_column("research_syntheses", "generation")
    op.create_unique_constraint("uq_synthesis_run", "research_syntheses", ["run_id"])

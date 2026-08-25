"""add immutable per-paper analysis attempts

Revision ID: 0034_analysis_attempts
Revises: 0033_research_memory_feedback
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0034_analysis_attempts"
down_revision = "0033_research_memory_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_analysis_run_work", "research_paper_analyses", type_="unique")
    op.add_column(
        "research_paper_analyses",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "research_paper_analyses",
        sa.Column("trigger", sa.String(32), nullable=False, server_default="INITIAL"),
    )
    op.add_column(
        "research_paper_analyses",
        sa.Column(
            "requested_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "research_paper_analyses",
        sa.Column(
            "supersedes_analysis_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_paper_analyses.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_analysis_run_work_attempt",
        "research_paper_analyses",
        ["run_id", "work_id", "attempt"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_analysis_run_work_attempt", "research_paper_analyses", type_="unique"
    )
    op.drop_column("research_paper_analyses", "supersedes_analysis_id")
    op.drop_column("research_paper_analyses", "requested_by")
    op.drop_column("research_paper_analyses", "trigger")
    op.drop_column("research_paper_analyses", "attempt")
    op.create_unique_constraint(
        "uq_analysis_run_work", "research_paper_analyses", ["run_id", "work_id"]
    )

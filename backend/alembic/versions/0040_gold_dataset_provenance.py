"""add gold dataset adjudication provenance

Revision ID: 0040_gold_provenance
Revises: 0039_figure_artifacts
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0040_gold_provenance"
down_revision = "0039_figure_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_evaluation_datasets",
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
    )
    op.add_column(
        "research_evaluation_datasets",
        sa.Column("provenance_json", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_evaluation_datasets", "provenance_json")
    op.drop_column("research_evaluation_datasets", "status")

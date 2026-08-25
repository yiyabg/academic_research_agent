"""link local-paper chunks to authoritative figure evidence

Revision ID: 0047_local_paper_figure_links
Revises: 0046_local_paper_structured
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0047_local_paper_figure_links"
down_revision = "0046_local_paper_structured"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "local_paper_figures",
        sa.Column("figure_label", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "local_paper_chunks",
        sa.Column(
            "figure_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("local_paper_figures.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_local_paper_chunks_figure_id", "local_paper_chunks", ["figure_id"])


def downgrade() -> None:
    op.drop_index("ix_local_paper_chunks_figure_id", table_name="local_paper_chunks")
    op.drop_column("local_paper_chunks", "figure_id")
    op.drop_column("local_paper_figures", "figure_label")

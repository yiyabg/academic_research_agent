"""add structured sections (abstract, introduction, conclusion) to local_papers

Revision ID: 0045_add_structured_sections
Revises: 0044_local_paper_library
Create Date: 2026-08-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0045_add_structured_sections"
down_revision = "0044_local_paper_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("local_papers", sa.Column("abstract_text", sa.Text(), nullable=True))
    op.add_column("local_papers", sa.Column("introduction_text", sa.Text(), nullable=True))
    op.add_column("local_papers", sa.Column("conclusion_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("local_papers", "conclusion_text")
    op.drop_column("local_papers", "introduction_text")
    op.drop_column("local_papers", "abstract_text")

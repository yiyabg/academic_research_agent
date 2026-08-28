"""add venue and keywords fields, deprecate redundant text fields

Revision ID: 0053_add_venue_keywords
Revises: 0052_local_paper_analysis_stages
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0053_add_venue_keywords"
down_revision = "0052_local_paper_analysis_stages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add normalized metadata fields for venue and keywords.

    The abstract_text, introduction_text, and conclusion_text fields are kept
    for backward compatibility but marked as deprecated. New code should use
    LocalPaperSection with section_type='ABSTRACT'/'INTRODUCTION'/'CONCLUSION'.
    """
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    # Add normalized metadata fields
    op.add_column(
        "local_papers",
        sa.Column("venue", sa.Text(), nullable=True),
    )
    op.add_column(
        "local_papers",
        sa.Column("keywords_json", jsonb, nullable=False, server_default="[]"),
    )

    # Note: abstract_text, introduction_text, conclusion_text remain in the table
    # for backward compatibility but are marked deprecated in the ORM model.
    # They will be set to NULL during sync and should not be used by new code.


def downgrade() -> None:
    """Remove venue and keywords fields."""
    op.drop_column("local_papers", "keywords_json")
    op.drop_column("local_papers", "venue")

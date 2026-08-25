"""persist evidence-grounded relevance facet judgements

Revision ID: 0042_relevance_facets
Revises: 0041_research_orgs
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0042_relevance_facets"
down_revision = "0041_research_orgs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_relevance_scores",
        sa.Column(
            "facet_judgement_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("research_relevance_scores", "facet_judgement_json")

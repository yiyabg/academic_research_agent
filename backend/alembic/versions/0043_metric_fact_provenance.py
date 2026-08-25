"""bind constraint ledgers to exact annual metric facts

Revision ID: 0043_metric_fact_provenance
Revises: 0042_relevance_facets
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0043_metric_fact_provenance"
down_revision = "0042_relevance_facets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows without a year cannot be made auditable by guessing. The
    # migration intentionally fails until an administrator replaces/reimports
    # such a snapshot with an explicitly licensed annual fact set.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM research_venue_metric_facts WHERE metric_year IS NULL
          ) THEN
            RAISE EXCEPTION
              '0043 requires every venue metric fact to have an explicit metric_year';
          END IF;
        END $$;
        """
    )
    op.alter_column(
        "research_venue_metric_facts",
        "metric_year",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.add_column(
        "research_constraint_evaluations",
        sa.Column("metric_fact_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "research_constraint_evaluations",
        sa.Column("metric_year", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_constraint_evaluation_metric_fact",
        "research_constraint_evaluations",
        "research_venue_metric_facts",
        ["metric_fact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_research_constraint_evaluations_metric_fact_id",
        "research_constraint_evaluations",
        ["metric_fact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_constraint_evaluations_metric_fact_id",
        table_name="research_constraint_evaluations",
    )
    op.drop_constraint(
        "fk_constraint_evaluation_metric_fact",
        "research_constraint_evaluations",
        type_="foreignkey",
    )
    op.drop_column("research_constraint_evaluations", "metric_year")
    op.drop_column("research_constraint_evaluations", "metric_fact_id")
    op.alter_column(
        "research_venue_metric_facts",
        "metric_year",
        existing_type=sa.Integer(),
        nullable=True,
    )

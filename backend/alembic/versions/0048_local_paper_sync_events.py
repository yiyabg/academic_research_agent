"""persist replayable local-paper sync progress events

Revision ID: 0048_local_paper_sync_events
Revises: 0047_local_paper_figure_links
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0048_local_paper_sync_events"
down_revision = "0047_local_paper_figure_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_paper_sync_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sync_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("local_paper_sync_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("sync_run_id", "sequence", name="uq_local_paper_sync_event_sequence"),
    )
    op.create_index(
        "ix_local_paper_sync_events_sync_run_id", "local_paper_sync_events", ["sync_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_local_paper_sync_events_sync_run_id", table_name="local_paper_sync_events")
    op.drop_table("local_paper_sync_events")

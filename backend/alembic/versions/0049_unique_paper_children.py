"""enforce unique child text within each local paper

Revision ID: 0049_unique_paper_children
Revises: 0048_local_paper_sync_events
Create Date: 2026-08-24
"""

from alembic import op

revision = "0049_unique_paper_children"
down_revision = "0048_local_paper_sync_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older extraction versions could emit the same header/caption/OCR child
    # many times.  Keep the earliest row before installing the invariant.
    # v4 uses a fresh Qdrant collection, so these removed v3 child IDs are
    # never queried after the required manual rebuild.
    op.execute(
        """
        DELETE FROM local_paper_chunks AS duplicate
        USING local_paper_chunks AS retained
        WHERE duplicate.paper_id = retained.paper_id
          AND duplicate.content_sha256 = retained.content_sha256
          AND duplicate.id > retained.id
        """
    )
    op.create_unique_constraint(
        "uq_local_paper_chunk_content",
        "local_paper_chunks",
        ["paper_id", "content_sha256"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_local_paper_chunk_content", "local_paper_chunks", type_="unique")

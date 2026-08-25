"""add private local Zotero paper-library domain

Revision ID: 0044_local_paper_library
Revises: 0043_metric_fact_provenance
Create Date: 2026-08-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_local_paper_library"
down_revision = "0043_metric_fact_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "local_paper_libraries",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("source_root", sa.Text(), nullable=False),
        sa.Column("qdrant_collection", sa.String(length=100), nullable=False, unique=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="NOT_SYNCED"),
        sa.Column("last_sync_summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "local_paper_sync_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_local_paper_sync_runs_library_id", "local_paper_sync_runs", ["library_id"])
    op.create_index("ix_local_paper_sync_runs_status", "local_paper_sync_runs", ["status"])
    op.create_table(
        "local_papers",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("citekey", sa.String(length=255), nullable=False),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("bibtex_type", sa.String(length=64), nullable=False),
        sa.Column("relative_source_path", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("bibtex_entry", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="INDEXED"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("library_id", "citekey", name="uq_local_paper_library_citekey"),
        sa.UniqueConstraint("library_id", "source_sha256", name="uq_local_paper_library_sha256"),
    )
    op.create_index("ix_local_papers_library_id", "local_papers", ["library_id"])
    op.create_index("ix_local_papers_doi", "local_papers", ["doi"])
    op.create_index("ix_local_papers_publication_year", "local_papers", ["publication_year"])
    op.create_index("ix_local_papers_bibtex_type", "local_papers", ["bibtex_type"])
    op.create_index("ix_local_papers_status", "local_papers", ["status"])
    op.create_table(
        "local_paper_chunks",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("paper_id", "page_number", "chunk_index", name="uq_local_paper_chunk"),
    )
    op.create_index("ix_local_paper_chunks_paper_id", "local_paper_chunks", ["paper_id"])
    op.create_table(
        "local_paper_quarantine_items",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sync_run_id", uuid, sa.ForeignKey("local_paper_sync_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_kind", sa.String(length=48), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("citekey", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_local_paper_quarantine_items_library_id", "local_paper_quarantine_items", ["library_id"])
    op.create_index("ix_local_paper_quarantine_items_sync_run_id", "local_paper_quarantine_items", ["sync_run_id"])
    op.create_index("ix_local_paper_quarantine_items_item_kind", "local_paper_quarantine_items", ["item_kind"])


def downgrade() -> None:
    op.drop_table("local_paper_quarantine_items")
    op.drop_table("local_paper_chunks")
    op.drop_table("local_papers")
    op.drop_table("local_paper_sync_runs")
    op.drop_table("local_paper_libraries")

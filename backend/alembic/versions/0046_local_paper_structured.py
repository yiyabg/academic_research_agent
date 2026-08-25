"""add structured parent-child local paper retrieval data

Revision ID: 0046_local_paper_structured
Revises: 0045_add_structured_sections
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0046_local_paper_structured"
down_revision = "0045_add_structured_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column(
        "local_papers",
        sa.Column(
            "ingestion_version",
            sa.String(length=64),
            nullable=False,
            server_default="legacy-fixed-chunk-v1",
        ),
    )

    op.create_table(
        "local_paper_sections",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False, server_default="正文"),
        sa.Column("heading_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("bbox_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("section_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "paper_id", "page_number", "section_index", name="uq_local_paper_section"
        ),
    )
    op.create_index("ix_local_paper_sections_paper_id", "local_paper_sections", ["paper_id"])

    op.add_column(
        "local_paper_chunks",
        sa.Column(
            "section_id",
            uuid,
            sa.ForeignKey("local_paper_sections.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "local_paper_chunks",
        sa.Column("paragraph_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "local_paper_chunks", sa.Column("heading", sa.Text(), nullable=False, server_default="正文")
    )
    op.add_column(
        "local_paper_chunks",
        sa.Column("bbox_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "local_paper_chunks",
        sa.Column("chunk_kind", sa.String(length=32), nullable=False, server_default="text"),
    )
    op.create_index("ix_local_paper_chunks_section_id", "local_paper_chunks", ["section_id"])

    op.create_table(
        "local_paper_figures",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("figure_index", sa.Integer(), nullable=False),
        sa.Column("bbox_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("caption_text", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "paper_id", "page_number", "figure_index", name="uq_local_paper_figure"
        ),
    )
    op.create_index("ix_local_paper_figures_paper_id", "local_paper_figures", ["paper_id"])


def downgrade() -> None:
    op.drop_table("local_paper_figures")
    op.drop_index("ix_local_paper_chunks_section_id", table_name="local_paper_chunks")
    op.drop_column("local_paper_chunks", "chunk_kind")
    op.drop_column("local_paper_chunks", "bbox_json")
    op.drop_column("local_paper_chunks", "heading")
    op.drop_column("local_paper_chunks", "paragraph_index")
    op.drop_column("local_paper_chunks", "section_id")
    op.drop_table("local_paper_sections")
    op.drop_column("local_papers", "ingestion_version")

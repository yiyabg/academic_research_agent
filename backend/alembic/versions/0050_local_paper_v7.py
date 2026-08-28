"""add document-versioned local-paper v7 storage and persistent lexical index

Revision ID: 0050_local_paper_v7
Revises: 0049_unique_paper_children
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0050_local_paper_v7"
down_revision = "0049_unique_paper_children"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)

    op.create_table(
        "local_paper_document_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("chunker_version", sa.String(length=128), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="BUILDING"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "quality_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "paper_id",
            "source_sha256",
            "parser_version",
            "chunker_version",
            name="uq_local_paper_document_version",
        ),
    )
    op.create_index(
        "ix_local_paper_document_versions_paper_id", "local_paper_document_versions", ["paper_id"]
    )
    op.create_index(
        "ix_local_paper_document_versions_status", "local_paper_document_versions", ["status"]
    )
    op.create_index(
        "ix_local_paper_document_versions_is_active", "local_paper_document_versions", ["is_active"]
    )
    op.create_index(
        "uq_local_paper_active_document_version",
        "local_paper_document_versions",
        ["paper_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.add_column("local_papers", sa.Column("active_document_version_id", uuid, nullable=True))
    op.create_index(
        "ix_local_papers_active_document_version_id", "local_papers", ["active_document_version_id"]
    )
    op.create_foreign_key(
        "fk_local_papers_active_document_version",
        "local_papers",
        "local_paper_document_versions",
        ["active_document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("local_paper_sections", sa.Column("document_version_id", uuid, nullable=True))
    op.add_column(
        "local_paper_sections",
        sa.Column(
            "heading_path_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "local_paper_sections",
        sa.Column("section_type", sa.String(length=48), nullable=False, server_default="BODY"),
    )
    op.add_column("local_paper_sections", sa.Column("page_end", sa.Integer(), nullable=True))
    op.add_column(
        "local_paper_sections",
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_local_paper_sections_document_version",
        "local_paper_sections",
        "local_paper_document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_local_paper_sections_document_version_id",
        "local_paper_sections",
        ["document_version_id"],
    )
    op.create_index(
        "ix_local_paper_sections_section_type", "local_paper_sections", ["section_type"]
    )
    op.drop_constraint("uq_local_paper_section", "local_paper_sections", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_section_version",
        "local_paper_sections",
        ["document_version_id", "page_number", "section_index"],
    )

    op.add_column("local_paper_chunks", sa.Column("document_version_id", uuid, nullable=True))
    op.add_column(
        "local_paper_chunks",
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("local_paper_chunks", sa.Column("embedding_text", sa.Text(), nullable=True))
    op.add_column("local_paper_chunks", sa.Column("lexical_terms", sa.Text(), nullable=True))
    op.add_column(
        "local_paper_chunks", sa.Column("lexical_tsv", postgresql.TSVECTOR(), nullable=True)
    )
    op.create_foreign_key(
        "fk_local_paper_chunks_document_version",
        "local_paper_chunks",
        "local_paper_document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_local_paper_chunks_document_version_id", "local_paper_chunks", ["document_version_id"]
    )
    op.create_index(
        "ix_local_paper_chunks_lexical_tsv",
        "local_paper_chunks",
        ["lexical_tsv"],
        postgresql_using="gin",
    )
    op.drop_constraint("uq_local_paper_chunk", "local_paper_chunks", type_="unique")
    op.drop_constraint("uq_local_paper_chunk_content", "local_paper_chunks", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_chunk_version",
        "local_paper_chunks",
        ["document_version_id", "page_number", "chunk_index"],
    )
    op.create_unique_constraint(
        "uq_local_paper_chunk_content_version",
        "local_paper_chunks",
        ["document_version_id", "content_sha256"],
    )

    op.create_table(
        "local_paper_chunk_locators",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "chunk_id",
            uuid,
            sa.ForeignKey("local_paper_chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("bbox_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "chunk_id", "page_number", "ordinal", name="uq_local_paper_chunk_locator"
        ),
    )
    op.create_index(
        "ix_local_paper_chunk_locators_chunk_id", "local_paper_chunk_locators", ["chunk_id"]
    )

    op.create_table(
        "local_paper_tables",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "document_version_id",
            uuid,
            sa.ForeignKey("local_paper_document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("table_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("bbox_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("caption_text", sa.Text(), nullable=True),
        sa.Column(
            "structure_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("html_text", sa.Text(), nullable=True),
        sa.Column("markdown_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "document_version_id", "table_index", name="uq_local_paper_table_version"
        ),
    )
    op.create_index("ix_local_paper_tables_paper_id", "local_paper_tables", ["paper_id"])
    op.create_index(
        "ix_local_paper_tables_document_version_id", "local_paper_tables", ["document_version_id"]
    )

    op.add_column("local_paper_figures", sa.Column("document_version_id", uuid, nullable=True))
    op.add_column("local_paper_figures", sa.Column("section_id", uuid, nullable=True))
    op.add_column(
        "local_paper_figures",
        sa.Column("artifact_kind", sa.String(length=32), nullable=False, server_default="figure"),
    )
    op.add_column(
        "local_paper_figures",
        sa.Column(
            "reference_texts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "local_paper_figures", sa.Column("perceptual_hash", sa.String(length=128), nullable=True)
    )
    op.create_foreign_key(
        "fk_local_paper_figures_document_version",
        "local_paper_figures",
        "local_paper_document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_local_paper_figures_section",
        "local_paper_figures",
        "local_paper_sections",
        ["section_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_local_paper_figures_document_version_id", "local_paper_figures", ["document_version_id"]
    )
    op.create_index("ix_local_paper_figures_section_id", "local_paper_figures", ["section_id"])
    op.drop_constraint("uq_local_paper_figure", "local_paper_figures", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_figure_version",
        "local_paper_figures",
        ["document_version_id", "page_number", "figure_index"],
    )

    op.create_table(
        "local_paper_retrieval_runs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "library_id",
            uuid,
            sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index_version", sa.String(length=128), nullable=False),
        sa.Column(
            "request_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("trace_cache_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_local_paper_retrieval_runs_library_id", "local_paper_retrieval_runs", ["library_id"]
    )
    op.create_index(
        "ix_local_paper_retrieval_runs_owner_id", "local_paper_retrieval_runs", ["owner_id"]
    )


def downgrade() -> None:
    op.drop_table("local_paper_retrieval_runs")
    op.drop_constraint("uq_local_paper_figure_version", "local_paper_figures", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_figure", "local_paper_figures", ["paper_id", "page_number", "figure_index"]
    )
    op.drop_index("ix_local_paper_figures_section_id", table_name="local_paper_figures")
    op.drop_index("ix_local_paper_figures_document_version_id", table_name="local_paper_figures")
    op.drop_constraint("fk_local_paper_figures_section", "local_paper_figures", type_="foreignkey")
    op.drop_constraint(
        "fk_local_paper_figures_document_version", "local_paper_figures", type_="foreignkey"
    )
    op.drop_column("local_paper_figures", "perceptual_hash")
    op.drop_column("local_paper_figures", "reference_texts_json")
    op.drop_column("local_paper_figures", "artifact_kind")
    op.drop_column("local_paper_figures", "section_id")
    op.drop_column("local_paper_figures", "document_version_id")
    op.drop_table("local_paper_tables")
    op.drop_table("local_paper_chunk_locators")
    op.drop_constraint("uq_local_paper_chunk_content_version", "local_paper_chunks", type_="unique")
    op.drop_constraint("uq_local_paper_chunk_version", "local_paper_chunks", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_chunk", "local_paper_chunks", ["paper_id", "page_number", "chunk_index"]
    )
    op.create_unique_constraint(
        "uq_local_paper_chunk_content", "local_paper_chunks", ["paper_id", "content_sha256"]
    )
    op.drop_index("ix_local_paper_chunks_lexical_tsv", table_name="local_paper_chunks")
    op.drop_index("ix_local_paper_chunks_document_version_id", table_name="local_paper_chunks")
    op.drop_constraint(
        "fk_local_paper_chunks_document_version", "local_paper_chunks", type_="foreignkey"
    )
    op.drop_column("local_paper_chunks", "lexical_tsv")
    op.drop_column("local_paper_chunks", "lexical_terms")
    op.drop_column("local_paper_chunks", "embedding_text")
    op.drop_column("local_paper_chunks", "token_count")
    op.drop_column("local_paper_chunks", "document_version_id")
    op.drop_constraint("uq_local_paper_section_version", "local_paper_sections", type_="unique")
    op.create_unique_constraint(
        "uq_local_paper_section",
        "local_paper_sections",
        ["paper_id", "page_number", "section_index"],
    )
    op.drop_index("ix_local_paper_sections_section_type", table_name="local_paper_sections")
    op.drop_index("ix_local_paper_sections_document_version_id", table_name="local_paper_sections")
    op.drop_constraint(
        "fk_local_paper_sections_document_version", "local_paper_sections", type_="foreignkey"
    )
    op.drop_column("local_paper_sections", "token_count")
    op.drop_column("local_paper_sections", "page_end")
    op.drop_column("local_paper_sections", "section_type")
    op.drop_column("local_paper_sections", "heading_path_json")
    op.drop_column("local_paper_sections", "document_version_id")
    op.drop_constraint(
        "fk_local_papers_active_document_version", "local_papers", type_="foreignkey"
    )
    op.drop_index("ix_local_papers_active_document_version_id", table_name="local_papers")
    op.drop_column("local_papers", "active_document_version_id")
    op.drop_index(
        "uq_local_paper_active_document_version", table_name="local_paper_document_versions"
    )
    op.drop_table("local_paper_document_versions")

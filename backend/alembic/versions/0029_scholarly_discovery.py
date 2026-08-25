"""add auditable scholarly discovery and work versions

Revision ID: 0029_scholarly_discovery
Revises: 0028_literature_research_core
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0029_scholarly_discovery"
down_revision = "0028_literature_research_core"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    op.create_table(
        "research_search_queries",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query_id", sa.String(80), nullable=False),
        sa.Column("family", sa.String(80), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("query_json", JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "query_id", name="uq_research_query_run_query_id"),
    )
    op.create_index("ix_research_search_queries_run_id", "research_search_queries", ["run_id"])
    op.create_index("ix_research_search_queries_source", "research_search_queries", ["source"])

    op.create_table(
        "research_source_pages",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_query_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_search_queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("cursor_in", sa.Text(), nullable=True),
        sa.Column("cursor_out", sa.Text(), nullable=True),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("raw_object_key", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("response_etag", sa.Text(), nullable=True),
        sa.Column("response_last_modified", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "request_fingerprint", name="uq_source_page_run_fingerprint"),
    )
    op.create_index("ix_research_source_pages_run_id", "research_source_pages", ["run_id"])
    op.create_index(
        "ix_research_source_pages_search_query_id",
        "research_source_pages",
        ["search_query_id"],
    )
    op.create_index("ix_research_source_pages_source", "research_source_pages", ["source"])

    op.create_table(
        "research_source_failures",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query_id", sa.String(80), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_source_failures_run_id", "research_source_failures", ["run_id"])
    op.create_index("ix_research_source_failures_source", "research_source_failures", ["source"])

    op.create_table(
        "research_venues",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("venue_type", sa.String(32), nullable=False),
        sa.Column("issn_l", sa.String(32), nullable=True),
        sa.Column("issns_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("publisher", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("normalized_name", "venue_type", name="uq_research_venue_name_type"),
    )
    op.create_index("ix_research_venues_normalized_name", "research_venues", ["normalized_name"])
    op.create_index("ix_research_venues_issn_l", "research_venues", ["issn_l"])

    op.create_table(
        "research_works",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster_key", sa.String(64), nullable=False),
        sa.Column("canonical_title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(48), nullable=False),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("authors_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "field_provenance_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "duplicate_decisions_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("preferred_version_id", PG_UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "cluster_key", name="uq_research_work_run_cluster"),
    )
    op.create_index("ix_research_works_run_id", "research_works", ["run_id"])
    op.create_index("ix_research_works_normalized_title", "research_works", ["normalized_title"])

    op.create_table(
        "research_work_versions",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "venue_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_venues.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("version_type", sa.String(48), nullable=False),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("arxiv_id", sa.Text(), nullable=True),
        sa.Column("openalex_id", sa.Text(), nullable=True),
        sa.Column("semantic_scholar_id", sa.Text(), nullable=True),
        sa.Column("pmid", sa.Text(), nullable=True),
        sa.Column("published_online", sa.Date(), nullable=True),
        sa.Column("issued", sa.Date(), nullable=True),
        sa.Column("published_print", sa.Date(), nullable=True),
        sa.Column("preprint_first_posted", sa.Date(), nullable=True),
        sa.Column("accepted", sa.Date(), nullable=True),
        sa.Column("effective_publication_date", sa.Date(), nullable=True),
        sa.Column("effective_date_field", sa.String(64), nullable=True),
        sa.Column("effective_date_source", sa.String(32), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("open_access_pdf_url", sa.Text(), nullable=True),
        sa.Column("volume", sa.String(100), nullable=True),
        sa.Column("issue", sa.String(100), nullable=True),
        sa.Column("pages", sa.String(100), nullable=True),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("work_id", "source", "source_id", name="uq_work_version_source_record"),
    )
    op.create_index("ix_research_work_versions_work_id", "research_work_versions", ["work_id"])
    op.create_index("ix_research_work_versions_venue_id", "research_work_versions", ["venue_id"])
    op.create_index("ix_research_work_versions_source", "research_work_versions", ["source"])
    op.create_index("ix_research_work_versions_doi", "research_work_versions", ["doi"])
    op.create_index("ix_research_work_versions_arxiv_id", "research_work_versions", ["arxiv_id"])
    op.create_index(
        "ix_research_work_versions_openalex_id", "research_work_versions", ["openalex_id"]
    )
    op.create_index("ix_research_work_versions_pmid", "research_work_versions", ["pmid"])
    op.create_foreign_key(
        "fk_research_work_preferred_version",
        "research_works",
        "research_work_versions",
        ["preferred_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "research_source_records",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_page_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_source_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "source_page_id",
            "source",
            "source_id",
            name="uq_research_source_record_identity",
        ),
    )
    op.create_index("ix_research_source_records_run_id", "research_source_records", ["run_id"])
    op.create_index(
        "ix_research_source_records_source_page_id", "research_source_records", ["source_page_id"]
    )
    op.create_index(
        "ix_research_source_records_version_id", "research_source_records", ["version_id"]
    )
    op.create_index("ix_research_source_records_source", "research_source_records", ["source"])


def downgrade() -> None:
    op.drop_table("research_source_records")
    op.drop_constraint("fk_research_work_preferred_version", "research_works", type_="foreignkey")
    op.drop_table("research_work_versions")
    op.drop_table("research_works")
    op.drop_table("research_venues")
    op.drop_table("research_source_failures")
    op.drop_table("research_source_pages")
    op.drop_table("research_search_queries")

"""add relevance fulltext parsed blocks and evidence

Revision ID: 0031_relevance_fulltext_evidence
Revises: 0030_quality_constraint_ledger
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0031_relevance_fulltext_evidence"
down_revision = "0030_quality_constraint_ledger"
branch_labels = None
depends_on = None


def _id():
    return sa.Column(
        "id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _timestamps():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade() -> None:
    op.create_table(
        "research_relevance_scores",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lexical_score", sa.Float(), nullable=False),
        sa.Column("semantic_score", sa.Float(), nullable=True),
        sa.Column("cross_encoder_score", sa.Float(), nullable=True),
        sa.Column("facet_scores_json", JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("model_versions_json", JSONB(), nullable=False),
        sa.Column("reasons_json", JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "work_id", name="uq_relevance_run_work"),
    )
    op.create_index("ix_research_relevance_scores_run_id", "research_relevance_scores", ["run_id"])
    op.create_index(
        "ix_research_relevance_scores_decision", "research_relevance_scores", ["decision"]
    )
    op.create_table(
        "research_fulltext_acquisitions",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("license_decision", sa.String(16), nullable=False),
        sa.Column("license_reference", sa.Text(), nullable=True),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("document_sha256", sa.String(64), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_research_fulltext_acquisitions_run_id", "research_fulltext_acquisitions", ["run_id"]
    )
    op.create_index(
        "ix_research_fulltext_acquisitions_version_id",
        "research_fulltext_acquisitions",
        ["version_id"],
    )
    op.create_table(
        "research_parsed_blocks",
        _id(),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_id", sa.String(100), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_path_json", JSONB(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("version_id", "block_id", name="uq_parsed_block_version_id"),
    )
    op.create_index("ix_research_parsed_blocks_run_id", "research_parsed_blocks", ["run_id"])
    op.create_index(
        "ix_research_parsed_blocks_version_id", "research_parsed_blocks", ["version_id"]
    )
    op.create_table(
        "research_evidence_locators",
        _id(),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("block_id", sa.String(100), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_path_json", JSONB(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("block_text_sha256", sa.String(64), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "evidence_id", name="uq_evidence_run_id"),
    )
    op.create_index(
        "ix_research_evidence_locators_run_id", "research_evidence_locators", ["run_id"]
    )
    op.create_index(
        "ix_research_evidence_locators_work_id", "research_evidence_locators", ["work_id"]
    )


def downgrade() -> None:
    op.drop_table("research_evidence_locators")
    op.drop_table("research_parsed_blocks")
    op.drop_table("research_fulltext_acquisitions")
    op.drop_table("research_relevance_scores")

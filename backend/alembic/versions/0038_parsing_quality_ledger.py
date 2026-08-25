"""add parsing quality ledger and block coordinates

Revision ID: 0038_parsing_quality
Revises: 0037_document_safety
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0038_parsing_quality"
down_revision = "0037_document_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_parsed_blocks", sa.Column("bbox_json", JSONB(), nullable=True))
    op.add_column(
        "research_parsed_blocks",
        sa.Column(
            "extraction_method",
            sa.String(24),
            nullable=False,
            server_default="native",
        ),
    )
    op.add_column("research_evidence_locators", sa.Column("bbox_json", JSONB(), nullable=True))
    op.add_column(
        "research_evidence_locators",
        sa.Column(
            "extraction_method",
            sa.String(24),
            nullable=False,
            server_default="native",
        ),
    )
    op.create_table(
        "research_parsing_results",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("parsed_page_count", sa.Integer(), nullable=False),
        sa.Column("text_coverage", sa.Float(), nullable=False),
        sa.Column("page_count_match", sa.Boolean(), nullable=False),
        sa.Column("section_detection_f1_estimate", sa.Float(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("figure_count", sa.Integer(), nullable=False),
        sa.Column("caption_count", sa.Integer(), nullable=False),
        sa.Column("caption_link_rate", sa.Float(), nullable=False),
        sa.Column("ocr_page_count", sa.Integer(), nullable=False),
        sa.Column("ocr_page_ratio", sa.Float(), nullable=False),
        sa.Column("total_characters", sa.Integer(), nullable=False),
        sa.Column("parser_versions_json", JSONB(), nullable=False),
        sa.Column("error_codes_json", JSONB(), nullable=False),
        sa.Column("blocks_object_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["research_work_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "version_id", name="uq_parsing_result_run_version"),
    )
    op.create_index(
        "ix_research_parsing_results_run_id",
        "research_parsing_results",
        ["run_id"],
    )
    op.create_index(
        "ix_research_parsing_results_version_id",
        "research_parsing_results",
        ["version_id"],
    )
    op.create_index(
        "ix_research_parsing_results_status",
        "research_parsing_results",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_parsing_results_status", table_name="research_parsing_results")
    op.drop_index(
        "ix_research_parsing_results_version_id",
        table_name="research_parsing_results",
    )
    op.drop_index("ix_research_parsing_results_run_id", table_name="research_parsing_results")
    op.drop_table("research_parsing_results")
    op.drop_column("research_evidence_locators", "extraction_method")
    op.drop_column("research_evidence_locators", "bbox_json")
    op.drop_column("research_parsed_blocks", "extraction_method")
    op.drop_column("research_parsed_blocks", "bbox_json")

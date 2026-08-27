"""add resumable local-paper analysis stages

Revision ID: 0052_local_paper_analysis_stages
Revises: 0051_local_paper_analysis_jobs
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "0052_local_paper_analysis_stages"
down_revision = "0051_local_paper_analysis_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.add_column(
        "local_paper_analysis_jobs",
        sa.Column("execution_mode", sa.String(length=16), nullable=False, server_default="staged"),
    )
    op.add_column(
        "local_paper_analysis_jobs",
        sa.Column("stage", sa.String(length=48), nullable=False, server_default="QUEUED"),
    )
    op.add_column(
        "local_paper_analysis_jobs",
        sa.Column("stage_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "local_paper_analysis_jobs",
        sa.Column("stage_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "local_paper_analysis_jobs", sa.Column("provider_status", sa.String(length=32), nullable=True)
    )
    op.create_table(
        "local_paper_analysis_stages",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("job_id", uuid, sa.ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", uuid, sa.ForeignKey("local_papers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stage_type", sa.String(length=24), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("result_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("normalized_error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "stage_type", "stage_index", name="uq_local_paper_analysis_stage"),
    )
    for field in ["job_id", "paper_id", "status", "provider_response_id"]:
        op.create_index(f"ix_local_paper_analysis_stages_{field}", "local_paper_analysis_stages", [field])
    for field, foreign_table in [("stage_id", "local_paper_analysis_stages"), ("paper_id", "local_papers")]:
        op.add_column(
            "local_paper_analysis_llm_attempts",
            sa.Column(field, uuid, sa.ForeignKey(f"{foreign_table}.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index(f"ix_local_paper_analysis_llm_attempts_{field}", "local_paper_analysis_llm_attempts", [field])
    op.add_column("local_paper_analysis_llm_attempts", sa.Column("normalized_error_code", sa.String(length=64), nullable=True))
    op.add_column("local_paper_analysis_llm_attempts", sa.Column("endpoint_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("local_paper_analysis_llm_attempts", "endpoint_hash")
    op.drop_column("local_paper_analysis_llm_attempts", "normalized_error_code")
    for field in ["paper_id", "stage_id"]:
        op.drop_index(f"ix_local_paper_analysis_llm_attempts_{field}", table_name="local_paper_analysis_llm_attempts")
        op.drop_column("local_paper_analysis_llm_attempts", field)
    op.drop_table("local_paper_analysis_stages")
    for field in ["provider_status", "stage_total", "stage_index", "stage", "execution_mode"]:
        op.drop_column("local_paper_analysis_jobs", field)

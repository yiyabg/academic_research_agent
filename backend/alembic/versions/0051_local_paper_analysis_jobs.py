"""add durable local-paper analysis, audit and explicit project grants

Revision ID: 0051_local_paper_analysis_jobs
Revises: 0050_local_paper_v7
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0051_local_paper_analysis_jobs"
down_revision = "0050_local_paper_v7"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "local_paper_library_project_grants",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("granted_by", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("permission", sa.String(length=24), nullable=False, server_default="ANALYZE"),
        *_timestamps(),
        sa.UniqueConstraint("library_id", "project_id", name="uq_local_paper_library_project_grant"),
    )
    op.create_index("ix_local_paper_library_project_grants_library_id", "local_paper_library_project_grants", ["library_id"])
    op.create_index("ix_local_paper_library_project_grants_project_id", "local_paper_library_project_grants", ["project_id"])
    op.create_table(
        "local_paper_analysis_sessions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("research_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="本地文献分析"),
        sa.Column("summary_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("audit_head_hash", sa.String(length=64), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    for field in ["library_id", "owner_id", "project_id", "is_archived"]:
        op.create_index(f"ix_local_paper_analysis_sessions_{field}", "local_paper_analysis_sessions", [field])
    op.create_table(
        "local_paper_analysis_jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("session_id", uuid, sa.ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("library_id", uuid, sa.ForeignKey("local_paper_libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", uuid, sa.ForeignKey("research_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="FOCUSED"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("request_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("retrieval_run_id", uuid, sa.ForeignKey("local_paper_retrieval_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_versions_json", jsonb, nullable=False, server_default="[]"),
        sa.Column("evidence_json", jsonb, nullable=False, server_default="[]"),
        sa.Column("result_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("artifact_key", sa.Text(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_timestamps(),
    )
    for field in ["session_id", "library_id", "owner_id", "project_id", "status", "retrieval_run_id"]:
        op.create_index(f"ix_local_paper_analysis_jobs_{field}", "local_paper_analysis_jobs", [field])
    op.create_index(
        "uq_local_paper_analysis_job_owner_idempotency",
        "local_paper_analysis_jobs", ["owner_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "local_paper_analysis_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("job_id", uuid, sa.ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("payload_json", jsonb, nullable=False, server_default="{}"),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("job_id", "sequence", name="uq_local_paper_analysis_event_sequence"),
    )
    op.create_index("ix_local_paper_analysis_events_job_id", "local_paper_analysis_events", ["job_id"])
    op.create_table(
        "local_paper_analysis_turns",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("session_id", uuid, sa.ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("assistant_output", sa.Text(), nullable=True),
        sa.Column("evidence_manifest_json", jsonb, nullable=False, server_default="[]"),
        sa.Column("metadata_json", jsonb, nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_local_paper_analysis_turns_session_id", "local_paper_analysis_turns", ["session_id"])
    op.create_index("ix_local_paper_analysis_turns_job_id", "local_paper_analysis_turns", ["job_id"])
    op.create_table(
        "local_paper_analysis_llm_attempts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("job_id", uuid, sa.ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_local_paper_analysis_llm_attempts_job_id", "local_paper_analysis_llm_attempts", ["job_id"])
    op.create_table(
        "local_paper_memory_candidates",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("owner_id", uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", uuid, sa.ForeignKey("local_paper_analysis_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("local_paper_analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_json", jsonb, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("confirmed_profile_id", uuid, sa.ForeignKey("user_research_profiles.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
    )
    for field in ["owner_id", "session_id", "job_id", "status"]:
        op.create_index(f"ix_local_paper_memory_candidates_{field}", "local_paper_memory_candidates", [field])


def downgrade() -> None:
    for table in [
        "local_paper_memory_candidates", "local_paper_analysis_llm_attempts", "local_paper_analysis_turns",
        "local_paper_analysis_events", "local_paper_analysis_jobs", "local_paper_analysis_sessions",
        "local_paper_library_project_grants",
    ]:
        op.drop_table(table)

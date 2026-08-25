"""create literature research protocol and run core

Revision ID: 0028_literature_research_core
Revises: 0027_user_magic_link_epoch
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0028_literature_research_core"
down_revision = "0027_user_magic_link_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_projects",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_projects_owner_id", "research_projects", ["owner_id"])
    op.create_index(
        "ix_research_projects_organization_id", "research_projects", ["organization_id"]
    )
    op.create_index("ix_research_projects_status", "research_projects", ["status"])

    op.create_table(
        "research_protocol_versions",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("protocol_json", JSONB(), nullable=False),
        sa.Column("protocol_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_protocol_project_version"),
        sa.UniqueConstraint("project_id", "protocol_hash", name="uq_protocol_project_hash"),
    )
    op.create_index(
        "ix_research_protocol_versions_project_id",
        "research_protocol_versions",
        ["project_id"],
    )
    op.create_index(
        "ix_research_protocol_versions_status", "research_protocol_versions", ["status"]
    )

    op.create_table(
        "research_runs",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "protocol_version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_protocol_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(48), nullable=False, server_default="QUEUED"),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_mode", sa.String(24), nullable=False, server_default="full_research"),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("protocol_hash", sa.String(71), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("strict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analyzed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("shortage_report_json", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_code", sa.String(100), nullable=True),
        sa.Column("failed_detail", JSONB(), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner_id", "client_request_id", name="uq_run_owner_client_request"),
    )
    op.create_index("ix_research_runs_project_id", "research_runs", ["project_id"])
    op.create_index(
        "ix_research_runs_protocol_version_id", "research_runs", ["protocol_version_id"]
    )
    op.create_index("ix_research_runs_owner_id", "research_runs", ["owner_id"])
    op.create_index("ix_research_runs_organization_id", "research_runs", ["organization_id"])
    op.create_index("ix_research_runs_state", "research_runs", ["state"])
    op.create_index("ix_run_state_updated", "research_runs", ["state", "updated_at"])

    op.create_table(
        "research_task_executions",
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
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("shard_key", sa.String(255), nullable=False, server_default="main"),
        sa.Column("input_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_json", JSONB(), nullable=True),
        sa.Column("error_json", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "run_id", "stage", "shard_key", "input_hash", name="uq_task_idempotency"
        ),
    )
    op.create_index("ix_research_task_executions_run_id", "research_task_executions", ["run_id"])

    op.create_table(
        "research_outbox_events",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "event_id",
            PG_UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "sequence", name="uq_research_event_run_sequence"),
        sa.UniqueConstraint("event_id", name="uq_research_event_id"),
    )
    op.create_index("ix_research_outbox_events_run_id", "research_outbox_events", ["run_id"])
    op.create_index(
        "ix_research_outbox_events_event_type", "research_outbox_events", ["event_type"]
    )


def downgrade() -> None:
    op.drop_table("research_outbox_events")
    op.drop_table("research_task_executions")
    op.drop_index("ix_run_state_updated", table_name="research_runs")
    op.drop_table("research_runs")
    op.drop_table("research_protocol_versions")
    op.drop_table("research_projects")

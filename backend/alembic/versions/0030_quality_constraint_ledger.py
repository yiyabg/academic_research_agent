"""add authorized metrics and constraint ledger

Revision ID: 0030_quality_constraint_ledger
Revises: 0029_scholarly_discovery
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0030_quality_constraint_ledger"
down_revision = "0029_scholarly_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_metric_snapshots",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("metric_names_json", JSONB(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("license_reference", sa.Text(), nullable=False),
        sa.Column("authorized_scope", sa.Text(), nullable=False),
        sa.Column("license_attested", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "imported_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source_name", "source_version", "payload_sha256", name="uq_metric_snapshot_payload"
        ),
    )
    op.create_index(
        "ix_research_metric_snapshots_source_name", "research_metric_snapshots", ["source_name"]
    )
    op.create_index("ix_research_metric_snapshots_status", "research_metric_snapshots", ["status"])

    op.create_table(
        "research_venue_metric_facts",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "snapshot_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_metric_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "venue_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_venues.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("venue_name", sa.Text(), nullable=False),
        sa.Column("venue_normalized_name", sa.Text(), nullable=False),
        sa.Column("venue_type", sa.String(32), nullable=False),
        sa.Column("issn_l", sa.String(32), nullable=True),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_value", JSONB(), nullable=False),
        sa.Column("metric_year", sa.Integer(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "venue_normalized_name",
            "venue_type",
            "metric_name",
            "metric_year",
            name="uq_metric_fact_identity",
        ),
    )
    for column in ("snapshot_id", "venue_id", "venue_normalized_name", "issn_l", "metric_name"):
        op.create_index(
            f"ix_research_venue_metric_facts_{column}", "research_venue_metric_facts", [column]
        )

    op.create_table(
        "research_work_eligibility",
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
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("protocol_hash", sa.String(71), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("hard_pass_count", sa.Integer(), nullable=False),
        sa.Column("hard_fail_count", sa.Integer(), nullable=False),
        sa.Column("hard_unknown_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "work_id", name="uq_work_eligibility_run_work"),
    )
    op.create_index(
        "ix_research_work_eligibility_eligible", "research_work_eligibility", ["eligible"]
    )
    op.create_index(
        "ix_work_eligibility_run_work", "research_work_eligibility", ["run_id", "work_id"]
    )

    op.create_table(
        "research_constraint_evaluations",
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
            "work_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_work_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("protocol_hash", sa.String(71), nullable=False),
        sa.Column("constraint_id", sa.String(100), nullable=False),
        sa.Column("field", sa.String(200), nullable=False),
        sa.Column("operator", sa.String(32), nullable=False),
        sa.Column("expected_value", JSONB(), nullable=True),
        sa.Column("observed_value", JSONB(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("evidence_reference", sa.Text(), nullable=True),
        sa.Column(
            "metric_snapshot_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("research_metric_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id", "work_id", "constraint_id", name="uq_constraint_ledger_entry"
        ),
    )
    op.create_index(
        "ix_research_constraint_evaluations_decision",
        "research_constraint_evaluations",
        ["decision"],
    )
    op.create_index(
        "ix_research_constraint_evaluations_reason_code",
        "research_constraint_evaluations",
        ["reason_code"],
    )
    op.create_index(
        "ix_constraint_ledger_run_work", "research_constraint_evaluations", ["run_id", "work_id"]
    )


def downgrade() -> None:
    op.drop_table("research_constraint_evaluations")
    op.drop_table("research_work_eligibility")
    op.drop_table("research_venue_metric_facts")
    op.drop_table("research_metric_snapshots")

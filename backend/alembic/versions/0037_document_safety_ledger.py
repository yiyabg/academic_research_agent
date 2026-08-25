"""add fail-closed full-text safety ledger

Revision ID: 0037_document_safety
Revises: 0036_versioned_outputs
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0037_document_safety"
down_revision = "0036_versioned_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column(
            "resolved_ips_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column(
            "redirect_chain_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column(
            "malware_scan_status",
            sa.String(24),
            nullable=False,
            server_default="NOT_SCANNED",
        ),
    )
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column("malware_scan_engine", sa.String(64), nullable=True),
    )
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column("malware_signature", sa.String(255), nullable=True),
    )
    op.add_column(
        "research_fulltext_acquisitions",
        sa.Column("malware_scanned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_fulltext_acquisitions", "malware_scanned_at")
    op.drop_column("research_fulltext_acquisitions", "malware_signature")
    op.drop_column("research_fulltext_acquisitions", "malware_scan_engine")
    op.drop_column("research_fulltext_acquisitions", "malware_scan_status")
    op.drop_column("research_fulltext_acquisitions", "redirect_chain_json")
    op.drop_column("research_fulltext_acquisitions", "resolved_ips_json")

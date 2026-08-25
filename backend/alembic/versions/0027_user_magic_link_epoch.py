"""add users.magic_link_epoch — skipped (enable_email=false)

Revision ID: 0027_user_magic_link_epoch

No-op placeholder so the revision chain stays linear when email (and therefore
magic-link sign-in) is disabled.
"""

revision = "0027_user_magic_link_epoch"
down_revision = "0026_create_mcp_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

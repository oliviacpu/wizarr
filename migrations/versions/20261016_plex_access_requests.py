"""Opt-in public Plex requests and account-bound invitations."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_plex_access_requests"
down_revision = "20261011_ldap_user_sync_filter"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invitation") as batch_op:
        batch_op.add_column(sa.Column("plex_account_id", sa.String(32), nullable=True))
    op.create_table(
        "access_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entered_identifier", sa.String(254), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("plex_account_id", sa.String(32), nullable=False, unique=True),
        sa.Column("plex_username", sa.String(254), nullable=False),
        sa.Column("plex_email", sa.String(254), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "invitation_id",
            sa.Integer(),
            sa.ForeignKey("invitation.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("approved_scope", sa.JSON(), nullable=True),
        sa.Column("redemption_stage", sa.String(24), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_access_request_status", "access_request", ["status"])


def downgrade():
    # Older code cannot enforce the account binding. Do not turn these into
    # ordinary bearer invitations when their binding column is removed.
    op.execute(
        sa.text(
            "UPDATE invitation SET used=1, unlimited=0, expires=CURRENT_TIMESTAMP WHERE plex_account_id IS NOT NULL"
        )
    )
    op.drop_table("access_request")
    with op.batch_alter_table("invitation") as batch_op:
        batch_op.drop_column("plex_account_id")

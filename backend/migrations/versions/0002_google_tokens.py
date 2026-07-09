"""google token fields on users: token_expiry, token_scopes, auth_status

Revision ID: 0002_google_tokens
Revises: 0001_initial
Create Date: 2024-01-02 00:00:00

Phase 2 (docs/implement_providers.md §9). ``google_access_token`` /
``google_refresh_token`` and ``sync_status.cursor`` already exist in
``0001_initial``; this revision adds only the three new ``user`` columns. No
vector changes.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_google_tokens"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user", sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("user", sa.Column("token_scopes", postgresql.JSONB(), nullable=True))
    op.add_column(
        "user",
        sa.Column("auth_status", sa.String(), nullable=True, server_default="valid"),
    )


def downgrade() -> None:
    op.drop_column("user", "auth_status")
    op.drop_column("user", "token_scopes")
    op.drop_column("user", "token_expiry")

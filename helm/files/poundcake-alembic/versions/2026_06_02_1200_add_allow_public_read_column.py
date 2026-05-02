""""Add allow_public_read column to adapter_credentials for credential-managed
policy gating.

Revision ID: 2026_06_02_1200
Revises: 2026_05_22_1200
Create Date: 2026-06-02 12:00:00.000000

The ``allow_public_read`` column is owned by the credential manager and
controls whether an adapter credential permits unauthenticated public read
endpoints (e.g. ``raw.githubusercontent.com``) when no token is configured.
Default is ``false``; operators/bootstrap must explicitly set it to ``true``
when writing credentials that intend to use public read paths.

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2026_06_02_1200"
down_revision = "2026_05_22_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "adapter_credentials",
        sa.Column("allow_public_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("adapter_credentials", "allow_public_read")

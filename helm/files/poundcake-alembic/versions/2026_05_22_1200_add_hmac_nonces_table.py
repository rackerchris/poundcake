"""Add hmac_nonces table for shared replay protection.

Revision ID: 2026_05_22_1200
Revises: 2026_05_20_0900
Create Date: 2026-05-22 12:00:00.000000

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "2026_05_22_1200"
down_revision = "2026_05_20_0900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hmac_nonces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "key", name="ux_hmac_nonces_kind_key"),
    )
    op.create_index(op.f("ix_hmac_nonces_id"), "hmac_nonces", ["id"], unique=False)
    op.create_index(
        op.f("ix_hmac_nonces_kind_expires_at"),
        "hmac_nonces",
        ["kind", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_hmac_nonces_kind_expires_at"), table_name="hmac_nonces")
    op.drop_index(op.f("ix_hmac_nonces_id"), table_name="hmac_nonces")
    op.drop_table("hmac_nonces")

"""Split adapter credentials from service identity credentials.

Revision ID: 2026_05_18_1200
Revises: 2026_02_03_1600
Create Date: 2026-05-18 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2026_05_18_1200"
down_revision = "2026_02_03_1600"
branch_labels = None
depends_on = None


def _credential_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_plugin_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(length=64), nullable=False),
        sa.Column("credential_key_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "adapter_credentials",
        *_credential_columns(),
        sa.CheckConstraint("credential_type <> ''", name="ck_adapter_credentials_type_present"),
        sa.CheckConstraint("credential_key_id <> ''", name="ck_adapter_credentials_key_present"),
        sa.ForeignKeyConstraint(["service_plugin_id"], ["service_plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_plugin_id",
            "credential_type",
            "credential_key_id",
            name="ux_adapter_credentials_identity",
        ),
    )
    op.create_index(
        "ix_adapter_credentials_id",
        "adapter_credentials",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_adapter_credentials_service_plugin_id",
        "adapter_credentials",
        ["service_plugin_id"],
        unique=False,
    )

    op.create_table(
        "service_identity_credentials",
        *_credential_columns(),
        sa.CheckConstraint(
            "credential_type <> ''",
            name="ck_service_identity_credentials_type_present",
        ),
        sa.CheckConstraint(
            "credential_key_id <> ''",
            name="ck_service_identity_credentials_key_present",
        ),
        sa.ForeignKeyConstraint(["service_plugin_id"], ["service_plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_plugin_id",
            "credential_type",
            "credential_key_id",
            name="ux_service_identity_credentials_identity",
        ),
    )
    op.create_index(
        "ix_service_identity_credentials_id",
        "service_identity_credentials",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_service_identity_credentials_key_id",
        "service_identity_credentials",
        ["credential_key_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_identity_credentials_service_plugin_id",
        "service_identity_credentials",
        ["service_plugin_id"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO adapter_credentials (
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        )
        SELECT
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        FROM service_plugin_credentials
        WHERE credential_type <> 'internal_control_plane_hmac'
        """
    )
    op.execute(
        """
        INSERT INTO service_identity_credentials (
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        )
        SELECT
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        FROM service_plugin_credentials
        WHERE credential_type = 'internal_control_plane_hmac'
        """
    )

    op.drop_index(
        "ix_service_plugin_credentials_service_plugin_id",
        table_name="service_plugin_credentials",
    )
    op.drop_index("ix_service_plugin_credentials_id", table_name="service_plugin_credentials")
    op.drop_table("service_plugin_credentials")


def downgrade() -> None:
    op.create_table(
        "service_plugin_credentials",
        *_credential_columns(),
        sa.CheckConstraint("credential_type <> ''", name="ck_sp_credentials_type_present"),
        sa.CheckConstraint("credential_key_id <> ''", name="ck_sp_credentials_key_present"),
        sa.ForeignKeyConstraint(["service_plugin_id"], ["service_plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_plugin_id",
            "credential_type",
            "credential_key_id",
            name="ux_service_plugin_credentials_identity",
        ),
    )
    op.create_index(
        "ix_service_plugin_credentials_id",
        "service_plugin_credentials",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugin_credentials_service_plugin_id",
        "service_plugin_credentials",
        ["service_plugin_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO service_plugin_credentials (
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        )
        SELECT
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        FROM adapter_credentials
        """
    )
    op.execute(
        """
        INSERT INTO service_plugin_credentials (
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        )
        SELECT
            id, service_plugin_id, credential_type, credential_key_id,
            encrypted_payload, created_at, updated_at
        FROM service_identity_credentials
        """
    )
    op.drop_index(
        "ix_service_identity_credentials_service_plugin_id",
        table_name="service_identity_credentials",
    )
    op.drop_index(
        "ix_service_identity_credentials_key_id",
        table_name="service_identity_credentials",
    )
    op.drop_index("ix_service_identity_credentials_id", table_name="service_identity_credentials")
    op.drop_table("service_identity_credentials")
    op.drop_index("ix_adapter_credentials_service_plugin_id", table_name="adapter_credentials")
    op.drop_index("ix_adapter_credentials_id", table_name="adapter_credentials")
    op.drop_table("adapter_credentials")

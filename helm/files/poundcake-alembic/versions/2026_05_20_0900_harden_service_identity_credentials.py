"""Harden service identity credential boundaries.

Revision ID: 2026_05_20_0900
Revises: 2026_05_18_1200
Create Date: 2026-05-20 09:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2026_05_20_0900"
down_revision = "2026_05_18_1200"
branch_labels = None
depends_on = None

SERVICE_IDENTITY_VIEWS = {
    "prep-chef": "service_identity_credentials_prep_chef",
    "expediter-runner": "service_identity_credentials_expediter_runner",
    "timer": "service_identity_credentials_timer",
    "dishwasher": "service_identity_credentials_dishwasher",
}


def _create_service_identity_views() -> None:
    for service_type, view_name in SERVICE_IDENTITY_VIEWS.items():
        op.execute(
            f"""
            CREATE VIEW {view_name} AS
            SELECT
                sic.id,
                sic.service_plugin_id,
                sic.credential_type,
                sic.credential_key_id,
                sic.encrypted_payload,
                sic.created_at,
                sic.updated_at
            FROM service_identity_credentials sic
            JOIN service_plugins sp ON sp.id = sic.service_plugin_id
            WHERE sp.service_type = '{service_type}'
              AND sp.plugin_type = 'internal_plugin'
              AND sic.credential_type = 'internal_control_plane_hmac'
            """
        )


def _drop_service_identity_views() -> None:
    for view_name in SERVICE_IDENTITY_VIEWS.values():
        op.execute(sa.text(f"DROP VIEW IF EXISTS {view_name}"))


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM service_identity_credentials
            WHERE credential_type = 'internal_control_plane_hmac'
              AND service_plugin_id IN (
                  SELECT id
                  FROM service_plugins
                  WHERE COALESCE(plugin_type, '') <> 'internal_plugin'
              )
            """
        )
    )
    duplicates = bind.execute(
        sa.text(
            """
            SELECT credential_type, credential_key_id, COUNT(*) AS row_count
            FROM service_identity_credentials
            GROUP BY credential_type, credential_key_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        details = ", ".join(
            f"{row.credential_type}/{row.credential_key_id}={row.row_count}"
            for row in duplicates
        )
        raise RuntimeError(
            "duplicate service identity credential key ids must be resolved before migration: "
            f"{details}"
        )
    with op.batch_alter_table("service_identity_credentials") as batch_op:
        batch_op.create_unique_constraint(
            "ux_service_identity_credentials_key_identity",
            ["credential_type", "credential_key_id"],
        )
    _create_service_identity_views()


def downgrade() -> None:
    _drop_service_identity_views()
    with op.batch_alter_table("service_identity_credentials") as batch_op:
        batch_op.drop_constraint(
            "ux_service_identity_credentials_key_identity",
            type_="unique",
        )

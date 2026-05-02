#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""initial_schema

Revision ID: 2026_02_03_1600
Revises:
Create Date: 2026-02-03 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "2026_02_03_1600"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Recipes
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("clear_timeout_sec", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recipes_id"), "recipes", ["id"], unique=False)
    op.create_index(op.f("ix_recipes_name"), "recipes", ["name"], unique=True)

    # Ingredients (global)
    op.create_table(
        "ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_exec", sa.String(length=100), nullable=False),
        sa.Column("destination_target", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("task_key_template", sa.String(length=255), nullable=False),
        sa.Column("service_payload_template", mysql.JSON(), nullable=True),
        sa.Column("service_exec_parameters", mysql.JSON(), nullable=True),
        sa.Column("payload_schema", mysql.JSON(), nullable=False),
        sa.Column("service_exec_expected_outcome_default", mysql.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("service_type", sa.String(length=50), nullable=False, server_default="undefined"),
        sa.Column(
            "ingredient_purpose", sa.String(length=32), nullable=False, server_default="utility"
        ),
        sa.Column("is_blocking", sa.Boolean(), nullable=False),
        sa.Column("default_expected_secs", sa.Integer(), nullable=False),
        sa.Column("default_timeout", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("retry_delay", sa.Integer(), nullable=False),
        sa.Column("on_failure", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("service_type <> ''", name="ck_ingredients_service_type_present"),
        sa.CheckConstraint("service_exec <> ''", name="ck_ingredients_service_exec_present"),
        sa.CheckConstraint(
            "ingredient_purpose in ('remediation','comms','utility','plugin_health','suppression_sync','suppression_lifecycle')",
            name="ck_ingredients_ingredient_purpose",
        ),
        sa.CheckConstraint(
            "on_failure in ('continue','stop','retry')",
            name="ck_ingredients_on_failure",
        ),
        sa.CheckConstraint(
            "default_expected_secs > 0",
            name="ck_ingredients_default_expected_secs_positive",
        ),
        sa.CheckConstraint("default_timeout > 0", name="ck_ingredients_default_timeout_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ingredients_id"), "ingredients", ["id"], unique=False)
    op.create_index(
        op.f("ix_ingredients_service_exec"), "ingredients", ["service_exec"], unique=False
    )
    op.create_index(
        "idx_ingredients_service_template",
        "ingredients",
        ["service_type", "service_exec", "destination_target", "task_key_template"],
        unique=False,
    )

    op.create_table(
        "service_plugins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_type", sa.String(length=50), nullable=False),
        sa.Column("plugin_short_id", sa.String(length=12), nullable=False),
        sa.Column(
            "plugin_type",
            sa.String(length=32),
            nullable=False,
            server_default="external_plugin",
        ),
        sa.Column(
            "plugin_tier",
            sa.String(length=32),
            nullable=False,
            server_default="community",
        ),
        sa.Column("plugin_log_key", sa.String(length=32), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("run_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("query_limit", sa.Integer(), nullable=True),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("plugin_config", mysql.JSON(), nullable=True),
        sa.Column("capabilities_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "registered_ingredient_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "registered_recipe_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "credential_status", sa.String(length=32), nullable=False, server_default="unknown"
        ),
        sa.Column("credential_error", sa.Text(), nullable=True),
        sa.Column("last_credential_bootstrap_at", sa.DateTime(), nullable=True),
        sa.Column("last_credential_rotation_at", sa.DateTime(), nullable=True),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("health_message", sa.Text(), nullable=True),
        sa.Column("health_error_code", sa.String(length=100), nullable=True),
        sa.Column("health_latency_ms", sa.Integer(), nullable=True),
        sa.Column("health_details", mysql.JSON(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_health_check_at", sa.DateTime(), nullable=True),
        sa.Column("next_health_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column(
            "health_check_state", sa.String(length=32), nullable=False, server_default="idle"
        ),
        sa.Column("health_check_order_id", sa.Integer(), nullable=True),
        sa.Column("health_check_started_at", sa.DateTime(), nullable=True),
        sa.Column("health_check_grace_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("service_type <> ''", name="ck_service_plugins_service_type_present"),
        sa.CheckConstraint("plugin_short_id <> ''", name="ck_service_plugins_short_id_present"),
        sa.CheckConstraint(
            "plugin_type in ('internal_plugin','external_plugin')",
            name="ck_service_plugins_plugin_type",
        ),
        sa.CheckConstraint(
            "plugin_tier in ('community','supported')",
            name="ck_service_plugins_plugin_tier",
        ),
        sa.CheckConstraint(
            "plugin_log_key is null or plugin_log_key <> ''",
            name="ck_service_plugins_plugin_log_key_present",
        ),
        sa.CheckConstraint(
            "health_status in ('unknown','initializing','healthy','degraded','failed','disabled')",
            name="ck_service_plugins_health_status",
        ),
        sa.CheckConstraint(
            "health_check_state in ('idle','queued','running')",
            name="ck_service_plugins_health_check_state",
        ),
        sa.CheckConstraint(
            "query_limit is null or query_limit > 0",
            name="ck_service_plugins_query_limit_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plugin_log_key", name="ux_service_plugins_plugin_log_key"),
        sa.UniqueConstraint("plugin_short_id", name="ux_service_plugins_plugin_short_id"),
        sa.UniqueConstraint("service_type", name="ux_service_plugins_service_type"),
    )
    op.create_index("ix_service_plugins_id", "service_plugins", ["id"], unique=False)
    op.create_index("ix_service_plugins_enabled", "service_plugins", ["enabled"], unique=False)
    op.create_index(
        "ix_service_plugins_plugin_type",
        "service_plugins",
        ["plugin_type"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugins_plugin_short_id",
        "service_plugins",
        ["plugin_short_id"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugins_plugin_tier",
        "service_plugins",
        ["plugin_tier"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugins_health_status",
        "service_plugins",
        ["health_status"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugins_next_health_check_at",
        "service_plugins",
        ["next_health_check_at"],
        unique=False,
    )
    op.create_index(
        "ix_service_plugins_health_check_order_id",
        "service_plugins",
        ["health_check_order_id"],
        unique=False,
    )

    op.create_table(
        "service_plugin_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_plugin_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(length=64), nullable=False),
        sa.Column("credential_key_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
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

    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(length=255), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("service_type", sa.String(length=50), nullable=True),
        sa.Column("service_exec", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="registered"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "run_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("300"),
        ),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("task_payload", mysql.JSON(), nullable=True),
        sa.Column("task_parameters", mysql.JSON(), nullable=True),
        sa.Column("expected_outcome", mysql.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="idle"),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("last_order_id", sa.Integer(), nullable=True),
        sa.Column("last_order_req_id", sa.String(length=100), nullable=True),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("task_key <> ''", name="ck_scheduled_tasks_task_key_present"),
        sa.CheckConstraint("task_type <> ''", name="ck_scheduled_tasks_task_type_present"),
        sa.CheckConstraint("run_interval_seconds > 0", name="ck_scheduled_tasks_interval_positive"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_scheduled_tasks_timeout_positive"),
        sa.CheckConstraint(
            "source in ('core','plugin_manifest','registered')",
            name="ck_scheduled_tasks_source",
        ),
        sa.CheckConstraint(
            "task_type in ('plugin_health_check','service_execution')",
            name="ck_scheduled_tasks_task_type",
        ),
        sa.CheckConstraint(
            "status in ('idle','queued','running','disabled')",
            name="ck_scheduled_tasks_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_key", name="ux_scheduled_tasks_task_key"),
    )
    op.create_index("ix_scheduled_tasks_id", "scheduled_tasks", ["id"], unique=False)
    op.create_index(
        "ix_scheduled_tasks_enabled_next_run",
        "scheduled_tasks",
        ["is_enabled", "next_run_at"],
        unique=False,
    )
    op.create_index("ix_scheduled_tasks_status", "scheduled_tasks", ["status"], unique=False)
    op.create_index(
        "ix_scheduled_tasks_service_type", "scheduled_tasks", ["service_type"], unique=False
    )
    op.create_index("ix_scheduled_tasks_task_type", "scheduled_tasks", ["task_type"], unique=False)

    # Recipe Ingredients (junction)
    op.create_table(
        "recipe_ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("ingredient_id", sa.Integer(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("on_success", sa.String(length=50), nullable=False, server_default="continue"),
        sa.Column("parallel_group", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("service_payload", mysql.JSON(), nullable=True),
        sa.Column("service_exec_parameters_override", mysql.JSON(), nullable=True),
        sa.Column("service_exec_expected_secs", sa.Integer(), nullable=True),
        sa.Column("service_exec_timeout", sa.Integer(), nullable=True),
        sa.Column("service_exec_expected_outcome", mysql.JSON(), nullable=True),
        sa.Column("run_phase", sa.String(length=16), nullable=False, server_default="both"),
        sa.Column("run_condition", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "on_success in ('continue','stop')", name="ck_recipe_ingredients_on_success"
        ),
        sa.CheckConstraint(
            "run_phase in ('firing','resolving','both')",
            name="ck_recipe_ingredients_run_phase",
        ),
        sa.CheckConstraint(
            "service_exec_expected_secs is null or service_exec_expected_secs > 0",
            name="ck_recipe_ingredients_expected_secs_positive",
        ),
        sa.CheckConstraint(
            "service_exec_timeout is null or service_exec_timeout > 0",
            name="ck_recipe_ingredients_timeout_positive",
        ),
        sa.ForeignKeyConstraint(["ingredient_id"], ["ingredients.id"]),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recipe_ingredients_id"), "recipe_ingredients", ["id"], unique=False)
    op.create_index(
        "idx_recipe_ingredient_order",
        "recipe_ingredients",
        ["recipe_id", "step_order"],
        unique=False,
    )

    # Orders (old alerts)
    # Note: We need to use raw SQL to create the table with the generated column
    # because SQLAlchemy doesn't support GENERATED columns in create_table()
    op.execute("""
        CREATE TABLE orders (
            id INTEGER NOT NULL AUTO_INCREMENT,
            req_id VARCHAR(100) NOT NULL,
            fingerprint VARCHAR(255) NOT NULL,
            alert_status VARCHAR(50) NOT NULL,
            processing_status VARCHAR(50) NOT NULL,
            is_active BOOLEAN NOT NULL,
            remediation_outcome VARCHAR(16) NOT NULL DEFAULT 'pending',
            clear_timeout_sec INTEGER,
            clear_deadline_at DATETIME,
            clear_timed_out_at DATETIME,
            auto_close_eligible BOOLEAN NOT NULL DEFAULT 0,
            alert_group_name VARCHAR(255) NOT NULL,
            severity VARCHAR(50),
            instance VARCHAR(255),
            correlation_key VARCHAR(64),
            counter INTEGER NOT NULL,
            labels JSON NOT NULL,
            annotations JSON,
            raw_data JSON,
            starts_at DATETIME NOT NULL,
            ends_at DATETIME,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            fingerprint_when_active VARCHAR(255) GENERATED ALWAYS AS (IF(is_active = 1, fingerprint, NULL)) STORED,
            CONSTRAINT ck_orders_processing_status CHECK (processing_status in ('new','processing','resolving','complete','failed','errored','timeout','canceled')),
            CONSTRAINT ck_orders_remediation_outcome CHECK (remediation_outcome in ('pending','succeeded','failed','none')),
            PRIMARY KEY (id)
        )
        """)
    op.create_index(op.f("ix_orders_id"), "orders", ["id"], unique=False)
    op.create_index(op.f("ix_orders_req_id"), "orders", ["req_id"], unique=False)
    op.create_index(op.f("ix_orders_fingerprint"), "orders", ["fingerprint"], unique=False)
    op.create_index(op.f("ix_orders_alert_status"), "orders", ["alert_status"], unique=False)
    op.create_index(
        op.f("ix_orders_processing_status"), "orders", ["processing_status"], unique=False
    )
    op.create_index(op.f("ix_orders_is_active"), "orders", ["is_active"], unique=False)
    op.create_index(
        "ix_orders_remediation_outcome",
        "orders",
        ["remediation_outcome"],
        unique=False,
    )
    op.create_index(
        "ix_orders_clear_deadline_at",
        "orders",
        ["clear_deadline_at"],
        unique=False,
    )
    op.create_index(
        "ix_orders_clear_timed_out_at",
        "orders",
        ["clear_timed_out_at"],
        unique=False,
    )
    op.create_index(
        "ix_orders_auto_close_eligible",
        "orders",
        ["auto_close_eligible"],
        unique=False,
    )

    # Create unique index on the generated column
    # Since it's NULL for inactive orders, multiple inactive orders can have the same fingerprint
    # But only one active order per fingerprint is allowed
    op.create_index(
        "ux_orders_fingerprint_active", "orders", ["fingerprint_when_active"], unique=True
    )
    op.create_index(
        op.f("ix_orders_alert_group_name"), "orders", ["alert_group_name"], unique=False
    )
    op.create_index(op.f("ix_orders_severity"), "orders", ["severity"], unique=False)
    op.create_index(op.f("ix_orders_instance"), "orders", ["instance"], unique=False)
    op.create_index(op.f("ix_orders_correlation_key"), "orders", ["correlation_key"], unique=False)
    op.create_index(
        "ix_orders_fingerprint_severity_created_at",
        "orders",
        ["fingerprint", "severity", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_orders_created_at"), "orders", ["created_at"], unique=False)
    # Dishes
    op.create_table(
        "dishes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("req_id", sa.String(length=100), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("run_phase", sa.String(length=16), nullable=False, server_default="firing"),
        sa.Column("processing_status", sa.String(length=50), nullable=False),
        sa.Column("dish_exec_status", sa.String(length=50), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("expected_run_secs", sa.Integer(), nullable=True),
        sa.Column("run_time_secs", sa.Integer(), nullable=True),
        sa.Column("dish_actual_outcome", mysql.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "processing_status in ('new','processing','finalizing','complete','failed','errored','timeout','canceled')",
            name="ck_dishes_processing_status",
        ),
        sa.CheckConstraint("run_phase in ('firing','resolving')", name="ck_dishes_run_phase"),
        sa.CheckConstraint(
            "dish_exec_status is null or dish_exec_status in ('pending','dispatched','running','succeeded','failed','errored','timeout','canceled')",
            name="ck_dishes_dish_exec_status",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dishes_id"), "dishes", ["id"], unique=False)
    op.create_index(op.f("ix_dishes_req_id"), "dishes", ["req_id"], unique=False)
    op.create_index(
        op.f("ix_dishes_processing_status"), "dishes", ["processing_status"], unique=False
    )
    op.create_index(op.f("ix_dishes_run_phase"), "dishes", ["run_phase"], unique=False)

    # Dish Ingredients (per-task executions)
    op.create_table(
        "dish_ingredients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("req_id", sa.String(length=100), nullable=False),
        sa.Column("dish_id", sa.Integer(), nullable=False),
        sa.Column("recipe_ingredient_id", sa.Integer(), nullable=True),
        sa.Column("task_key", sa.String(length=255), nullable=True),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("parallel_group", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("service_type", sa.String(length=50), nullable=False),
        sa.Column("service_exec", sa.String(length=255), nullable=False),
        sa.Column("destination_target", sa.String(length=255), nullable=True),
        sa.Column("service_exec_id", sa.String(length=100), nullable=True),
        sa.Column("service_payload", mysql.JSON(), nullable=True),
        sa.Column("service_exec_parameters", mysql.JSON(), nullable=True),
        sa.Column("service_exec_expected_secs", sa.Integer(), nullable=True),
        sa.Column("service_exec_timeout", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("retry_delay", sa.Integer(), nullable=True),
        sa.Column("on_failure", sa.String(length=50), nullable=True),
        sa.Column("service_exec_expected_outcome", mysql.JSON(), nullable=True),
        sa.Column("service_exec_run_time", sa.Integer(), nullable=True),
        sa.Column(
            "service_exec_sla_exceeded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("service_exec_claimed_at", sa.DateTime(), nullable=True),
        sa.Column("service_exec_claimed_by", sa.String(length=100), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "service_exec_id_norm",
            sa.String(length=100),
            sa.Computed("IFNULL(service_exec_id, '')", persisted=True),
        ),
        sa.Column(
            "recipe_ingredient_id_norm",
            sa.Integer(),
            sa.Computed("IFNULL(recipe_ingredient_id, 0)", persisted=True),
        ),
        sa.Column(
            "task_key_norm",
            sa.String(length=255),
            sa.Computed("IFNULL(task_key, '')", persisted=True),
        ),
        sa.Column(
            "service_exec_status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("service_exec_start_time", sa.DateTime(), nullable=True),
        sa.Column("service_exec_completed_time", sa.DateTime(), nullable=True),
        sa.Column("service_exec_canceled_time", sa.DateTime(), nullable=True),
        sa.Column("service_exec_actual_outcome", mysql.JSON(), nullable=True),
        sa.Column("service_exec_error", sa.Text(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("service_type <> ''", name="ck_dish_ingredients_service_type_present"),
        sa.CheckConstraint("service_exec <> ''", name="ck_dish_ingredients_service_exec_present"),
        sa.CheckConstraint(
            "on_failure is null or on_failure in ('continue','stop','retry')",
            name="ck_dish_ingredients_on_failure",
        ),
        sa.CheckConstraint(
            "service_exec_expected_secs is null or service_exec_expected_secs > 0",
            name="ck_dish_ingredients_expected_secs_positive",
        ),
        sa.CheckConstraint(
            "service_exec_timeout is null or service_exec_timeout > 0",
            name="ck_dish_ingredients_timeout_positive",
        ),
        sa.CheckConstraint(
            "service_exec_status in ('pending','dispatched','running','succeeded','failed','errored','timeout','canceled')",
            name="ck_dish_ingredients_service_exec_status",
        ),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"]),
        sa.ForeignKeyConstraint(["recipe_ingredient_id"], ["recipe_ingredients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dish_ingredients_req_id", "dish_ingredients", ["req_id"], unique=False)
    op.create_index("ix_dish_ingredients_dish_id", "dish_ingredients", ["dish_id"], unique=False)
    op.create_index("ix_dish_ingredients_task_key", "dish_ingredients", ["task_key"], unique=False)
    op.create_index(
        "ix_dish_ingredients_service_exec_id",
        "dish_ingredients",
        ["service_exec_id"],
        unique=False,
    )
    op.create_index(
        "ix_dish_ingredients_service_type",
        "dish_ingredients",
        ["service_type"],
        unique=False,
    )
    op.create_index(
        "ux_dish_ingredients_dish_step",
        "dish_ingredients",
        ["dish_id", "recipe_ingredient_id_norm", "task_key_norm"],
        unique=True,
    )

    # Alert suppressions
    op.create_table(
        "alert_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("summary_ticket_enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="local"),
        sa.Column("source_service_type", sa.String(length=50), nullable=True),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("source_payload", mysql.JSON(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_service_type",
            "source_ref",
            name="ux_alert_suppressions_source_ref",
        ),
    )
    op.create_index("ix_alert_suppressions_id", "alert_suppressions", ["id"], unique=False)
    op.create_index("ix_alert_suppressions_name", "alert_suppressions", ["name"], unique=False)
    op.create_index(
        "ix_alert_suppressions_starts_at",
        "alert_suppressions",
        ["starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_alert_suppressions_ends_at", "alert_suppressions", ["ends_at"], unique=False
    )
    op.create_index(
        "ix_alert_suppressions_canceled_at",
        "alert_suppressions",
        ["canceled_at"],
        unique=False,
    )
    op.create_index(
        "idx_alert_suppressions_active_lookup",
        "alert_suppressions",
        ["enabled", "starts_at", "ends_at", "canceled_at"],
        unique=False,
    )

    op.create_table(
        "alert_suppression_matchers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suppression_id", sa.Integer(), nullable=False),
        sa.Column("label_key", sa.String(length=255), nullable=False),
        sa.Column("operator", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["suppression_id"], ["alert_suppressions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_suppression_matchers_id",
        "alert_suppression_matchers",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_suppression_matchers_suppression_id",
        "alert_suppression_matchers",
        ["suppression_id"],
        unique=False,
    )

    op.create_table(
        "suppressed_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suppression_id", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=True),
        sa.Column("alertname", sa.String(length=255), nullable=True),
        sa.Column("severity", sa.String(length=64), nullable=True),
        sa.Column("labels_json", mysql.JSON(), nullable=False),
        sa.Column("annotations_json", mysql.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("req_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["suppression_id"], ["alert_suppressions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suppressed_events_id", "suppressed_events", ["id"], unique=False)
    op.create_index(
        "ix_suppressed_events_suppression_id",
        "suppressed_events",
        ["suppression_id"],
        unique=False,
    )
    op.create_index(
        "ix_suppressed_events_received_at",
        "suppressed_events",
        ["received_at"],
        unique=False,
    )
    op.create_index(
        "ix_suppressed_events_fingerprint",
        "suppressed_events",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_suppressed_events_alertname",
        "suppressed_events",
        ["alertname"],
        unique=False,
    )
    op.create_index(
        "ix_suppressed_events_severity",
        "suppressed_events",
        ["severity"],
        unique=False,
    )
    op.create_index(
        "ix_suppressed_events_payload_hash",
        "suppressed_events",
        ["payload_hash"],
        unique=False,
    )
    op.create_index("ix_suppressed_events_req_id", "suppressed_events", ["req_id"], unique=False)
    op.create_index(
        "idx_suppressed_events_suppression_received_at",
        "suppressed_events",
        ["suppression_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "idx_suppressed_events_fingerprint",
        "suppressed_events",
        ["fingerprint"],
        unique=False,
    )

    op.create_table(
        "suppression_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suppression_id", sa.Integer(), nullable=False),
        sa.Column("total_suppressed", sa.Integer(), nullable=False),
        sa.Column("total_cleared", sa.Integer(), nullable=False),
        sa.Column("total_still_firing", sa.Integer(), nullable=False),
        sa.Column("by_alertname_json", mysql.JSON(), nullable=True),
        sa.Column("by_severity_json", mysql.JSON(), nullable=True),
        sa.Column("still_firing_alerts_json", mysql.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("summary_created_at", sa.DateTime(), nullable=True),
        sa.Column("summary_close_at", sa.DateTime(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["suppression_id"], ["alert_suppressions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suppression_id"),
    )
    op.create_index("ix_suppression_summaries_id", "suppression_summaries", ["id"], unique=False)
    op.create_index(
        "ix_suppression_summaries_suppression_id",
        "suppression_summaries",
        ["suppression_id"],
        unique=True,
    )
    op.create_index(
        "ix_suppression_summaries_state",
        "suppression_summaries",
        ["state"],
        unique=False,
    )

    op.create_table(
        "auth_principals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("groups_json", mysql.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "subject_id",
            name="ux_auth_principals_provider_subject",
        ),
    )
    op.create_index("ix_auth_principals_id", "auth_principals", ["id"], unique=False)
    op.create_index(
        "ix_auth_principals_provider",
        "auth_principals",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_auth_principals_username",
        "auth_principals",
        ["username"],
        unique=False,
    )
    op.create_index(
        "ix_auth_principals_provider_username",
        "auth_principals",
        ["provider", "username"],
        unique=False,
    )

    op.create_table(
        "auth_role_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("binding_type", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("principal_id", sa.Integer(), nullable=True),
        sa.Column("external_group", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id"], ["auth_principals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "binding_type",
            "principal_id",
            name="ux_auth_role_bindings_provider_type_principal",
        ),
        sa.UniqueConstraint(
            "provider",
            "binding_type",
            "external_group",
            name="ux_auth_role_bindings_provider_type_group",
        ),
    )
    op.create_index("ix_auth_role_bindings_id", "auth_role_bindings", ["id"], unique=False)
    op.create_index(
        "ix_auth_role_bindings_provider",
        "auth_role_bindings",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_auth_role_bindings_binding_type",
        "auth_role_bindings",
        ["binding_type"],
        unique=False,
    )
    op.create_index(
        "ix_auth_role_bindings_role",
        "auth_role_bindings",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_auth_role_bindings_principal_id",
        "auth_role_bindings",
        ["principal_id"],
        unique=False,
    )
    op.create_index(
        "ix_auth_role_bindings_external_group",
        "auth_role_bindings",
        ["external_group"],
        unique=False,
    )


def downgrade() -> None:
    # Greenfield-only schema: reset database state by recreating the database/volume.
    pass

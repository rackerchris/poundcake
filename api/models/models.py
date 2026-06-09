#  ____                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Database models for PoundCake."""

from __future__ import annotations

from api.types import JSONObject

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

# We use the explicit MariaDB/MySQL JSON type to ensure the dialect handles serialization properly
from sqlalchemy.dialects.mysql import JSON as MYSQL_JSON
from api.core.database import Base
from api.core.time import utc_now_db


def get_utc_now():
    """Return a UTC timestamp for database storage."""
    return utc_now_db()


class RecipeIngredient(Base):
    """
    The 'Assembly Line' - links Ingredients to Recipes in a specific order.
    """

    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), nullable=False)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), nullable=False)

    # Determines the position in the recipe execution graph.
    step_order: Mapped[int] = mapped_column(default=1, nullable=False)

    # Logic gates for recipe graph execution.
    on_success: Mapped[str | None] = mapped_column(String(50), default="continue")
    # Parallel grouping (same depth implies parallel tasks)
    parallel_group: Mapped[int] = mapped_column(default=0, nullable=False)
    # Depth in the task graph (for parallel/linear ordering)
    depth: Mapped[int] = mapped_column(default=0, nullable=False)
    # Optional per-step execution parameter overrides.
    service_payload: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    service_exec_parameters_override: Mapped[JSONObject | None] = mapped_column(
        MYSQL_JSON, nullable=True
    )
    service_exec_expected_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_exec_timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_exec_expected_outcome: Mapped[Any | None] = mapped_column(MYSQL_JSON, nullable=True)
    # Controls when this step is eligible to run in the order lifecycle.
    run_phase: Mapped[str] = mapped_column(String(16), default="both", nullable=False)
    # Optional lifecycle condition gate for the step.
    run_condition: Mapped[str] = mapped_column(String(40), default="always", nullable=False)

    recipe: Mapped["Recipe"] = relationship(back_populates="recipe_ingredients")
    ingredient: Mapped["Ingredient"] = relationship()

    __table_args__ = (
        Index("idx_recipe_ingredient_order", "recipe_id", "step_order"),
        CheckConstraint(
            "on_success in ('continue','stop')", name="ck_recipe_ingredients_on_success"
        ),
        CheckConstraint(
            "run_phase in ('firing','resolving','both')",
            name="ck_recipe_ingredients_run_phase",
        ),
        CheckConstraint(
            "service_exec_expected_secs is null or service_exec_expected_secs > 0",
            name="ck_recipe_ingredients_expected_secs_positive",
        ),
        CheckConstraint(
            "service_exec_timeout is null or service_exec_timeout > 0",
            name="ck_recipe_ingredients_timeout_positive",
        ),
    )


class Recipe(Base):
    """
    Workflow templates and metadata
    """

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clear_timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    recipe_ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient",
        back_populates="recipe",
        order_by="RecipeIngredient.step_order",
        cascade="all, delete-orphan",
    )
    dishes: Mapped[list["Dish"]] = relationship("Dish", back_populates="recipe")

    @hybrid_property
    def total_expected_run_secs(self):
        """Automatically sums the duration of all ingredients in this recipe."""
        return sum(
            ri.ingredient.default_expected_secs
            for ri in self.recipe_ingredients
            if ri.ingredient is not None
        )


class Ingredient(Base):
    """
    Atomic execution definitions.
    """

    __tablename__ = "ingredients"
    __table_args__ = (
        Index(
            "idx_ingredients_service_template",
            "service_type",
            "service_exec",
            "destination_target",
            "task_key_template",
        ),
        CheckConstraint("service_type <> ''", name="ck_ingredients_service_type_present"),
        CheckConstraint("service_exec <> ''", name="ck_ingredients_service_exec_present"),
        CheckConstraint(
            "ingredient_purpose in ('remediation','comms','utility','plugin_health','suppression_sync','suppression_lifecycle')",
            name="ck_ingredients_ingredient_purpose",
        ),
        CheckConstraint(
            "on_failure in ('continue','stop','retry')",
            name="ck_ingredients_on_failure",
        ),
        CheckConstraint(
            "default_expected_secs > 0",
            name="ck_ingredients_default_expected_secs_positive",
        ),
        CheckConstraint("default_timeout > 0", name="ck_ingredients_default_timeout_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_exec: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    destination_target: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    task_key_template: Mapped[str] = mapped_column(String(255), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), default="undefined", nullable=False)
    ingredient_purpose: Mapped[str] = mapped_column(String(32), default="utility", nullable=False)

    service_payload_template: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    service_exec_parameters: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    payload_schema: Mapped[JSONObject] = mapped_column(
        MYSQL_JSON,
        nullable=False,
        default=lambda: {"type": "object", "additionalProperties": True},
    )
    service_exec_expected_outcome_default: Mapped[Any | None] = mapped_column(
        MYSQL_JSON, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_blocking: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_expected_secs: Mapped[int] = mapped_column(Integer, nullable=False)
    default_timeout: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_delay: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    on_failure: Mapped[str] = mapped_column(String(50), default="stop", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServicePlugin(Base):
    """Persisted control-plane state for enabled service plugins."""

    __tablename__ = "service_plugins"
    __table_args__ = (
        UniqueConstraint("service_type", name="ux_service_plugins_service_type"),
        UniqueConstraint("plugin_short_id", name="ux_service_plugins_plugin_short_id"),
        UniqueConstraint("plugin_log_key", name="ux_service_plugins_plugin_log_key"),
        Index("ix_service_plugins_enabled", "enabled"),
        Index("ix_service_plugins_plugin_type", "plugin_type"),
        Index("ix_service_plugins_plugin_short_id", "plugin_short_id"),
        Index("ix_service_plugins_plugin_tier", "plugin_tier"),
        Index("ix_service_plugins_health_status", "health_status"),
        Index("ix_service_plugins_next_health_check_at", "next_health_check_at"),
        CheckConstraint("service_type <> ''", name="ck_service_plugins_service_type_present"),
        CheckConstraint("plugin_short_id <> ''", name="ck_service_plugins_short_id_present"),
        CheckConstraint(
            "plugin_type in ('internal_plugin','external_plugin')",
            name="ck_service_plugins_plugin_type",
        ),
        CheckConstraint(
            "plugin_tier in ('community','supported')",
            name="ck_service_plugins_plugin_tier",
        ),
        CheckConstraint(
            "plugin_log_key is null or plugin_log_key <> ''",
            name="ck_service_plugins_plugin_log_key_present",
        ),
        CheckConstraint(
            "health_status in ('unknown','initializing','healthy','degraded','failed','disabled')",
            name="ck_service_plugins_health_status",
        ),
        CheckConstraint(
            "health_check_state in ('idle','queued','running')",
            name="ck_service_plugins_health_check_state",
        ),
        CheckConstraint(
            "query_limit is null or query_limit > 0",
            name="ck_service_plugins_query_limit_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    plugin_short_id: Mapped[str] = mapped_column(String(12), nullable=False)
    plugin_type: Mapped[str] = mapped_column(String(32), default="external_plugin", nullable=False)
    plugin_tier: Mapped[str] = mapped_column(String(32), default="community", nullable=False)
    plugin_log_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    plugin_config: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    capabilities_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registered_ingredient_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_recipe_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credential_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    credential_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_credential_bootstrap_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_credential_rotation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    health_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_details: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_health_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health_check_state: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    health_check_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    health_check_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    health_check_grace_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )


class AdapterCredential(Base):
    """Encrypted adapter/provider credential material owned by Credential Manager.

    ``allow_public_read`` controls whether the credential manager permits the
    owning adapter to use unauthenticated public read endpoints (e.g.
    ``raw.githubusercontent.com``) when no token is configured.  The credential
    manager is the **authoritative policy gate** — it stores this flag, returns
    it alongside the decrypted payload, and the adapter must honour it.  Default
    is ``false``; operators/bootstrap must explicitly set it to ``true`` during
    credential write.
    """

    __tablename__ = "adapter_credentials"
    __table_args__ = (
        UniqueConstraint(
            "service_plugin_id",
            "credential_type",
            "credential_key_id",
            name="ux_adapter_credentials_identity",
        ),
        Index("ix_adapter_credentials_service_plugin_id", "service_plugin_id"),
        CheckConstraint("credential_type <> ''", name="ck_adapter_credentials_type_present"),
        CheckConstraint("credential_key_id <> ''", name="ck_adapter_credentials_key_present"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_plugin_id: Mapped[int] = mapped_column(
        ForeignKey("service_plugins.id", ondelete="CASCADE"),
        nullable=False,
    )
    credential_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    allow_public_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )


class ServiceIdentityCredential(Base):
    """Encrypted internal service identity material owned by Service Identity Manager."""

    __tablename__ = "service_identity_credentials"

    __table_args__ = (
        UniqueConstraint(
            "service_plugin_id",
            "credential_type",
            "credential_key_id",
            name="ux_service_identity_credentials_identity",
        ),
        UniqueConstraint(
            "credential_type",
            "credential_key_id",
            name="ux_service_identity_credentials_key_identity",
        ),
        Index("ix_service_identity_credentials_service_plugin_id", "service_plugin_id"),
        Index("ix_service_identity_credentials_key_id", "credential_key_id"),
        CheckConstraint(
            "credential_type <> ''", name="ck_service_identity_credentials_type_present"
        ),
        CheckConstraint(
            "credential_key_id <> ''", name="ck_service_identity_credentials_key_present"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    service_plugin_id: Mapped[int] = mapped_column(
        ForeignKey("service_plugins.id", ondelete="CASCADE"),
        nullable=False,
    )
    credential_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )


class ScheduledTask(Base):
    """Recurring Dishwasher task definition and latest run summary."""

    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        UniqueConstraint("task_key", name="ux_scheduled_tasks_task_key"),
        Index("ix_scheduled_tasks_enabled_next_run", "is_enabled", "next_run_at"),
        Index("ix_scheduled_tasks_status", "status"),
        Index("ix_scheduled_tasks_service_type", "service_type"),
        Index("ix_scheduled_tasks_task_type", "task_type"),
        CheckConstraint("task_key <> ''", name="ck_scheduled_tasks_task_key_present"),
        CheckConstraint("task_type <> ''", name="ck_scheduled_tasks_task_type_present"),
        CheckConstraint("run_interval_seconds > 0", name="ck_scheduled_tasks_interval_positive"),
        CheckConstraint("timeout_seconds > 0", name="ck_scheduled_tasks_timeout_positive"),
        CheckConstraint(
            "source in ('core','plugin_manifest','registered')",
            name="ck_scheduled_tasks_source",
        ),
        CheckConstraint(
            "task_type in ('plugin_health_check','service_execution')",
            name="ck_scheduled_tasks_task_type",
        ),
        CheckConstraint(
            "status in ('idle','queued','running','disabled')",
            name="ck_scheduled_tasks_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    task_key: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    service_exec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="registered", nullable=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    task_payload: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    task_parameters: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    expected_outcome: Mapped[Any | None] = mapped_column(MYSQL_JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_order_req_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )


class OperatorAuditEvent(Base):
    """Reader-safe audit trail for UI-initiated operator actions."""

    __tablename__ = "operator_audit_events"
    __table_args__ = (
        Index("ix_operator_audit_events_created_at", "created_at"),
        Index("ix_operator_audit_events_surface_status", "surface", "status"),
        Index("ix_operator_audit_events_actor_username", "actor_username"),
        CheckConstraint("action <> ''", name="ck_operator_audit_events_action_present"),
        CheckConstraint("surface <> ''", name="ck_operator_audit_events_surface_present"),
        CheckConstraint("status <> ''", name="ck_operator_audit_events_status_present"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    req_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    surface: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="attempt")
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details: Mapped[JSONObject] = mapped_column(MYSQL_JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)


class Dish(Base):
    """
    Execution instances - Tracks the actual run of a recipe.
    """

    __tablename__ = "dishes"
    __table_args__ = (
        CheckConstraint(
            "processing_status in ('new','processing','finalizing','complete','failed','errored','timeout','canceled')",
            name="ck_dishes_processing_status",
        ),
        CheckConstraint("run_phase in ('firing','resolving')", name="ck_dishes_run_phase"),
        CheckConstraint(
            "dish_exec_status is null or dish_exec_status in ('pending','dispatched','running','succeeded','failed','errored','timeout','canceled')",
            name="ck_dishes_dish_exec_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Traceability ID from Middleware (X-Request-Id)
    req_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), nullable=False)
    run_phase: Mapped[str] = mapped_column(String(16), default="firing", nullable=False, index=True)

    processing_status: Mapped[str] = mapped_column(
        String(50), default="new", nullable=False, index=True
    )
    dish_exec_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Snapshot of the expected duration at the time of the order
    expected_run_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Calculation: completed_at - started_at
    run_time_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Rollup output payload for this dish.
    dish_actual_outcome: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    # Relationships
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="dishes")
    order: Mapped["Order | None"] = relationship("Order", back_populates="dishes")
    dish_ingredients: Mapped[list["DishIngredient"]] = relationship(
        "DishIngredient",
        back_populates="dish",
        cascade="all, delete-orphan",
    )

    @property
    def work_execution_time_secs(self) -> int | None:
        total = 0
        seen = False
        for row in self.dish_ingredients or []:
            run_time = row.service_exec_run_time
            if run_time is None:
                continue
            total += int(run_time)
            seen = True
        return total if seen else None

    @property
    def work_execution_groups(self) -> list[dict[str, int]]:
        groups: dict[tuple[int, int], dict[str, int]] = {}
        for row in self.dish_ingredients or []:
            depth = int(row.depth or 0)
            parallel_group = int(row.parallel_group or 0)
            key = (depth, parallel_group)
            group = groups.setdefault(
                key,
                {
                    "depth": depth,
                    "parallel_group": parallel_group,
                    "rows": 0,
                    "total_seconds": 0,
                },
            )
            group["rows"] += 1
            group["total_seconds"] += int(row.service_exec_run_time or 0)
        return [groups[key] for key in sorted(groups)]


class DishIngredient(Base):
    """
    Execution instances for individual recipe tasks within a Dish.
    """

    __tablename__ = "dish_ingredients"
    __table_args__ = (
        CheckConstraint("service_type <> ''", name="ck_dish_ingredients_service_type_present"),
        CheckConstraint("service_exec <> ''", name="ck_dish_ingredients_service_exec_present"),
        CheckConstraint(
            "on_failure is null or on_failure in ('continue','stop','retry')",
            name="ck_dish_ingredients_on_failure",
        ),
        CheckConstraint(
            "service_exec_expected_secs is null or service_exec_expected_secs > 0",
            name="ck_dish_ingredients_expected_secs_positive",
        ),
        CheckConstraint(
            "service_exec_timeout is null or service_exec_timeout > 0",
            name="ck_dish_ingredients_timeout_positive",
        ),
        CheckConstraint(
            "service_exec_status in ('pending','dispatched','running','succeeded','failed','errored','timeout','canceled')",
            name="ck_dish_ingredients_service_exec_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    req_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"), nullable=False, index=True)
    recipe_ingredient_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipe_ingredients.id"), nullable=True
    )

    task_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    step_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parallel_group: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    service_exec: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_exec_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    service_payload: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    service_exec_parameters: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    service_exec_expected_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_exec_timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_delay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_failure: Mapped[str | None] = mapped_column(String(50), nullable=True)
    service_exec_expected_outcome: Mapped[Any | None] = mapped_column(MYSQL_JSON, nullable=True)
    service_exec_run_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_exec_sla_exceeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    service_exec_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    service_exec_claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    service_exec_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    service_exec_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    service_exec_completed_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    service_exec_canceled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    service_exec_actual_outcome: Mapped[JSONObject | None] = mapped_column(
        MYSQL_JSON, nullable=True
    )
    service_exec_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    dish: Mapped["Dish"] = relationship("Dish", back_populates="dish_ingredients")
    recipe_ingredient: Mapped["RecipeIngredient | None"] = relationship("RecipeIngredient")


class Order(Base):
    """
    The Webhook/Alert trigger (Old Alerts)
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "processing_status in ('new','processing','resolving','complete','failed','errored','timeout','canceled')",
            name="ck_orders_processing_status",
        ),
        CheckConstraint(
            "remediation_outcome in ('pending','succeeded','failed','none')",
            name="ck_orders_remediation_outcome",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    req_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alert_status: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    processing_status: Mapped[str] = mapped_column(
        String(50), default="new", nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    remediation_outcome: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False, index=True
    )
    clear_timeout_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clear_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    clear_timed_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    auto_close_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    alert_group_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    severity: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    instance: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    counter: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fingerprint_when_active: Mapped[str | None] = mapped_column(
        String(255),
        Computed("IF(is_active = 1, fingerprint, NULL)", persisted=True),
        nullable=True,
    )

    labels: Mapped[JSONObject] = mapped_column(MYSQL_JSON, nullable=False)
    annotations: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    raw_data: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)

    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    dishes: Mapped[list["Dish"]] = relationship("Dish", back_populates="order")

    @property
    def order_lifetime_secs(self) -> int | None:
        if self.processing_status not in {"complete", "failed", "errored", "timeout", "canceled"}:
            return None
        if self.created_at is None or self.updated_at is None:
            return None
        return max(0, int((self.updated_at - self.created_at).total_seconds()))


class AlertSuppression(Base):
    """Maintenance window for suppressing webhook alerts."""

    __tablename__ = "alert_suppressions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="matchers")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary_ticket_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    source_service_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_payload: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    matchers: Mapped[list["AlertSuppressionMatcher"]] = relationship(
        "AlertSuppressionMatcher",
        back_populates="suppression",
        cascade="all, delete-orphan",
    )

    suppressed_events: Mapped[list["SuppressedEvent"]] = relationship(
        "SuppressedEvent",
        back_populates="suppression",
        cascade="all, delete-orphan",
    )

    summary: Mapped["SuppressionSummary | None"] = relationship(
        "SuppressionSummary",
        back_populates="suppression",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "idx_alert_suppressions_active_lookup",
            "enabled",
            "starts_at",
            "ends_at",
            "canceled_at",
        ),
        UniqueConstraint(
            "source_service_type",
            "source_ref",
            name="ux_alert_suppressions_source_ref",
        ),
    )


class AlertSuppressionMatcher(Base):
    """Label matcher row for suppression matching rules."""

    __tablename__ = "alert_suppression_matchers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    suppression_id: Mapped[int] = mapped_column(
        ForeignKey("alert_suppressions.id"),
        nullable=False,
        index=True,
    )
    label_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)

    suppression: Mapped["AlertSuppression"] = relationship(
        "AlertSuppression", back_populates="matchers"
    )


class SuppressedEvent(Base):
    """Individual alert event captured by suppression windows."""

    __tablename__ = "suppressed_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    suppression_id: Mapped[int] = mapped_column(
        ForeignKey("alert_suppressions.id"),
        nullable=False,
        index=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    alertname: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    severity: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    labels_json: Mapped[JSONObject] = mapped_column(MYSQL_JSON, nullable=False)
    annotations_json: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="firing")
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    req_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)

    suppression: Mapped["AlertSuppression"] = relationship(
        "AlertSuppression",
        back_populates="suppressed_events",
    )

    __table_args__ = (
        Index("idx_suppressed_events_suppression_received_at", "suppression_id", "received_at"),
        Index("idx_suppressed_events_fingerprint", "fingerprint"),
    )


class SuppressionSummary(Base):
    """Aggregated suppression window summary."""

    __tablename__ = "suppression_summaries"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    suppression_id: Mapped[int] = mapped_column(
        ForeignKey("alert_suppressions.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    total_suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cleared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_still_firing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    by_alertname_json: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    by_severity_json: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    still_firing_alerts_json: Mapped[JSONObject | None] = mapped_column(MYSQL_JSON, nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_close_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    suppression: Mapped["AlertSuppression"] = relationship(
        "AlertSuppression", back_populates="summary"
    )


class AuthPrincipal(Base):
    """Observed external principal metadata used for RBAC assignments."""

    __tablename__ = "auth_principals"
    __table_args__ = (
        UniqueConstraint("provider", "subject_id", name="ux_auth_principals_provider_subject"),
        Index("ix_auth_principals_provider_username", "provider", "username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    groups_json: Mapped[list[str] | None] = mapped_column(MYSQL_JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    role_bindings: Mapped[list["AuthRoleBinding"]] = relationship(
        "AuthRoleBinding",
        back_populates="principal",
        cascade="all, delete-orphan",
    )


class AuthRoleBinding(Base):
    """Provider-scoped RBAC bindings for users and groups."""

    __tablename__ = "auth_role_bindings"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "binding_type",
            "principal_id",
            name="ux_auth_role_bindings_provider_type_principal",
        ),
        UniqueConstraint(
            "provider",
            "binding_type",
            "external_group",
            name="ux_auth_role_bindings_provider_type_group",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    binding_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    principal_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_principals.id"),
        nullable=True,
        index=True,
    )
    external_group: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=get_utc_now, onupdate=get_utc_now, nullable=False
    )

    principal: Mapped["AuthPrincipal | None"] = relationship(
        "AuthPrincipal",
        back_populates="role_bindings",
    )


class HmacNonce(Base):
    """Replay-protection nonce for internal HMAC-signed requests.

    Used by ``database``-mode nonce store to share replay state across API
    replicas.  Rows are inserted atomically with INSERT … SELECT WHERE NOT
    EXISTS and TTL-enforced by the callers clock skew.
    """

    __tablename__ = "hmac_nonces"
    __table_args__ = (
        UniqueConstraint("kind", "key", name="ux_hmac_nonces_kind_key"),
        Index("ix_hmac_nonces_kind_expires_at", "kind", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

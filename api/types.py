#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""
Strict type definitions using Literal types for better type safety.

This module defines all valid status values and other constrained types
using Python's Literal type for compile-time type checking and better
IDE support.
"""

from typing import Literal

from shared.types import JSONArray, JSONObject, JSONPrimitive, JSONValue

__all__ = [
    "JSONArray",
    "JSONObject",
    "JSONPrimitive",
    "JSONValue",
]

# =============================================================================
# Processing Status Types
# =============================================================================

# Dish processing statuses
DishProcessingStatus = Literal[
    "new",
    "processing",
    "finalizing",
    "complete",
    "failed",
    "errored",
    "timeout",
    "canceled",
]

# Order processing statuses (subset of dish statuses)
OrderProcessingStatus = Literal[
    "new",
    "processing",
    "resolving",
    "complete",
    "failed",
    "errored",
    "timeout",
    "canceled",
]

# =============================================================================
# Alert Status Types
# =============================================================================

AlertStatus = Literal[
    "firing",
    "resolved",
]

# =============================================================================
# Order Types
# =============================================================================

OrderType = Literal[
    "webhook_alert",
    "scheduled_task",
    "manual",
]

OrderScope = Literal[
    "operator",
    "system",
    "all",
]

WEBHOOK_ALERT_ORDER_TYPE: OrderType = "webhook_alert"
SCHEDULED_TASK_ORDER_TYPE: OrderType = "scheduled_task"
MANUAL_ORDER_TYPE: OrderType = "manual"

ALL_ORDER_TYPES: frozenset[OrderType] = frozenset(
    {WEBHOOK_ALERT_ORDER_TYPE, SCHEDULED_TASK_ORDER_TYPE, MANUAL_ORDER_TYPE}
)
OPERATOR_ORDER_TYPES: frozenset[OrderType] = frozenset({WEBHOOK_ALERT_ORDER_TYPE})
SYSTEM_ORDER_TYPES: frozenset[OrderType] = frozenset({SCHEDULED_TASK_ORDER_TYPE})

# =============================================================================
# Recipe Ingredient Types
# =============================================================================

OnSuccessAction = Literal[
    "continue",
    "stop",
]

OnFailureAction = Literal[
    "continue",
    "stop",
    "retry",
]

RunPhase = Literal[
    "firing",
    "resolving",
    "both",
]

DishRunPhase = Literal[
    "firing",
    "resolving",
]

RunCondition = Literal[
    "always",
    "remediation_failed",
    "clear_timeout_expired",
    "resolved_after_success",
    "resolved_after_failure",
    "resolved_after_no_remediation",
    "resolved_after_timeout",
]

ExecutionPurpose = Literal[
    "remediation",
    "comms",
    "utility",
    "plugin_health",
    "suppression_sync",
    "suppression_lifecycle",
]

RemediationOutcome = Literal[
    "pending",
    "succeeded",
    "failed",
    "none",
]

# =============================================================================
# Suppression Types
# =============================================================================

SuppressionScope = Literal[
    "all",
    "matchers",
]

SuppressionStatus = Literal[
    "scheduled",
    "active",
    "expired",
    "canceled",
]

SuppressionMatcherOperator = Literal[
    "eq",
    "neq",
    "regex",
    "nregex",
    "exists",
    "not_exists",
]

# =============================================================================
# Unified Execution Types
# =============================================================================

CanonicalExecutionStatus = Literal[
    "pending",
    "dispatched",
    "running",
    "succeeded",
    "failed",
    "errored",
    "timeout",
    "canceled",
]

PluginHealthStatus = Literal[
    "unknown",
    "initializing",
    "healthy",
    "degraded",
    "failed",
    "disabled",
]

PluginBootstrapStatus = Literal[
    "ready",
    "initializing",
    "failed",
]

PluginHealthCheckState = Literal[
    "idle",
    "queued",
    "running",
]

ScheduledTaskStatus = Literal[
    "idle",
    "queued",
    "running",
    "disabled",
]

ScheduledTaskSource = Literal[
    "core",
    "plugin_manifest",
    "registered",
]

ScheduledTaskType = Literal[
    "plugin_health_check",
    "service_execution",
]

# =============================================================================
# Authentication & Authorization Types
# =============================================================================

AuthRole = Literal[
    "reader",
    "operator",
    "admin",
    "service",
]

AuthProvider = Literal[
    "local",
    "active_directory",
    "auth0",
    "azure_ad",
    "service",
]

AuthPrincipalType = Literal[
    "user",
    "service",
]

AuthBindingType = Literal[
    "user",
    "group",
]

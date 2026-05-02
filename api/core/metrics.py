#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
"""Prometheus metrics definitions for PoundCake."""

from prometheus_client import Counter, Histogram, Gauge, Info

# Application info
APP_INFO = Info("poundcake", "PoundCake application information")

# Order metrics
ORDERS_RECEIVED = Counter(
    "poundcake_orders_received_total", "Total number of orders received", ["group_name", "severity"]
)

ORDERS_PROCESSED = Counter(
    "poundcake_orders_processed_total", "Total number of orders processed", ["group_name", "status"]
)

ORDERS_RESOLVED_BEFORE_DISH = Counter(
    "poundcake_orders_resolved_before_dish_start_total",
    "Total number of orders resolved before any dish was started",
    ["group_name", "severity"],
)

ORDER_LIFETIME = Histogram(
    "poundcake_order_lifetime_seconds",
    "Total order lifetime from ingestion to terminal state",
    ["group_name", "status"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

DISH_WALL_TIME = Histogram(
    "poundcake_dish_wall_time_seconds",
    "Dish elapsed wall time from first work execution dispatch to terminal state",
    ["group_name", "run_phase", "status"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

DISH_WORK_EXECUTION_TIME = Histogram(
    "poundcake_dish_work_execution_seconds",
    "Total dish work execution time summed across work execution rows",
    ["group_name", "run_phase", "status"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

# Remediation metrics
REMEDIATIONS_EXECUTED = Counter(
    "poundcake_remediations_executed_total",
    "Total number of remediation actions executed",
    ["action", "status"],
)

REMEDIATION_DURATION = Histogram(
    "poundcake_remediation_duration_seconds",
    "Time spent executing remediation actions",
    ["action"],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

# Task queue metrics
ACTIVE_TASKS = Gauge("poundcake_active_tasks", "Number of currently processing tasks")

QUEUED_TASKS = Gauge("poundcake_queued_tasks", "Number of tasks waiting in queue")

# API metrics
HTTP_REQUESTS = Counter(
    "poundcake_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status_code"]
)

HTTP_REQUEST_DURATION = Histogram(
    "poundcake_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# HTTP retry metrics
HTTP_RETRIES = Counter(
    "poundcake_http_retries_total",
    "Total HTTP request retries",
    ["method", "endpoint", "reason"],
)

# Database metrics
DB_CONNECTIONS = Gauge("poundcake_db_connections", "Number of active database connections")

DB_QUERY_DURATION = Histogram(
    "poundcake_db_query_duration_seconds",
    "Database query duration",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# Mapping metrics
MAPPINGS_TOTAL = Gauge("poundcake_mappings_total", "Total number of configured mappings")

MAPPINGS_MATCHED = Counter(
    "poundcake_mappings_matched_total",
    "Total number of orders matched to mappings",
    ["mapping_name"],
)

POUNDCAKE_SUPPRESSED_EVENTS = Counter(
    "poundcake_suppressed_events_total",
    "Total suppressed events attributed to suppression windows",
    ["suppression_id", "alertname", "severity"],
)

POUNDCAKE_ACTIVE_SUPPRESSIONS = Gauge(
    "poundcake_active_suppressions",
    "Number of currently active alert suppression windows",
)

POUNDCAKE_SUPPRESSION_SUMMARY_TICKETS = Counter(
    "poundcake_suppression_summary_tickets_total",
    "Total suppression summary ticket create/close outcomes",
    ["status"],
)

POUNDCAKE_SUPPRESSION_SUMMARY_FAILURES = Counter(
    "poundcake_suppression_summary_failures_total",
    "Total suppression summary lifecycle failures",
)


def init_app_info(app_name: str, version: str) -> None:
    """Initialize application info metric.

    Args:
        app_name: Application name
        version: Application version
    """
    APP_INFO.info(
        {
            "app_name": app_name,
            "version": version,
        }
    )


def record_order_received(group_name: str, severity: str) -> None:
    """Record an order received.

    Args:
        group_name: Name of the group (order)
        severity: Severity level
    """
    ORDERS_RECEIVED.labels(group_name=group_name, severity=severity or "unknown").inc()


def record_order_processed(group_name: str, status: str) -> None:
    """Record an order processed.

    Args:
        group_name: Name of the group (order)
        status: Processing status (completed, failed, etc.)
    """
    ORDERS_PROCESSED.labels(group_name=group_name, status=status).inc()


def record_order_lifetime(group_name: str, status: str, lifetime_seconds: int | None) -> None:
    """Record total order lifetime from ingestion to terminal state."""
    if lifetime_seconds is None:
        return
    ORDER_LIFETIME.labels(group_name=group_name, status=status).observe(lifetime_seconds)


def record_dish_wall_time(
    group_name: str,
    run_phase: str,
    status: str,
    wall_time_seconds: int | None,
) -> None:
    """Record elapsed dish wall time."""
    if wall_time_seconds is None:
        return
    DISH_WALL_TIME.labels(
        group_name=group_name,
        run_phase=run_phase or "unknown",
        status=status,
    ).observe(wall_time_seconds)


def record_dish_work_execution_time(
    group_name: str,
    run_phase: str,
    status: str,
    work_execution_seconds: int | None,
) -> None:
    """Record summed work execution time for a dish."""
    if work_execution_seconds is None:
        return
    DISH_WORK_EXECUTION_TIME.labels(
        group_name=group_name,
        run_phase=run_phase or "unknown",
        status=status,
    ).observe(work_execution_seconds)


def record_order_resolved_before_dish_start(group_name: str, severity: str) -> None:
    """Record an order resolved before any dish was started.

    Args:
        group_name: Name of the group (order)
        severity: Severity level
    """
    ORDERS_RESOLVED_BEFORE_DISH.labels(group_name=group_name, severity=severity or "unknown").inc()


def record_remediation(action: str, status: str, duration: float | None = None) -> None:
    """Record a remediation execution.

    Args:
        action: Action reference
        status: Execution status
        duration: Execution duration in seconds
    """
    REMEDIATIONS_EXECUTED.labels(action=action, status=status).inc()
    if duration is not None:
        REMEDIATION_DURATION.labels(action=action).observe(duration)


def record_http_request(method: str, endpoint: str, status_code: int, duration: float) -> None:
    """Record an HTTP request.

    Args:
        method: HTTP method
        endpoint: Request endpoint
        status_code: Response status code
        duration: Request duration in seconds
    """
    HTTP_REQUESTS.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
    HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)


def record_http_retry(method: str, endpoint: str, reason: str) -> None:
    """Record an HTTP retry attempt.

    Args:
        method: HTTP method
        endpoint: Request endpoint
        reason: Retry reason (status/exception)
    """
    HTTP_RETRIES.labels(method=method, endpoint=endpoint, reason=reason).inc()


def update_active_tasks(count: int) -> None:
    """Update the active tasks gauge.

    Args:
        count: Current number of active tasks
    """
    ACTIVE_TASKS.set(count)


def update_queued_tasks(count: int) -> None:
    """Update the queued tasks gauge.

    Args:
        count: Current number of queued tasks
    """
    QUEUED_TASKS.set(count)


def update_mappings_total(count: int) -> None:
    """Update the total mappings gauge.

    Args:
        count: Total number of mappings
    """
    MAPPINGS_TOTAL.set(count)


def record_mapping_match(mapping_name: str) -> None:
    """Record a mapping match.

    Args:
        mapping_name: Name of the matched mapping
    """
    MAPPINGS_MATCHED.labels(mapping_name=mapping_name).inc()


def record_suppressed_event(suppression_id: int, alertname: str, severity: str) -> None:
    """Record a suppressed alert event."""
    POUNDCAKE_SUPPRESSED_EVENTS.labels(
        suppression_id=str(suppression_id),
        alertname=alertname or "unknown",
        severity=severity or "unknown",
    ).inc()


def set_active_suppressions(count: int) -> None:
    """Set active suppression gauge."""
    POUNDCAKE_ACTIVE_SUPPRESSIONS.set(count)


def record_suppression_summary_ticket(status: str) -> None:
    """Record suppression summary create/close status."""
    POUNDCAKE_SUPPRESSION_SUMMARY_TICKETS.labels(status=status).inc()


def record_suppression_summary_failure() -> None:
    """Record suppression lifecycle failure."""
    POUNDCAKE_SUPPRESSION_SUMMARY_FAILURES.inc()

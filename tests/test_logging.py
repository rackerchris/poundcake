import logging
from types import SimpleNamespace

from api.core.logging import KeyValueConsoleFormatter
from api.core.middleware import request_auth_log_context, request_completion_log_level


def _format_record(message: str, **extra: object) -> str:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
        func="test_func",
    )
    record.req_id = "REQ-1"
    record.instance_id = "instance-1"
    record.service = "unit-test"
    for key, value in extra.items():
        setattr(record, key, value)
    formatter = KeyValueConsoleFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    return formatter.format(record)


def test_console_formatter_omits_empty_http_context() -> None:
    rendered = _format_record("internal event")

    assert "[NA]" not in rendered
    assert "status=NA" not in rendered
    assert "latency_ms=NA" not in rendered
    assert "internal event" in rendered


def test_console_formatter_includes_present_http_context() -> None:
    rendered = _format_record(
        "api event",
        method="GET",
        status_code=200,
        latency_ms=12,
    )

    assert "[GET]" in rendered
    assert "status=200" in rendered
    assert "latency_ms=12" in rendered


def test_console_formatter_includes_auth_context() -> None:
    rendered = _format_record(
        "api event",
        method="GET",
        status_code=200,
        latency_ms=12,
        auth_principal_type="user",
        auth_username="admin",
        auth_role="admin",
        auth_service_type=None,
        auth_principal_id=1,
    )

    assert "auth_principal_type=user" in rendered
    assert "auth_username=admin" in rendered
    assert "auth_role=admin" in rendered
    assert "auth_principal_id=1" in rendered


def test_request_auth_log_context_uses_safe_identity_fields() -> None:
    context = SimpleNamespace(
        principal_type="service",
        username="service:dishwasher",
        role="service",
        service_type="dishwasher",
        principal_id=42,
        session_id="do-not-log",
        groups=["do-not-log"],
    )

    assert request_auth_log_context(context) == {
        "auth_principal_type": "service",
        "auth_username": "service:dishwasher",
        "auth_role": "service",
        "auth_service_type": "dishwasher",
        "auth_principal_id": 42,
    }


def test_request_auth_log_context_omits_anonymous_context() -> None:
    assert request_auth_log_context(None) == {}


def test_dishwasher_success_request_logs_at_debug() -> None:
    assert (
        request_completion_log_level(
            req_id="SYSTEM-DISHWASHER",
            path="/api/v1/internal/service-registry/ingredients/bulk",
            status_code=200,
            latency_ms=12,
        )
        == logging.DEBUG
    )


def test_dishwasher_failure_request_logs_at_info() -> None:
    assert (
        request_completion_log_level(
            req_id="SYSTEM-DISHWASHER",
            path="/api/v1/internal/service-registry/ingredients/bulk",
            status_code=500,
            latency_ms=12,
        )
        == logging.INFO
    )


def test_dishwasher_slow_request_logs_at_info() -> None:
    assert (
        request_completion_log_level(
            req_id="SYSTEM-DISHWASHER",
            path="/api/v1/internal/service-registry/ingredients/bulk",
            status_code=200,
            latency_ms=1000,
        )
        == logging.INFO
    )


def test_user_request_logs_at_info() -> None:
    assert (
        request_completion_log_level(
            req_id="REQ-1",
            path="/api/v1/orders",
            status_code=200,
            latency_ms=12,
        )
        == logging.INFO
    )


def test_internal_plugin_success_request_logs_at_debug() -> None:
    assert (
        request_completion_log_level(
            req_id="SYSTEM-TIMER-123",
            path="/api/v1/dish-ingredients/in-flight",
            status_code=200,
            latency_ms=12,
        )
        == logging.DEBUG
    )


def test_internal_control_plane_path_logs_at_debug_for_order_req_id() -> None:
    assert (
        request_completion_log_level(
            req_id="alert-order-1",
            path="/api/v1/expediter/status/dummy/receipt-1",
            status_code=200,
            latency_ms=12,
        )
        == logging.DEBUG
    )


def test_internal_control_plane_path_failure_logs_at_info_for_order_req_id() -> None:
    assert (
        request_completion_log_level(
            req_id="alert-order-1",
            path="/api/v1/expediter/status/dummy/receipt-1",
            status_code=503,
            latency_ms=12,
        )
        == logging.INFO
    )


def test_successful_probe_request_logs_at_debug() -> None:
    assert (
        request_completion_log_level(
            req_id="REQ-1",
            path="/readyz",
            status_code=200,
            latency_ms=12,
        )
        == logging.DEBUG
    )


def test_failed_probe_request_logs_at_info() -> None:
    assert (
        request_completion_log_level(
            req_id="REQ-1",
            path="/readyz",
            status_code=503,
            latency_ms=12,
        )
        == logging.INFO
    )

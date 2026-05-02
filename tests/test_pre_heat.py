"""Webhook intake behavior for repeated Alertmanager alerts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from api.models.models import Order
import api.services.pre_heat as pre_heat_service
from api.services.pre_heat import _correlation_key_from_labels, pre_heat


class _ScalarResult:
    def __init__(self, value: Order | None) -> None:
        self._value = value

    def first(self) -> Order | None:
        return self._value


class _Result:
    def __init__(self, value: Order | None = None) -> None:
        self._value = value

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._value)


class _WebhookSession:
    """Small AsyncSession double for pre_heat's order upsert path."""

    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.pending_orders: list[Order] = []
        self.statements: list[Any] = []
        self.flush_errors: list[Exception] = []
        self.rollback_count = 0
        self.begin_count = 0
        self._next_id = 1

    def in_transaction(self) -> bool:
        return False

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.pending_orders.clear()
        return None

    @asynccontextmanager
    async def begin(self) -> Any:
        self.begin_count += 1
        try:
            yield self
        except Exception:
            self.pending_orders.clear()
            raise

    def add(self, order: Order) -> None:
        self.pending_orders.append(order)

    async def flush(self) -> None:
        if self.flush_errors:
            raise self.flush_errors.pop(0)
        for order in self.pending_orders:
            order.id = self._next_id
            self._next_id += 1
            self.orders.append(order)
        self.pending_orders.clear()
        return None

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        if getattr(statement, "is_update", False):
            self._apply_order_update(statement)
            return _Result()

        query = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if "orders.fingerprint =" in query and "orders.severity =" in query:
            return _Result(self._latest_warning_order())

        return _Result(self._latest_active_order())

    def _latest_active_order(self) -> Order | None:
        active = [order for order in self.orders if order.is_active]
        if not active:
            return None
        return max(active, key=lambda order: order.created_at or datetime.min)

    def _latest_warning_order(self) -> Order | None:
        warnings = [
            order
            for order in self.orders
            if order.severity == "warning" and order.fingerprint == "repeated-processing-alert-1"
        ]
        if not warnings:
            return None
        return max(warnings, key=lambda order: order.created_at or datetime.min)

    def _apply_order_update(self, statement: Any) -> None:
        order = self._latest_active_order()
        if order is None:
            return

        for column, value in statement._values.items():
            key = column.key
            if key == "counter":
                order.counter += 1
                continue
            if key == "processing_status" and getattr(value, "key", None) == "processing_status":
                continue
            if hasattr(value, "value"):
                setattr(order, key, value.value)


class _DbOrig:
    def __init__(self, code: int) -> None:
        self.args = (code, "simulated database error")


def _operational_error(code: int) -> OperationalError:
    return OperationalError("statement", {}, _DbOrig(code))


def _payload() -> dict[str, Any]:
    return {
        "receiver": "contract-test",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "RepeatedProcessingAlert",
                    "group_name": "RepeatedProcessingAlert",
                    "instance": "host-1",
                    "severity": "warning",
                },
                "annotations": {"summary": "Repeated processing alert"},
                "startsAt": "2026-05-03T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": "repeated-processing-alert-1",
            }
        ],
        "groupLabels": {"alertname": "RepeatedProcessingAlert"},
        "commonLabels": {"alertname": "RepeatedProcessingAlert"},
        "commonAnnotations": {"summary": "Repeated processing alert"},
        "externalURL": "http://alertmanager.example",
        "version": "4",
        "groupKey": "{}:{}",
        "truncatedAlerts": 0,
    }


def _payload_with(
    *,
    alert_status: str = "firing",
    severity: str = "warning",
    alertname: str = "RepeatedProcessingAlert",
) -> dict[str, Any]:
    payload = _payload()
    alert = payload["alerts"][0]
    alert["status"] = alert_status
    labels = dict(alert["labels"])
    labels["severity"] = severity
    labels["alertname"] = alertname
    alert["labels"] = labels
    if alert_status == "resolved":
        alert["endsAt"] = "2026-05-03T10:10:00Z"
    return payload


def _business_snapshot(order: Order) -> dict[str, Any]:
    ignored = {"counter", "updated_at"}
    return {
        column.name: getattr(order, column.name)
        for column in Order.__table__.columns
        if column.name not in ignored and not column.computed
    }


@pytest.mark.asyncio
async def test_warning_firing_webhook_creates_terminal_noop_order() -> None:
    db = _WebhookSession()

    result = await pre_heat(_payload_with(severity="Warning"), db, "req-warning")
    order = db.orders[0]

    assert result["status"] == "warning_recorded"
    assert result["order_id"] == order.id
    assert order.alert_status == "firing"
    assert order.processing_status == "complete"
    assert order.remediation_outcome == "none"
    assert order.is_active is False
    assert order.severity == "warning"
    assert order.counter == 1
    assert order.correlation_key == _correlation_key_from_labels(order.labels)


@pytest.mark.asyncio
async def test_repeated_warning_firing_webhook_increments_terminal_order() -> None:
    db = _WebhookSession()

    first_result = await pre_heat(_payload_with(severity="warning"), db, "req-first")
    first_order = db.orders[0]
    second_result = await pre_heat(_payload_with(severity="warning"), db, "req-second")

    assert first_result["status"] == "warning_recorded"
    assert second_result["status"] == "warning_counter_incremented"
    assert second_result["order_id"] == first_order.id
    assert len(db.orders) == 1
    assert first_order.counter == 2
    assert first_order.processing_status == "complete"
    assert first_order.remediation_outcome == "none"
    assert first_order.is_active is False


@pytest.mark.asyncio
async def test_warning_firing_webhook_uses_warning_lookup_not_active_dispatch_lookup() -> None:
    db = _WebhookSession()

    await pre_heat(_payload_with(severity="warning"), db, "req-warning")

    query = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    where_clause = query.split("WHERE", 1)[1]
    assert "orders.fingerprint =" in where_clause
    assert "orders.severity =" in where_clause
    assert "fingerprint_when_active" not in where_clause


@pytest.mark.asyncio
async def test_warning_resolved_webhook_updates_existing_terminal_order() -> None:
    db = _WebhookSession()

    await pre_heat(_payload_with(severity="warning"), db, "req-first")
    order = db.orders[0]
    result = await pre_heat(
        _payload_with(alert_status="resolved", severity="warning"),
        db,
        "req-resolved",
    )

    assert result["status"] == "warning_resolved"
    assert result["order_id"] == order.id
    assert len(db.orders) == 1
    assert order.alert_status == "resolved"
    assert order.ends_at is not None
    assert order.processing_status == "complete"
    assert order.remediation_outcome == "none"
    assert order.is_active is False


@pytest.mark.asyncio
async def test_warning_resolved_without_existing_order_is_ignored() -> None:
    db = _WebhookSession()

    result = await pre_heat(
        _payload_with(alert_status="resolved", severity="warning"),
        db,
        "req-resolved",
    )

    assert result["status"] == "ignored"
    assert result["order_id"] is None
    assert db.orders == []


@pytest.mark.asyncio
async def test_critical_firing_webhook_still_creates_active_new_order() -> None:
    db = _WebhookSession()

    result = await pre_heat(_payload_with(severity="critical"), db, "req-critical")
    order = db.orders[0]

    assert result["status"] == "created"
    assert order.processing_status == "new"
    assert order.remediation_outcome == "pending"
    assert order.is_active is True
    assert order.severity == "critical"


def test_correlation_key_ignores_alertname_and_severity() -> None:
    warning_labels = {
        "alertname": "node-md-state-warning",
        "severity": "warning",
        "group_name": "node-md-state",
        "node_hostname": "node-a",
        "device": "md0",
    }
    critical_labels = {
        **warning_labels,
        "alertname": "node-md-state-critical",
        "severity": "critical",
    }
    other_node_labels = {**warning_labels, "node_hostname": "node-b"}

    assert _correlation_key_from_labels(warning_labels) == _correlation_key_from_labels(
        critical_labels
    )
    assert _correlation_key_from_labels(warning_labels) != _correlation_key_from_labels(
        other_node_labels
    )


@pytest.mark.asyncio
async def test_repeated_firing_webhook_for_processing_order_only_increments_counter() -> None:
    db = _WebhookSession()

    first_result = await pre_heat(_payload_with(severity="critical"), db, "req-first")
    order = db.orders[0]
    order.processing_status = "processing"
    order.updated_at = datetime.now(timezone.utc)
    before = _business_snapshot(order)

    second_result = await pre_heat(_payload_with(severity="critical"), db, "req-second")
    after = _business_snapshot(order)

    assert first_result["status"] == "created"
    assert second_result["status"] == "counter_incremented"
    assert second_result["order_id"] == order.id
    assert order.counter == 2
    assert order.processing_status == "processing"
    assert before == after
    assert len(db.orders) == 1


@pytest.mark.asyncio
async def test_active_lookup_uses_generated_unique_key() -> None:
    db = _WebhookSession()

    await pre_heat(_payload_with(severity="critical"), db, "req-first")

    query = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    where_clause = query.split("WHERE", 1)[1]
    assert "fingerprint_when_active" in query
    assert "is_active" not in where_clause
    assert "ORDER BY" not in query


@pytest.mark.asyncio
async def test_retryable_deadlock_retries_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    db = _WebhookSession()
    db.flush_errors.append(_operational_error(1213))

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(pre_heat_service.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(pre_heat_service.random, "uniform", lambda _low, _high: 0)

    result = await pre_heat(_payload_with(severity="critical"), db, "req-deadlock")

    assert result["status"] == "created"
    assert db.rollback_count == 1
    assert db.begin_count == 2
    assert len(db.orders) == 1
    assert sleep_calls == [0.05]


@pytest.mark.asyncio
async def test_retryable_lock_wait_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    db = _WebhookSession()
    db.flush_errors.append(_operational_error(1205))

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(pre_heat_service.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(pre_heat_service.random, "uniform", lambda _low, _high: 0)

    result = await pre_heat(_payload_with(severity="critical"), db, "req-lock-timeout")

    assert result["status"] == "created"
    assert db.rollback_count == 1
    assert db.begin_count == 2
    assert sleep_calls == [0.05]


@pytest.mark.asyncio
async def test_non_retryable_operational_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _WebhookSession()
    db.flush_errors.append(_operational_error(1064))

    async def _fail_sleep(_seconds: float) -> None:
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(pre_heat_service.asyncio, "sleep", _fail_sleep)

    with pytest.raises(OperationalError):
        await pre_heat(_payload_with(severity="critical"), db, "req-non-retryable")

    assert db.rollback_count == 0
    assert db.begin_count == 1


@pytest.mark.asyncio
async def test_retryable_operational_error_exhausts_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    db = _WebhookSession()
    db.flush_errors.extend(
        _operational_error(1213) for _ in range(pre_heat_service.MAX_PRE_HEAT_ATTEMPTS)
    )

    async def _record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(pre_heat_service.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(pre_heat_service.random, "uniform", lambda _low, _high: 0)

    with pytest.raises(OperationalError):
        await pre_heat(_payload_with(severity="critical"), db, "req-exhausted")

    assert db.rollback_count == pre_heat_service.MAX_PRE_HEAT_ATTEMPTS
    assert db.begin_count == pre_heat_service.MAX_PRE_HEAT_ATTEMPTS
    assert sleep_calls == [0.05, 0.1]

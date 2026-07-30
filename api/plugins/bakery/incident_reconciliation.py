"""Active incident reconciliation against Prometheus alerts and Bakery ticket state.

Reconciles Order rows by comparing live Prometheus alerts to the state of
bakery communication DishIngredients.  Metadata is tracked on
DishIngredient.service_exec_actual_outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.config import get_settings
from api.core.logging import get_logger
from api.core.statuses import can_transition_to_resolving
from api.models.models import Dish, DishIngredient, Order
from api.plugins.bakery.client import (
    add_ticket_comment_with_key,
    create_ticket_with_key,
    get_communication,
    poll_operation,
    sync_communication,
    update_ticket_with_key,
)
from api.services.communications import (
    normalize_destination_target,
    normalize_destination_type,
)
from api.services.communications_policy import POLICY_METADATA_KEY
from api.services.prometheus_service import get_prometheus_client

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants – mirror the old OrderCommunication-based metadata keys
# ---------------------------------------------------------------------------
RECONCILE_ACTIVE_STATUSES = {"new", "processing", "resolving", "waiting_ticket_close"}

RECONCILE_MATCH_LABEL_KEYS = (
    "alertname",
    "group_name",
    "severity",
    "namespace",
    "cluster",
    "horizontalpodautoscaler",
    "deployment",
    "statefulset",
    "daemonset",
    "replicaset",
    "persistentvolumeclaim",
    "persistentvolume",
    "pod",
    "container",
    "node",
    "node_name",
    "k8s_node_name",
    "host_name",
    "node_hostname",
    "hostname",
    "instance",
    "job",
    "service",
)

CLEAR_NOTE_TICKET_IDS_KEY = "clear_note_ticket_ids"
LAST_CLEAR_NOTE_TICKET_ID_KEY = "last_clear_note_ticket_id"
TICKET_ALERT_NOTE_STATE_BY_TICKET_KEY = "ticket_alert_note_state_by_ticket"
TICKET_ALERT_NOTE_STATE_AT_BY_TICKET_KEY = "ticket_alert_note_state_at_by_ticket"
LAST_TICKET_ALERT_NOTE_TICKET_ID_KEY = "last_ticket_alert_note_ticket_id"
LAST_TICKET_ALERT_NOTE_STATE_KEY = "last_ticket_alert_note_state"
LAST_TICKET_ALERT_NOTE_STATE_AT_KEY = "last_ticket_alert_note_state_at"
ALERT_NOTE_STATE_FIRING = "firing"
ALERT_NOTE_STATE_RESOLVED = "resolved"
ALERT_NOTE_STATES = {ALERT_NOTE_STATE_FIRING, ALERT_NOTE_STATE_RESOLVED}

TICKET_TERMINAL_STATES = {
    "closed",
    "resolved",
    "cancelled",
    "canceled",
    "completed",
    "dead",
    "archived",
}

TICKET_REOPENABLE_STATES = {
    "closed",
    "resolved",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_metadata_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_alert_note_state(value: Any) -> str | None:
    state = str(value or "").strip().lower()
    return state if state in ALERT_NOTE_STATES else None


# -- DishIngredient as "communication route" helpers ------------------------


def _is_bakery_communication(dish_ingredient: DishIngredient) -> bool:
    return (
        str(dish_ingredient.service_type or "").strip().lower() == "bakery"
        and str(dish_ingredient.service_exec or "").strip().lower() == "communication"
        and not dish_ingredient.deleted
    )


def _ticket_id_from_ingredient(ingredient: DishIngredient) -> str | None:
    outcome = ingredient.service_exec_actual_outcome
    if isinstance(outcome, dict):
        ctx_updates = outcome.get("_context_updates") or outcome.get("context_updates")
        if isinstance(ctx_updates, dict):
            tid = ctx_updates.get("bakery_comms_id") or ctx_updates.get("bakery_ticket_id")
            if isinstance(tid, str) and tid.strip():
                return tid.strip()
    payload = ingredient.service_payload
    if isinstance(payload, dict):
        tid = payload.get("ticket_id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()
    return None


def _destination_type(ingredient: DishIngredient) -> str:
    return normalize_destination_type(ingredient.service_type)


def _destination_target(ingredient: DishIngredient) -> str:
    return normalize_destination_target(ingredient.destination_target)


def _ingredient_metadata(ingredient: DishIngredient) -> dict[str, Any]:
    outcome = ingredient.service_exec_actual_outcome
    if isinstance(outcome, dict):
        recon = outcome.get("reconcile_metadata")
        if isinstance(recon, dict):
            return dict(recon)
    return {}


def _set_ingredient_metadata(ingredient: DishIngredient, metadata: dict[str, Any]) -> None:
    outcome = (
        dict(ingredient.service_exec_actual_outcome)
        if isinstance(ingredient.service_exec_actual_outcome, dict)
        else {}
    )
    outcome["reconcile_metadata"] = metadata
    ingredient.service_exec_actual_outcome = outcome


# -- Metadata helpers (ported from old code) --------------------------------


def _clear_note_ticket_ids(metadata: dict[str, Any]) -> set[str]:
    ticket_ids: set[str] = set()
    value = metadata.get(CLEAR_NOTE_TICKET_IDS_KEY)
    if isinstance(value, list):
        ticket_ids.update(str(item).strip() for item in value if str(item or "").strip())
    legacy = str(metadata.get(LAST_CLEAR_NOTE_TICKET_ID_KEY) or "").strip()
    if legacy:
        ticket_ids.add(legacy)
    return ticket_ids


def _has_clear_note_for_ticket(metadata: dict[str, Any], ticket_id: str) -> bool:
    return ticket_id in _clear_note_ticket_ids(metadata)


def _remember_clear_note_for_ticket(metadata: dict[str, Any], ticket_id: str) -> None:
    ticket_ids = _clear_note_ticket_ids(metadata)
    ticket_ids.add(ticket_id)
    metadata[CLEAR_NOTE_TICKET_IDS_KEY] = sorted(ticket_ids)
    metadata[LAST_CLEAR_NOTE_TICKET_ID_KEY] = ticket_id


def _ticket_alert_note_state(metadata: dict[str, Any], ticket_id: str) -> str | None:
    value = metadata.get(TICKET_ALERT_NOTE_STATE_BY_TICKET_KEY)
    if isinstance(value, dict):
        state = _normalize_alert_note_state(value.get(ticket_id))
        if state:
            return state
    if str(metadata.get(LAST_TICKET_ALERT_NOTE_TICKET_ID_KEY) or "").strip() == ticket_id:
        state = _normalize_alert_note_state(metadata.get(LAST_TICKET_ALERT_NOTE_STATE_KEY))
        if state:
            return state
    if _has_clear_note_for_ticket(metadata, ticket_id):
        return ALERT_NOTE_STATE_RESOLVED
    return None


def _ticket_alert_note_state_at(metadata: dict[str, Any], ticket_id: str) -> datetime | None:
    value = metadata.get(TICKET_ALERT_NOTE_STATE_AT_BY_TICKET_KEY)
    if isinstance(value, dict):
        parsed = _coerce_metadata_datetime(value.get(ticket_id))
        if parsed:
            return parsed
    if str(metadata.get(LAST_TICKET_ALERT_NOTE_TICKET_ID_KEY) or "").strip() == ticket_id:
        return _coerce_metadata_datetime(metadata.get(LAST_TICKET_ALERT_NOTE_STATE_AT_KEY))
    return None


def _remember_ticket_alert_note_state(
    metadata: dict[str, Any],
    ticket_id: str,
    state: str,
) -> None:
    normalized_state = _normalize_alert_note_state(state)
    if not normalized_state:
        return
    states = metadata.get(TICKET_ALERT_NOTE_STATE_BY_TICKET_KEY)
    state_by_ticket = dict(states) if isinstance(states, dict) else {}
    state_by_ticket[ticket_id] = normalized_state

    state_at = _now().isoformat()
    times = metadata.get(TICKET_ALERT_NOTE_STATE_AT_BY_TICKET_KEY)
    state_at_by_ticket = dict(times) if isinstance(times, dict) else {}
    state_at_by_ticket[ticket_id] = state_at

    metadata[TICKET_ALERT_NOTE_STATE_BY_TICKET_KEY] = state_by_ticket
    metadata[TICKET_ALERT_NOTE_STATE_AT_BY_TICKET_KEY] = state_at_by_ticket
    metadata[LAST_TICKET_ALERT_NOTE_TICKET_ID_KEY] = ticket_id
    metadata[LAST_TICKET_ALERT_NOTE_STATE_KEY] = normalized_state
    metadata[LAST_TICKET_ALERT_NOTE_STATE_AT_KEY] = state_at
    if normalized_state == ALERT_NOTE_STATE_RESOLVED:
        _remember_clear_note_for_ticket(metadata, ticket_id)


# -- Route metadata extraction (ported from old code) -----------------------


def _matching_ingredient(
    dish_ingredient: DishIngredient, destination_type: str, destination_target: str
) -> bool:
    return (
        str(dish_ingredient.service_type or "").strip().lower() == "bakery"
        and normalize_destination_type(dish_ingredient.service_type) == destination_type
        and normalize_destination_target(dish_ingredient.destination_target) == destination_target
    )


def _extract_route_metadata(
    order: Order,
    ingredient: DishIngredient,
) -> dict[str, Any]:
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    metadata = dict(_ingredient_metadata(ingredient))
    if not metadata.get("route_label"):
        policy_label = metadata.get("label")
        if isinstance(policy_label, str) and policy_label.strip():
            metadata["route_label"] = policy_label.strip()

    if all(str(metadata.get(key) or "").strip() for key in ("scope", "owner_key", "route_id")):
        return metadata

    dishes = sorted(
        list(order.dishes or []),
        key=lambda item: (item.created_at or _now(), item.id or 0),
        reverse=True,
    )
    for dish in dishes:
        items = sorted(
            list(dish.dish_ingredients or []),
            key=lambda item: (item.created_at or _now(), item.id or 0),
            reverse=True,
        )
        for item in items:
            if not _matching_ingredient(item, dest_type, dest_target):
                continue
            payload = item.service_payload if isinstance(item.service_payload, dict) else {}
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            provider_config = context.get("provider_config")
            if isinstance(provider_config, dict) and provider_config:
                metadata.setdefault("provider_config", dict(provider_config))
            policy_metadata = context.get(POLICY_METADATA_KEY)
            if isinstance(policy_metadata, dict) and policy_metadata:
                for key in (
                    "scope",
                    "owner_key",
                    "route_id",
                    "label",
                    "execution_target",
                    "destination_target",
                ):
                    value = policy_metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        metadata.setdefault(key, value.strip())
                policy_provider_config = policy_metadata.get("provider_config")
                if isinstance(policy_provider_config, dict) and policy_provider_config:
                    metadata.setdefault("provider_config", dict(policy_provider_config))
            route_label = context.get("route_label")
            if isinstance(route_label, str) and route_label.strip():
                metadata.setdefault("route_label", route_label.strip())
            if not metadata.get("route_label"):
                policy_label = metadata.get("label")
                if isinstance(policy_label, str) and policy_label.strip():
                    metadata.setdefault("route_label", policy_label.strip())
            if all(
                str(metadata.get(key) or "").strip() for key in ("scope", "owner_key", "route_id")
            ):
                return metadata
    return metadata


# -- Alert matching --------------------------------------------------------


def _matching_labels(order: Order) -> dict[str, str]:
    labels = dict(order.labels or {})
    result: dict[str, str] = {}
    for key in RECONCILE_MATCH_LABEL_KEYS:
        value = labels.get(key)
        if value not in (None, ""):
            result[key] = str(value)
    return result


def _alert_matches_order(order: Order, alert: dict[str, Any]) -> bool:
    if not isinstance(alert, dict):
        return False
    labels = alert.get("labels")
    if not isinstance(labels, dict):
        return False

    alert_fingerprint = str(alert.get("fingerprint") or "").strip()
    if alert_fingerprint and alert_fingerprint == str(order.fingerprint or "").strip():
        return True

    expected = _matching_labels(order)
    if not expected:
        return False
    for key, value in expected.items():
        current = labels.get(key)
        if current in (None, ""):
            return False
        if str(current) != value:
            return False
    return True


def _alert_is_firing(order: Order, alerts: list[dict[str, Any]]) -> bool:
    for alert in alerts:
        state = str(alert.get("state") or "").strip().lower()
        if state and state != "firing":
            continue
        if _alert_matches_order(order, alert):
            return True
    return False


# -- Ticket state helpers --------------------------------------------------


async def _poll_ticket_state(ticket_id: str) -> tuple[str | None, bool, bool]:
    """Return (state, writable, reopenable) for a Bakery ticket."""
    try:
        comm = await sync_communication(ticket_id)
    except Exception:
        try:
            comm = await get_communication(ticket_id)
        except Exception:
            return None, False, False

    state = str(comm.state or "").strip().lower()
    is_terminal = state in TICKET_TERMINAL_STATES
    is_reopenable = state in TICKET_REOPENABLE_STATES
    return state, not is_terminal, is_reopenable


# -- Ticket note / payload builders ----------------------------------------


def _headline(order: Order) -> str:
    annotations = dict(order.annotations or {})
    return str(annotations.get("summary") or order.alert_group_name or "Alert").strip()


def _description(order: Order) -> str:
    annotations = dict(order.annotations or {})
    return str(
        annotations.get("description")
        or annotations.get("summary")
        or f"Alert {order.alert_group_name} is active."
    ).strip()


def _firing_ticket_note(order: Order, ingredient: DishIngredient) -> str:
    route = _extract_route_metadata(order, ingredient)
    route_label = str(route.get("route_label") or _destination_type(ingredient)).strip()
    return (
        f"Alert {order.alert_group_name} is still firing. PoundCake reopened or recreated the "
        f"{route_label} incident because it cannot be closed until the alert clears."
    )


def _refire_ticket_note(order: Order, ingredient: DishIngredient) -> str:
    route = _extract_route_metadata(order, ingredient)
    route_label = str(route.get("route_label") or _destination_type(ingredient)).strip()
    return (
        f"Alert {order.alert_group_name} is firing again after previously clearing. "
        "PoundCake did not remediate or validate a fix. The "
        f"{route_label} ticket remains open; this update documents the renewed alert "
        "in the existing incident."
    )


def _clear_ticket_note(order: Order, ingredient: DishIngredient) -> str:
    route = _extract_route_metadata(order, ingredient)
    route_label = str(route.get("route_label") or _destination_type(ingredient)).strip()
    return (
        f"Alert {order.alert_group_name} is no longer firing, but PoundCake did not remediate "
        f"or validate a fix. The {route_label} ticket remains open for human "
        "investigation; PoundCake will keep the incident active until the ticket is closed."
    )


def _reconcile_context(order: Order, ingredient: DishIngredient) -> dict[str, Any]:
    route = _extract_route_metadata(order, ingredient)
    context: dict[str, Any] = {
        "source": "poundcake_system",
        "provider_type": _destination_type(ingredient),
        "execution_target": _destination_type(ingredient),
        "destination_target": _destination_target(ingredient),
        "route_label": str(route.get("route_label") or _destination_type(ingredient)).strip(),
        "labels": dict(order.labels or {}),
        "annotations": dict(order.annotations or {}),
    }
    provider_config = route.get("provider_config")
    if isinstance(provider_config, dict) and provider_config:
        context["provider_config"] = dict(provider_config)
    if all(str(route.get(key) or "").strip() for key in ("scope", "owner_key", "route_id")):
        context[POLICY_METADATA_KEY] = {
            "scope": str(route.get("scope") or "").strip(),
            "owner_key": str(route.get("owner_key") or "").strip(),
            "route_id": str(route.get("route_id") or "").strip(),
            "label": str(route.get("route_label") or route.get("label") or "").strip(),
            "execution_target": str(route.get("execution_target") or _destination_type(ingredient)),
            "destination_target": str(
                route.get("destination_target") or _destination_target(ingredient)
            ),
            "provider_config": dict(provider_config or {}),
        }
    return context


def _open_payload(order: Order, ingredient: DishIngredient) -> dict[str, Any]:
    context = _reconcile_context(order, ingredient)
    return {
        "title": _headline(order),
        "description": _description(order),
        "message": _firing_ticket_note(order, ingredient),
        "source": "poundcake_system",
        "context": context,
    }


def _reopen_payload(target: str) -> dict[str, Any]:
    if target == "rackspace_core":
        return {"context": {"attributes": {"status": "New"}}}
    return {"state": "open"}


# -- Resolving step helpers ------------------------------------------------


def _has_active_resolving_route_step(
    order: Order,
    ingredient: DishIngredient,
) -> bool:
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    for dish in list(order.dishes or []):
        if str(dish.run_phase or "").strip().lower() != "resolving":
            continue
        if str(dish.processing_status or "").strip().lower() in {
            "complete",
            "failed",
            "errored",
            "timeout",
            "canceled",
            "finalizing",
        }:
            continue
        for step in list(dish.dish_ingredients or []):
            if step.deleted:
                continue
            if str(step.service_type or "").strip().lower() != "bakery":
                continue
            if normalize_destination_type(step.service_type) != dest_type:
                continue
            if normalize_destination_target(step.destination_target) != dest_target:
                continue
            return True
    return False


def _has_completed_resolving_notify_step(
    order: Order,
    ingredient: DishIngredient,
    *,
    since: datetime | None = None,
) -> bool:
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    for dish in list(order.dishes or []):
        if str(dish.run_phase or "").strip().lower() != "resolving":
            continue
        for step in list(dish.dish_ingredients or []):
            if step.deleted:
                continue
            if str(step.service_type or "").strip().lower() != "bakery":
                continue
            if normalize_destination_type(step.service_type) != dest_type:
                continue
            if normalize_destination_target(step.destination_target) != dest_target:
                continue
            params = (
                step.service_exec_parameters
                if isinstance(step.service_exec_parameters, dict)
                else {}
            )
            operation = str(params.get("operation") or "").strip().lower()
            status = str(step.service_exec_status or "").strip().lower()
            if operation == "notify" and status in {"succeeded", "success", "completed"}:
                if since is not None:
                    completed_at = (
                        _coerce_metadata_datetime(
                            getattr(step, "service_exec_completed_time", None)
                        )
                        or _coerce_metadata_datetime(getattr(step, "updated_at", None))
                        or _coerce_metadata_datetime(getattr(step, "created_at", None))
                    )
                    if completed_at is None or completed_at < since:
                        continue
                return True
    return False


def _has_open_ticket_routes(order: Order) -> bool:
    for dish in order.dishes or []:
        for ingredient in dish.dish_ingredients or []:
            if not _is_bakery_communication(ingredient):
                continue
            ticket_id = _ticket_id_from_ingredient(ingredient)
            if not ticket_id:
                continue
            metadata = _ingredient_metadata(ingredient)
            remote_state = metadata.get("remote_state")
            if remote_state and str(remote_state).strip().lower() in TICKET_TERMINAL_STATES:
                continue
            return True
    return False


# -- Bakery operation helpers ----------------------------------------------


async def _await_bakery_operation(operation_id: str) -> tuple[bool, str | None]:
    payload = await poll_operation(operation_id)
    status = str(payload.status or "").strip().lower()
    if status in {"succeeded", "success", "completed"}:
        return True, None
    return False, str(
        payload.last_error or f"Bakery operation ended in status={status or 'unknown'}"
    )


# ---------------------------------------------------------------------------
# Reconciliation actions
# ---------------------------------------------------------------------------


async def _reopen_or_recreate_ticket(
    *,
    order: Order,
    ingredient: DishIngredient,
    ticket_id: str,
    req_id: str,
    actions: list[str],
) -> None:
    metadata = _ingredient_metadata(ingredient)
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    _, _, reopenable = await _poll_ticket_state(ticket_id)

    if reopenable:
        if metadata.get("last_reopen_ticket_id") == ticket_id:
            _set_ingredient_metadata(ingredient, metadata)
            return
        accepted = await update_ticket_with_key(
            req_id=req_id,
            ticket_id=ticket_id,
            payload=_reopen_payload(dest_type),
            idempotency_key=None,
        )
        success, error = await _await_bakery_operation(accepted.operation_id)
        if not success:
            metadata["last_error"] = error
            _set_ingredient_metadata(ingredient, metadata)
            return
        metadata["remote_state"] = "open"
        metadata["writable"] = True
        metadata["reopenable"] = False

        note = await add_ticket_comment_with_key(
            req_id=req_id,
            ticket_id=ticket_id,
            payload={
                "comment": _firing_ticket_note(order, ingredient),
                "context": _reconcile_context(order, ingredient),
            },
            idempotency_key=None,
        )
        note_success, note_error = await _await_bakery_operation(note.operation_id)
        if not note_success:
            metadata["last_error"] = note_error
            _set_ingredient_metadata(ingredient, metadata)
            return

        try:
            refreshed = await sync_communication(ticket_id)
            metadata["remote_state"] = str(refreshed.state or "").strip().lower()
        except Exception:
            pass

        metadata["last_reopen_ticket_id"] = ticket_id
        _remember_ticket_alert_note_state(metadata, ticket_id, ALERT_NOTE_STATE_FIRING)
        _set_ingredient_metadata(ingredient, metadata)
        actions.append(f"reopened:{dest_type}:{dest_target}")
        return

    if metadata.get("last_successor_from_ticket_id") == ticket_id:
        _set_ingredient_metadata(ingredient, metadata)
        return

    accepted = await create_ticket_with_key(
        req_id=req_id,
        payload=_open_payload(order, ingredient),
        idempotency_key=None,
    )
    success, error = await _await_bakery_operation(accepted.operation_id)
    if not success:
        metadata["last_error"] = error
        _set_ingredient_metadata(ingredient, metadata)
        return

    new_ticket_id = accepted.ticket_id.strip()
    metadata["remote_state"] = "open"
    metadata["writable"] = True
    metadata["reopenable"] = False
    metadata["last_successor_from_ticket_id"] = ticket_id
    if new_ticket_id:
        metadata["current_ticket_id"] = new_ticket_id
        _remember_ticket_alert_note_state(metadata, new_ticket_id, ALERT_NOTE_STATE_FIRING)

    _set_ingredient_metadata(ingredient, metadata)
    actions.append(f"recreated:{dest_type}:{dest_target}:{new_ticket_id}")


async def _notify_refire_ticket(
    *,
    db: AsyncSession,
    order: Order,
    ingredient: DishIngredient,
    ticket_id: str,
    req_id: str,
    actions: list[str],
) -> None:
    metadata = _ingredient_metadata(ingredient)
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    if _ticket_alert_note_state(metadata, ticket_id) == ALERT_NOTE_STATE_FIRING:
        _set_ingredient_metadata(ingredient, metadata)
        return

    _remember_ticket_alert_note_state(metadata, ticket_id, ALERT_NOTE_STATE_FIRING)
    _set_ingredient_metadata(ingredient, metadata)
    await db.flush()

    try:
        accepted = await add_ticket_comment_with_key(
            req_id=req_id,
            ticket_id=ticket_id,
            payload={
                "comment": _refire_ticket_note(order, ingredient),
                "context": _reconcile_context(order, ingredient),
            },
            idempotency_key=None,
        )
    except Exception as exc:
        logger.warning(
            "Failed to submit refire ticket note after idempotency claim",
            extra={
                "req_id": req_id,
                "order_id": order.id,
                "ticket_id": ticket_id,
                "error": str(exc),
            },
        )
        metadata["last_error"] = str(exc)
        _set_ingredient_metadata(ingredient, metadata)
        await db.flush()
        return

    success, error = await _await_bakery_operation(accepted.operation_id)
    if not success:
        metadata["last_error"] = error
        _set_ingredient_metadata(ingredient, metadata)
        return

    try:
        refreshed = await sync_communication(ticket_id)
        metadata["remote_state"] = str(refreshed.state or "").strip().lower()
    except Exception:
        pass

    _set_ingredient_metadata(ingredient, metadata)
    actions.append(f"notified_firing:{dest_type}:{dest_target}")


async def _notify_clear_ticket(
    *,
    db: AsyncSession,
    order: Order,
    ingredient: DishIngredient,
    ticket_id: str,
    req_id: str,
    actions: list[str],
) -> None:
    metadata = _ingredient_metadata(ingredient)
    dest_type = _destination_type(ingredient)
    dest_target = _destination_target(ingredient)

    if _ticket_alert_note_state(metadata, ticket_id) == ALERT_NOTE_STATE_RESOLVED:
        _set_ingredient_metadata(ingredient, metadata)
        return

    _remember_ticket_alert_note_state(metadata, ticket_id, ALERT_NOTE_STATE_RESOLVED)
    _set_ingredient_metadata(ingredient, metadata)
    await db.flush()

    try:
        accepted = await add_ticket_comment_with_key(
            req_id=req_id,
            ticket_id=ticket_id,
            payload={
                "comment": _clear_ticket_note(order, ingredient),
                "context": _reconcile_context(order, ingredient),
            },
            idempotency_key=None,
        )
    except Exception as exc:
        logger.warning(
            "Failed to submit clear ticket note after idempotency claim",
            extra={
                "req_id": req_id,
                "order_id": order.id,
                "ticket_id": ticket_id,
                "error": str(exc),
            },
        )
        metadata["last_error"] = str(exc)
        _set_ingredient_metadata(ingredient, metadata)
        await db.flush()
        return

    success, error = await _await_bakery_operation(accepted.operation_id)
    if not success:
        metadata["last_error"] = error
        _set_ingredient_metadata(ingredient, metadata)
        return

    try:
        refreshed = await sync_communication(ticket_id)
        metadata["remote_state"] = str(refreshed.state or "").strip().lower()
    except Exception:
        pass

    _set_ingredient_metadata(ingredient, metadata)
    actions.append(f"notified_clear:{dest_type}:{dest_target}")


# ---------------------------------------------------------------------------
# Single-order reconciliation
# ---------------------------------------------------------------------------


async def _reconcile_single_order(
    db: AsyncSession,
    *,
    order: Order,
    req_id: str,
) -> dict[str, Any]:
    """Reconcile one order against Prometheus and Bakery ticket state."""

    result: dict[str, Any] = {
        "order_id": order.id,
        "processing_status": order.processing_status,
        "alert_status": order.alert_status,
        "actions": [],
    }

    ps = str(order.processing_status or "").strip().lower()
    if ps in {"complete", "failed", "errored", "timeout", "canceled"}:
        result["status"] = "skipped"
        return result

    now = _now()

    # -- Fetch Prometheus alerts -------------------------------------------
    try:
        alerts = await get_prometheus_client().get_alerts()
    except Exception as exc:
        logger.warning(
            "Prometheus unavailable during reconciliation",
            extra={"req_id": req_id, "order_id": order.id, "error": str(exc)},
        )
        result["status"] = "deferred"
        result["reason"] = "prometheus_unavailable"
        return result

    if alerts is None:
        result["status"] = "deferred"
        result["reason"] = "prometheus_unavailable"
        return result

    alert_firing = _alert_is_firing(order, alerts)
    result["observed_alert_status"] = "firing" if alert_firing else "resolved"

    previous_processing_status = ps
    previous_alert_status = str(order.alert_status or "").strip().lower()

    refired_existing_incident = alert_firing and (
        previous_processing_status in {"resolving", "waiting_ticket_close"}
        or previous_alert_status == "resolved"
    )

    # -- Update order alert / processing status ----------------------------
    if alert_firing:
        order.alert_status = "firing"
        order.ends_at = None
        order.is_active = True
        if previous_processing_status in {"resolving", "waiting_ticket_close"}:
            order.processing_status = "new"
            result["actions"].append("redispatch_firing")
    else:
        order.alert_status = "resolved"
        order.ends_at = order.ends_at or now
        order.is_active = True
        if can_transition_to_resolving(order.processing_status, "alert_resolved"):
            if str(order.processing_status or "").strip().lower() != "resolving":
                order.processing_status = "resolving"
                result["actions"].append("dispatch_resolving")

    # -- Collect bakery communication ingredients --------------------------
    comm_ingredients: list[DishIngredient] = []
    for dish in order.dishes or []:
        for ing in dish.dish_ingredients or []:
            if _is_bakery_communication(ing):
                comm_ingredients.append(ing)

    # -- Sync remote ticket state ------------------------------------------
    sync_errors = 0
    for ingredient in comm_ingredients:
        ticket_id = _ticket_id_from_ingredient(ingredient)
        if not ticket_id:
            continue
        try:
            state, writable, reopenable = await _poll_ticket_state(ticket_id)
            metadata = _ingredient_metadata(ingredient)
            metadata["remote_state"] = state
            metadata["writable"] = writable
            metadata["reopenable"] = reopenable
            _set_ingredient_metadata(ingredient, metadata)
        except Exception as exc:
            logger.warning(
                "Failed to sync ticket state during reconciliation",
                extra={
                    "req_id": req_id,
                    "order_id": order.id,
                    "ticket_id": ticket_id,
                    "error": str(exc),
                },
            )
            sync_errors += 1

    if sync_errors:
        order.updated_at = now
        await db.flush()
        result["status"] = "deferred"
        result["reason"] = "bakery_sync_partial_failure"
        result["sync_errors"] = sync_errors
        return result

    # -- Per-ticket reconciliation -----------------------------------------
    for ingredient in comm_ingredients:
        ticket_id = _ticket_id_from_ingredient(ingredient)
        if not ticket_id:
            continue
        metadata = _ingredient_metadata(ingredient)
        remote_state = str(metadata.get("remote_state") or "").strip().lower()
        is_terminal = remote_state in TICKET_TERMINAL_STATES

        if alert_firing:
            if is_terminal:
                await _reopen_or_recreate_ticket(
                    order=order,
                    ingredient=ingredient,
                    ticket_id=ticket_id,
                    req_id=req_id,
                    actions=result["actions"],
                )
            else:
                metadata = _ingredient_metadata(ingredient)
                metadata.pop("last_reopen_ticket_id", None)
                _set_ingredient_metadata(ingredient, metadata)
                if refired_existing_incident:
                    await _notify_refire_ticket(
                        db=db,
                        order=order,
                        ingredient=ingredient,
                        ticket_id=ticket_id,
                        req_id=req_id,
                        actions=result["actions"],
                    )
        else:
            if _has_active_resolving_route_step(order, ingredient):
                continue
            metadata = _ingredient_metadata(ingredient)
            last_note_state = _ticket_alert_note_state(metadata, ticket_id)
            last_note_state_at = _ticket_alert_note_state_at(metadata, ticket_id)
            completed_notify_since = (
                last_note_state_at if last_note_state == ALERT_NOTE_STATE_FIRING else None
            )
            completed_notify_already_ran = (
                False
                if last_note_state == ALERT_NOTE_STATE_FIRING and last_note_state_at is None
                else _has_completed_resolving_notify_step(
                    order,
                    ingredient,
                    since=completed_notify_since,
                )
            )
            if completed_notify_already_ran:
                if last_note_state != ALERT_NOTE_STATE_RESOLVED:
                    _remember_ticket_alert_note_state(
                        metadata, ticket_id, ALERT_NOTE_STATE_RESOLVED
                    )
                    _set_ingredient_metadata(ingredient, metadata)
                continue
            if not is_terminal:
                await _notify_clear_ticket(
                    db=db,
                    order=order,
                    ingredient=ingredient,
                    ticket_id=ticket_id,
                    req_id=req_id,
                    actions=result["actions"],
                )

    # -- Auto-complete if resolved + all tickets closed --------------------
    if not alert_firing and str(order.processing_status or "").strip().lower() in {
        "resolving",
        "waiting_ticket_close",
    }:
        if not _has_open_ticket_routes(order):
            order.processing_status = "complete"
            order.is_active = False
            result["actions"].append("complete_incident")

    order.updated_at = now
    await db.flush()

    result["status"] = "reconciled"
    result["processing_status"] = order.processing_status
    result["alert_status"] = order.alert_status
    result["has_open_ticket_routes"] = _has_open_ticket_routes(order)
    return result


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


async def reconcile_active_orders(
    db: AsyncSession,
    *,
    req_id: str,
    limit: int,
) -> dict:
    """Reconcile active orders against Prometheus alerts and Bakery ticket state.

    Args:
        db: Async database session.
        req_id: Request trace ID.
        limit: Maximum number of orders to reconcile per invocation.

    Returns:
        Dictionary summarizing the reconciliation run.
    """
    settings = get_settings()

    if not getattr(settings, "incident_reconcile_enabled", True):
        logger.info(
            "Incident reconciliation skipped — disabled via config",
            extra={"req_id": req_id},
        )
        return {
            "status": "skipped",
            "reason": "disabled",
            "orders_processed": 0,
            "orders": [],
        }

    effective_limit = min(limit, getattr(settings, "incident_reconcile_limit", 25) or 25)

    # -- Load active orders with dishes + ingredients eagerly loaded --------
    stmt = (
        select(Order)
        .where(Order.processing_status.in_(RECONCILE_ACTIVE_STATUSES))
        .where(Order.is_active.is_(True))
        .order_by(Order.created_at.asc())
        .limit(effective_limit)
        .options(
            selectinload(Order.dishes).selectinload(Dish.dish_ingredients),
        )
    )
    result_set = await db.execute(stmt)
    orders = result_set.scalars().all()

    if not orders:
        return {
            "status": "idle",
            "orders_processed": 0,
            "orders": [],
        }

    order_results: list[dict[str, Any]] = []
    reconciled = 0
    deferred = 0
    skipped = 0

    for order in orders:
        try:
            ordr = await _reconcile_single_order(
                db,
                order=order,
                req_id=req_id,
            )
            status = ordr.get("status", "unknown")
            if status == "reconciled":
                reconciled += 1
            elif status == "deferred":
                deferred += 1
            elif status == "skipped":
                skipped += 1
            order_results.append(ordr)
        except Exception as exc:
            logger.error(
                "Unexpected error during order reconciliation",
                extra={
                    "req_id": req_id,
                    "order_id": order.id,
                    "error": str(exc),
                },
            )
            order_results.append(
                {
                    "order_id": order.id,
                    "status": "error",
                    "error": str(exc),
                }
            )
            deferred += 1

    await db.commit()

    return {
        "status": "complete",
        "orders_processed": len(orders),
        "reconciled": reconciled,
        "deferred": deferred,
        "skipped": skipped,
        "orders": order_results,
    }

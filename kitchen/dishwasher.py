#!/usr/bin/env python3
#  ____                        _  ____      _
# |  _ \\ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \\| | | | '_ \\ / _` | |   / _` | |/ / _ \\
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \\___/ \\__,_|_| |_|\\__,_|\\____\\__,_|_|\\_\\___|
#
"""Dishwasher: scheduled control-plane task crawler."""

import hashlib
import json
import os
import re
import time
from urllib.parse import quote
from uuid import uuid4

from api.core.logging import setup_logging, get_logger
from api.core.config import get_settings
from api.core.time import utc_now_db
from api.plugins.catalog import (
    get_enabled_plugin_communication_routes,
    get_enabled_plugins,
    get_enabled_plugin_ingredient_templates,
    get_enabled_plugin_recipe_templates,
    get_enabled_plugin_scheduled_task_templates,
)
from api.types import JSONObject
from api.types import SCHEDULED_TASK_ORDER_TYPE
from kitchen.service_helpers import (
    get_worker_runtime_config,
    request_control_plane_sync,
    wait_for_api,
)

setup_logging()
logger = get_logger("dishwasher")

POUNDCAKE_API_URL = os.getenv("POUNDCAKE_API_URL", "http://poundcake:8080").rstrip("/")
API_BASE_URL = f"{POUNDCAKE_API_URL}/api/v1"
DISHWASHER_INTERVAL = int(os.getenv("DISHWASHER_INTERVAL", "0"))
SCHEDULED_TASK_LIMIT = int(os.getenv("DISHWASHER_TASK_LIMIT", "25"))
MARK_BOOTSTRAP = os.getenv("POUNDCAKE_BOOTSTRAP_MARK", "false").lower() == "true"

SYSTEM_REQ_ID = "SYSTEM-DISHWASHER"
POLLER_RETRIES = get_settings().poller_http_retries


def _ingredient_identity(payload: JSONObject) -> tuple[str, str, str, str]:
    return (
        str(payload.get("service_type") or "").strip().lower(),
        str(payload.get("service_exec") or "").strip(),
        str(payload.get("destination_target") or "").strip(),
        str(payload.get("task_key_template") or "").strip(),
    )


def _now():
    return utc_now_db()


def _enabled_service_types() -> str:
    return ",".join(plugin.service_type for plugin in get_enabled_plugins())


def _core_scheduled_task_templates() -> list[JSONObject]:
    return []


def _scheduled_task_templates() -> list[JSONObject]:
    return [
        *_core_scheduled_task_templates(),
        *get_enabled_plugin_scheduled_task_templates(),
    ]


def _recipe_payload(
    recipe: JSONObject,
    ingredient_ids: dict[tuple[str, str, str, str], int],
) -> JSONObject:
    steps: list[JSONObject] = []
    for step in recipe.get("recipe_ingredients") or []:
        if not isinstance(step, dict):
            raise ValueError(f"Recipe {recipe.get('name')} contains a non-object step")
        identity = _ingredient_identity(step)
        ingredient_id = ingredient_ids.get(identity)
        if ingredient_id is None:
            raise ValueError(
                f"Recipe {recipe.get('name')} references unregistered ingredient {identity}"
            )
        step_payload = {
            "ingredient_id": ingredient_id,
            "step_order": step.get("step_order", 1),
            "on_success": step.get("on_success", "continue"),
            "parallel_group": step.get("parallel_group", 0),
            "depth": step.get("depth", 0),
            "service_payload": step.get("service_payload"),
            "service_exec_parameters_override": step.get("service_exec_parameters_override"),
            "service_exec_expected_secs": step.get("service_exec_expected_secs"),
            "service_exec_timeout": step.get("service_exec_timeout"),
            "service_exec_expected_outcome": step.get("service_exec_expected_outcome"),
            "run_phase": step.get("run_phase", "both"),
            "run_condition": step.get("run_condition", "always"),
        }
        if bool(step.get("service_payload_from_order")):
            step_payload["service_payload_from_order"] = True
        steps.append(step_payload)
    return {
        "name": recipe["name"],
        "description": recipe.get("description"),
        "enabled": recipe.get("enabled", True),
        "clear_timeout_sec": recipe.get("clear_timeout_sec"),
        "recipe_ingredients": steps,
    }


def _normalized_recipe_step(step: JSONObject) -> JSONObject:
    return {
        "ingredient_id": int(step["ingredient_id"]),
        "step_order": int(step.get("step_order") or 1),
        "on_success": step.get("on_success") or "continue",
        "parallel_group": int(step.get("parallel_group") or 0),
        "depth": int(step.get("depth") or 0),
        "service_payload": step.get("service_payload"),
        "service_exec_parameters_override": step.get("service_exec_parameters_override"),
        "service_exec_expected_secs": step.get("service_exec_expected_secs"),
        "service_exec_timeout": step.get("service_exec_timeout"),
        "service_exec_expected_outcome": step.get("service_exec_expected_outcome"),
        "run_phase": step.get("run_phase") or "both",
        "run_condition": step.get("run_condition") or "always",
    }


def _recipe_contract(recipe: JSONObject) -> JSONObject:
    steps = [
        _normalized_recipe_step(step)
        for step in recipe.get("recipe_ingredients") or []
        if isinstance(step, dict) and step.get("ingredient_id") is not None
    ]
    return {
        "name": recipe.get("name"),
        "description": recipe.get("description"),
        "enabled": bool(recipe.get("enabled", True)),
        "clear_timeout_sec": recipe.get("clear_timeout_sec"),
        "recipe_ingredients": sorted(
            steps,
            key=lambda step: (
                int(step["step_order"]),
                int(step["depth"]),
                int(step["parallel_group"]),
                int(step["ingredient_id"]),
                json.dumps(step, sort_keys=True, separators=(",", ":"), default=str),
            ),
        ),
    }


def _recipe_contract_hash(recipe: JSONObject) -> str:
    encoded = json.dumps(
        _recipe_contract(recipe),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _recipe_contracts_match(left: JSONObject, right: JSONObject) -> bool:
    return _recipe_contract_hash(left) == _recipe_contract_hash(right)


def _sync_plugin_recipes(
    *,
    ingredient_rows: list[JSONObject],
    req_id: str,
) -> JSONObject:
    ingredient_ids = {
        _ingredient_identity(row): int(row["id"]) for row in ingredient_rows if "id" in row
    }
    stats: JSONObject = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

    for recipe_template in get_enabled_plugin_recipe_templates():
        payload = _recipe_payload(recipe_template, ingredient_ids)
        name = str(payload["name"])
        lookup = request_control_plane_sync(
            "GET",
            f"{API_BASE_URL}/recipes/by-name/{quote(name, safe=':')}",
            req_id=req_id,
            timeout=30,
            retries=POLLER_RETRIES,
        )
        if lookup.status_code == 404:
            response = request_control_plane_sync(
                "POST",
                f"{API_BASE_URL}/recipes/",
                json=payload,
                req_id=req_id,
                timeout=60,
                retries=POLLER_RETRIES,
            )
            if response.status_code not in (200, 201):
                stats["errors"] = int(stats["errors"]) + 1
                logger.error(
                    "Plugin recipe create failed",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "recipe_name": name,
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                continue
            stats["created"] = int(stats["created"]) + 1
            continue
        if lookup.status_code not in (200, 201):
            stats["errors"] = int(stats["errors"]) + 1
            logger.error(
                "Plugin recipe lookup failed",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "recipe_name": name,
                    "status_code": lookup.status_code,
                    "response": lookup.text,
                },
            )
            continue

        existing = lookup.json()
        recipe_id = existing.get("id")
        if recipe_id is None:
            stats["errors"] = int(stats["errors"]) + 1
            logger.error(
                "Plugin recipe lookup returned no id",
                extra={"req_id": SYSTEM_REQ_ID, "recipe_name": name},
            )
            continue
        if _recipe_contracts_match(payload, existing):
            stats["skipped"] = int(stats["skipped"]) + 1
            logger.debug(
                "Plugin recipe unchanged; skipping update",
                extra={"req_id": SYSTEM_REQ_ID, "recipe_name": name, "recipe_id": recipe_id},
            )
            continue
        response = request_control_plane_sync(
            "PATCH",
            f"{API_BASE_URL}/recipes/{recipe_id}",
            json=payload,
            req_id=req_id,
            timeout=60,
            retries=POLLER_RETRIES,
        )
        if response.status_code not in (200, 201):
            stats["errors"] = int(stats["errors"]) + 1
            logger.error(
                "Plugin recipe update failed",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "recipe_name": name,
                    "recipe_id": recipe_id,
                    "status_code": response.status_code,
                    "response": response.text,
                },
            )
            continue
        stats["updated"] = int(stats["updated"]) + 1

    return stats


def sync_plugin_communication_routes(*, req_id: str) -> JSONObject:
    stats: JSONObject = {"changed": False, "route_count": 0, "errors": 0}
    routes = get_enabled_plugin_communication_routes()
    if not routes:
        return stats
    stats["route_count"] = len(routes)
    response = request_control_plane_sync(
        "PUT",
        f"{API_BASE_URL}/communications/policy",
        json={"routes": routes},
        req_id=req_id,
        timeout=60,
        retries=POLLER_RETRIES,
    )
    if response.status_code not in (200, 201):
        stats["errors"] = int(stats["errors"]) + 1
        logger.error(
            "Plugin communication policy sync failed",
            extra={
                "req_id": SYSTEM_REQ_ID,
                "status_code": response.status_code,
                "response": response.text,
            },
        )
        return stats
    stats["changed"] = response.headers.get("X-PoundCake-Changed", "").lower() == "true"
    log = logger.info if stats["changed"] else logger.debug
    log(
        "Plugin communication policy sync complete",
        extra={
            "req_id": SYSTEM_REQ_ID,
            "route_count": len(routes),
            "changed": stats["changed"],
        },
    )
    return stats


def sync_scheduled_tasks(*, req_id: str) -> JSONObject:
    stats: JSONObject = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
    lookup_response = request_control_plane_sync(
        "GET",
        f"{API_BASE_URL}/scheduled-tasks",
        req_id=req_id,
        timeout=30,
        retries=POLLER_RETRIES,
    )
    existing_by_key: dict[str, JSONObject] = {}
    if lookup_response.status_code in (200, 201):
        for row in lookup_response.json():
            if isinstance(row, dict) and row.get("task_key"):
                existing_by_key[str(row["task_key"])] = row
    else:
        stats["errors"] = int(stats["errors"]) + 1
        logger.error(
            "Scheduled task lookup failed",
            extra={
                "req_id": SYSTEM_REQ_ID,
                "status_code": lookup_response.status_code,
                "response": lookup_response.text,
            },
        )
        return stats

    for template in _scheduled_task_templates():
        task_key = str(template.get("task_key") or "")
        existing = existing_by_key.get(task_key)
        if existing is None:
            response = request_control_plane_sync(
                "POST",
                f"{API_BASE_URL}/scheduled-tasks",
                json=template,
                req_id=req_id,
                timeout=30,
                retries=POLLER_RETRIES,
            )
            if response.status_code not in (200, 201):
                stats["errors"] = int(stats["errors"]) + 1
                logger.error(
                    "Scheduled task create failed",
                    extra={
                        "req_id": SYSTEM_REQ_ID,
                        "task_key": task_key,
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                continue
            stats["created"] = int(stats["created"]) + 1
            continue
        stats["skipped"] = int(stats["skipped"]) + 1
    return stats


def _sync_has_changes(
    *,
    ingredient_stats: JSONObject,
    recipe_stats: JSONObject,
    scheduled_task_stats: JSONObject,
    communication_stats: JSONObject,
) -> bool:
    return (
        any(
            int(stats.get(key) or 0) > 0
            for stats in (ingredient_stats, recipe_stats, scheduled_task_stats)
            for key in ("created", "updated", "errors")
        )
        or bool(communication_stats.get("changed"))
        or int(communication_stats.get("errors") or 0) > 0
    )


def run_sync() -> bool:
    params = {"mark_bootstrap": "true"} if MARK_BOOTSTRAP else {}
    templates = get_enabled_plugin_ingredient_templates()
    try:
        start_time = time.time()
        resp = request_control_plane_sync(
            "POST",
            f"{API_BASE_URL}/internal/service-registry/ingredients/bulk",
            json=templates,
            params=params,
            req_id=SYSTEM_REQ_ID,
            timeout=60,
            retries=POLLER_RETRIES,
        )
        latency_ms = int((time.time() - start_time) * 1000)
        if resp.status_code not in (200, 201):
            logger.error(
                "Dishwasher sync failed",
                extra={
                    "req_id": SYSTEM_REQ_ID,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "response": resp.text,
                },
            )
            return False
        ingredient_stats: JSONObject = {
            "created": int(resp.headers.get("X-PoundCake-Created-Count", "0") or 0),
            "updated": 0,
            "errors": 0,
        }
        communication_stats = sync_plugin_communication_routes(req_id=SYSTEM_REQ_ID)
        ingredient_rows = resp.json()
        recipe_stats = _sync_plugin_recipes(
            ingredient_rows=ingredient_rows,
            req_id=SYSTEM_REQ_ID,
        )
        scheduled_task_stats = sync_scheduled_tasks(req_id=SYSTEM_REQ_ID)
        enabled_plugins = get_enabled_plugins()
        has_changes = _sync_has_changes(
            ingredient_stats=ingredient_stats,
            recipe_stats=recipe_stats,
            scheduled_task_stats=scheduled_task_stats,
            communication_stats=communication_stats,
        )
        log = logger.info if has_changes else logger.debug
        log(
            "Plugin manifest sync complete",
            extra={
                "req_id": SYSTEM_REQ_ID,
                "latency_ms": latency_ms,
                "plugin_count": len(enabled_plugins),
                "service_types": _enabled_service_types(),
                "registered_ingredient_count": len(ingredient_rows),
                "ingredient_created_count": ingredient_stats.get("created"),
                "recipe_created_count": recipe_stats.get("created"),
                "recipe_updated_count": recipe_stats.get("updated"),
                "recipe_skipped_count": recipe_stats.get("skipped"),
                "recipe_error_count": recipe_stats.get("errors"),
                "scheduled_task_created_count": scheduled_task_stats.get("created"),
                "scheduled_task_updated_count": scheduled_task_stats.get("updated"),
                "scheduled_task_skipped_count": scheduled_task_stats.get("skipped"),
                "scheduled_task_error_count": scheduled_task_stats.get("errors"),
                "communication_policy_changed": communication_stats.get("changed"),
                "communication_policy_error_count": communication_stats.get("errors"),
            },
        )
        return (
            int(recipe_stats.get("errors") or 0) == 0
            and int(scheduled_task_stats.get("errors") or 0) == 0
            and int(communication_stats.get("errors") or 0) == 0
        )
    except Exception as e:
        logger.error(
            "Dishwasher sync error",
            extra={"req_id": SYSTEM_REQ_ID, "error": str(e)},
        )
        return False


def _api_json(
    method: str,
    path: str,
    *,
    req_id: str,
    json_payload: JSONObject | None = None,
    timeout: int = 30,
) -> tuple[int, JSONObject | list[JSONObject] | None, str]:
    response = request_control_plane_sync(
        method,
        f"{API_BASE_URL}{path}",
        json=json_payload,
        req_id=req_id,
        timeout=timeout,
        retries=POLLER_RETRIES,
    )
    parsed: JSONObject | list[JSONObject] | None
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    return response.status_code, parsed, response.text


def _slug_service_type(value: str | None, *, max_length: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return (slug or "core")[:max_length].strip("-") or "core"


def _scheduled_run_token() -> str:
    return f"{_now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"


def _load_plugin_req_id_keys() -> dict[str, str]:
    status_code, body, text = _api_json("GET", "/plugins", req_id=SYSTEM_REQ_ID, timeout=30)
    if status_code >= 400 or not isinstance(body, list):
        logger.warning(
            "Service plugin summary lookup failed; scheduled req ids will use fallbacks",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": status_code, "response": text},
        )
        return {}
    plugin_keys: dict[str, str] = {}
    for item in body:
        if not isinstance(item, dict):
            continue
        service_type = str(item.get("service_type") or "").strip().lower()
        plugin_short_id = str(item.get("plugin_short_id") or "").strip().lower()
        plugin_tier = str(item.get("plugin_tier") or "community").strip().lower()
        plugin_log_key = str(item.get("plugin_log_key") or "").strip().lower()
        if not service_type:
            continue
        if plugin_tier == "supported" and plugin_log_key:
            plugin_keys[service_type] = plugin_log_key[:32]
        elif plugin_short_id:
            plugin_keys[service_type] = f"{plugin_short_id[:12]}-{_slug_service_type(service_type)}"
    return plugin_keys


def _scheduled_task_req_id(
    task: JSONObject,
    plugin_req_id_keys: dict[str, str] | None = None,
) -> str:
    task_id = int(task["id"])
    raw_service_type = str(task.get("service_type") or "").strip().lower()
    service_type = _slug_service_type(raw_service_type or "core")
    plugin_key = (plugin_req_id_keys or {}).get(raw_service_type) if raw_service_type else "core"
    plugin_key = plugin_key or f"unknown-{service_type}"
    req_id = f"SYSTEM-SCHEDULED-{plugin_key}-{task_id}-{_scheduled_run_token()}"
    return req_id[:100]


def _enqueue_scheduled_task_order(
    task: JSONObject,
    plugin_req_id_keys: dict[str, str] | None = None,
) -> bool:
    task_id = int(task["id"])
    task_key = str(task.get("task_key") or "").strip()
    req_id = _scheduled_task_req_id(task, plugin_req_id_keys)
    now = _now()
    payload: JSONObject = {
        "req_id": req_id,
        "fingerprint": req_id,
        "alert_status": "firing",
        "processing_status": "new",
        "alert_group_name": task_key,
        "labels": {
            "order_type": SCHEDULED_TASK_ORDER_TYPE,
            "source": "dishwasher",
            "scheduled_task_id": str(task_id),
            "scheduled_task_key": task_key,
            "task_type": str(task.get("task_type") or ""),
            "service_type": str(task.get("service_type") or ""),
            "service_exec": str(task.get("service_exec") or ""),
        },
        "annotations": {
            "summary": f"Scheduled task {task_key}",
        },
        "raw_data": {
            "order_type": SCHEDULED_TASK_ORDER_TYPE,
            "source": "dishwasher",
            "scheduled_task_id": task_id,
            "scheduled_task_key": task_key,
            "task_type": task.get("task_type"),
            "service_type": task.get("service_type"),
            "service_exec": task.get("service_exec"),
            "task_payload": task.get("task_payload") or {},
            "task_parameters": task.get("task_parameters") or {},
            "expected_outcome": task.get("expected_outcome"),
        },
        "starts_at": now.isoformat(),
        "is_active": True,
        "counter": 1,
        "remediation_outcome": "pending",
        "auto_close_eligible": False,
    }
    status_code, body, text = _api_json(
        "POST",
        "/orders",
        req_id=req_id,
        json_payload=payload,
        timeout=30,
    )
    if status_code >= 400 or not isinstance(body, dict):
        logger.error(
            "Scheduled task order enqueue failed",
            extra={
                "req_id": req_id,
                "scheduled_task_id": task_id,
                "task_key": task_key,
                "status_code": status_code,
                "response": text,
            },
        )
        return False
    logger.info(
        "Scheduled task order enqueued",
        extra={
            "req_id": req_id,
            "scheduled_task_id": task_id,
            "task_key": task_key,
            "order_id": body.get("id"),
        },
    )
    return True


def run_due_scheduled_tasks() -> int:
    status_code, body, text = _api_json(
        "GET",
        f"/scheduled-tasks/due?limit={SCHEDULED_TASK_LIMIT}",
        req_id=SYSTEM_REQ_ID,
        timeout=30,
    )
    if status_code >= 400 or not isinstance(body, list):
        logger.warning(
            "Scheduled task due query failed",
            extra={"req_id": SYSTEM_REQ_ID, "status_code": status_code, "response": text},
        )
        return 0
    plugin_req_id_keys = _load_plugin_req_id_keys()
    for row in body:
        if isinstance(row, dict):
            _enqueue_scheduled_task_order(row, plugin_req_id_keys)
    return len(body)


def main() -> None:
    wait_for_api(
        API_BASE_URL, SYSTEM_REQ_ID, logger, require_healthy=False, max_attempts=120, delay_sec=2.0
    )

    if DISHWASHER_INTERVAL <= 0:
        attempts = int(os.getenv("DISHWASHER_BOOTSTRAP_ATTEMPTS", "10"))
        retry_delay = float(os.getenv("DISHWASHER_BOOTSTRAP_RETRY_DELAY", "5"))
        for attempt in range(1, attempts + 1):
            if run_sync():
                return
            if attempt < attempts:
                logger.warning(
                    "Dishwasher bootstrap sync failed; retrying",
                    extra={"req_id": SYSTEM_REQ_ID, "attempt": attempt, "max_attempts": attempts},
                )
                time.sleep(retry_delay)
        logger.error(
            "Dishwasher bootstrap sync failed after max attempts",
            extra={"req_id": SYSTEM_REQ_ID, "max_attempts": attempts},
        )
        return

    logger.info(
        "Dishwasher scheduled task crawler starting",
        extra={"req_id": SYSTEM_REQ_ID, "interval_sec": DISHWASHER_INTERVAL},
    )
    while True:
        runtime_config = get_worker_runtime_config(
            api_base_url=API_BASE_URL,
            service_type="dishwasher",
            req_id=SYSTEM_REQ_ID,
            default_interval=DISHWASHER_INTERVAL,
            logger=logger,
        )
        loop_interval = int(runtime_config["run_interval_seconds"])
        if not runtime_config["enabled"]:
            logger.info(
                "Dishwasher paused by internal plugin configuration",
                extra={"req_id": SYSTEM_REQ_ID, "interval_sec": loop_interval},
            )
            time.sleep(loop_interval)
            continue
        run_sync()
        run_due_scheduled_tasks()
        time.sleep(loop_interval)


if __name__ == "__main__":
    main()

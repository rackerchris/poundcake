#!/usr/bin/env bash
# Configure PoundCake's Bakery adapter for remote devstack usage.

set -euo pipefail

# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/load-local-secrets.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"

BAKERY_URL="${BAKERY_URL:-}"
BAKERY_MONITOR_ID="${BAKERY_MONITOR_ID:-}"
BAKERY_MONITOR_UUID="${BAKERY_MONITOR_UUID:-}"
BAKERY_MONITOR_HMAC_KEY_ID="${BAKERY_MONITOR_HMAC_KEY_ID:-}"
BAKERY_MONITOR_HMAC_SECRET="${BAKERY_MONITOR_HMAC_SECRET:-}"
BAKERY_VERIFY_SSL="${BAKERY_VERIFY_SSL:-true}"
BAKERY_TIMEOUT_SECONDS="${BAKERY_TIMEOUT_SECONDS:-15}"
BAKERY_MAX_RETRIES="${BAKERY_MAX_RETRIES:-2}"
BAKERY_POLL_INTERVAL_SECONDS="${BAKERY_POLL_INTERVAL_SECONDS:-2.0}"
BAKERY_POLL_TIMEOUT_SECONDS="${BAKERY_POLL_TIMEOUT_SECONDS:-60}"
BAKERY_PLUGIN_ID="${BAKERY_PLUGIN_ID:-rackspace/kronos-poundcake}"
BAKERY_ENVIRONMENT_LABEL="${BAKERY_ENVIRONMENT_LABEL:-devstack}"
BAKERY_REGION="${BAKERY_REGION:-ord}"
BAKERY_CLUSTER_NAME="${BAKERY_CLUSTER_NAME:-kind-poundcake}"
BAKERY_PLUGIN_NAMESPACE="${BAKERY_PLUGIN_NAMESPACE:-$POUNDCAKE_NAMESPACE}"
BAKERY_RELEASE_NAME="${BAKERY_RELEASE_NAME:-poundcake}"
BAKERY_TAGS="${BAKERY_TAGS:-devstack,kind}"

log() {
    printf '[helm-devstack-bakery-adapter] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-bakery-adapter] ERROR: %s\n' "$*" >&2
    exit 1
}

detect_executable() {
    local env_var="$1"
    local command_name="$2"
    shift 2
    local configured="${!env_var:-}"
    local candidate

    if [ -n "$configured" ]; then
        [ -x "$configured" ] || fail "$env_var is set but not executable: $configured"
        printf '%s\n' "$configured"
        return 0
    fi

    if command -v "$command_name" >/dev/null 2>&1; then
        command -v "$command_name"
        return 0
    fi

    for candidate in "$@"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    fail "$command_name is not installed or not in PATH"
}

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

[ -n "$BAKERY_URL" ] || fail "BAKERY_URL is required"
[ -n "$BAKERY_MONITOR_ID" ] || fail "BAKERY_MONITOR_ID is required"
[ -n "$BAKERY_MONITOR_UUID" ] || fail "BAKERY_MONITOR_UUID is required"
[ -n "$BAKERY_MONITOR_HMAC_KEY_ID" ] || fail "BAKERY_MONITOR_HMAC_KEY_ID is required"
[ -n "$BAKERY_MONITOR_HMAC_SECRET" ] || fail "BAKERY_MONITOR_HMAC_SECRET is required"

log "waiting for PoundCake API"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout="$WAIT_TIMEOUT"

log "writing Bakery adapter configuration and monitor credential through PoundCake runtime"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" exec -i deploy/poundcake-api -- \
    env \
        BAKERY_URL="$BAKERY_URL" \
        BAKERY_MONITOR_ID="$BAKERY_MONITOR_ID" \
        BAKERY_MONITOR_UUID="$BAKERY_MONITOR_UUID" \
        BAKERY_MONITOR_HMAC_KEY_ID="$BAKERY_MONITOR_HMAC_KEY_ID" \
        BAKERY_MONITOR_HMAC_SECRET="$BAKERY_MONITOR_HMAC_SECRET" \
        BAKERY_VERIFY_SSL="$BAKERY_VERIFY_SSL" \
        BAKERY_TIMEOUT_SECONDS="$BAKERY_TIMEOUT_SECONDS" \
        BAKERY_MAX_RETRIES="$BAKERY_MAX_RETRIES" \
        BAKERY_POLL_INTERVAL_SECONDS="$BAKERY_POLL_INTERVAL_SECONDS" \
        BAKERY_POLL_TIMEOUT_SECONDS="$BAKERY_POLL_TIMEOUT_SECONDS" \
        BAKERY_PLUGIN_ID="$BAKERY_PLUGIN_ID" \
        BAKERY_ENVIRONMENT_LABEL="$BAKERY_ENVIRONMENT_LABEL" \
        BAKERY_REGION="$BAKERY_REGION" \
        BAKERY_CLUSTER_NAME="$BAKERY_CLUSTER_NAME" \
        BAKERY_PLUGIN_NAMESPACE="$BAKERY_PLUGIN_NAMESPACE" \
        BAKERY_RELEASE_NAME="$BAKERY_RELEASE_NAME" \
        BAKERY_TAGS="$BAKERY_TAGS" \
        python3 - <<'PY'
import asyncio
import os

from api.core.time import utc_now_db
from api.plugins.bakery.adapter import BakeryExecutionAdapter
from api.plugins.state import (
    PLUGIN_RUN_STATE_FAILED,
    PLUGIN_RUN_STATE_HEALTHY,
    PLUGIN_RUN_STATE_INITIALIZING,
)
from api.services.adapter_runtime import dispose_adapter_runtime_resources
from api.services.credential_manager import write_adapter_credential
from api.services.plugin_operations import (
    disable_service_plugin_and_tasks,
    update_service_plugin_state,
)


def _bool(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


async def main() -> None:
    url = os.environ["BAKERY_URL"].strip().rstrip("/")
    monitor_id = os.environ["BAKERY_MONITOR_ID"].strip()
    monitor_uuid = os.environ["BAKERY_MONITOR_UUID"].strip()
    key_id = os.environ["BAKERY_MONITOR_HMAC_KEY_ID"].strip()
    secret = os.environ["BAKERY_MONITOR_HMAC_SECRET"].strip()
    if not url:
        raise SystemExit("BAKERY_URL is empty")
    if not monitor_id:
        raise SystemExit("BAKERY_MONITOR_ID is empty")
    if not monitor_uuid:
        raise SystemExit("BAKERY_MONITOR_UUID is empty")
    if not key_id:
        raise SystemExit("BAKERY_MONITOR_HMAC_KEY_ID is empty")
    if not secret:
        raise SystemExit("BAKERY_MONITOR_HMAC_SECRET is empty")

    plugin_config = {
        "url": url,
        "verify_ssl": _bool(os.environ["BAKERY_VERIFY_SSL"]),
        "timeout_seconds": int(os.environ["BAKERY_TIMEOUT_SECONDS"]),
        "max_retries": int(os.environ["BAKERY_MAX_RETRIES"]),
        "poll_interval_seconds": float(os.environ["BAKERY_POLL_INTERVAL_SECONDS"]),
        "poll_timeout_seconds": int(os.environ["BAKERY_POLL_TIMEOUT_SECONDS"]),
        "plugin_id": os.environ["BAKERY_PLUGIN_ID"].strip(),
        "environment_label": os.environ["BAKERY_ENVIRONMENT_LABEL"].strip(),
        "region": os.environ["BAKERY_REGION"].strip(),
        "cluster_name": os.environ["BAKERY_CLUSTER_NAME"].strip(),
        "namespace": os.environ["BAKERY_PLUGIN_NAMESPACE"].strip(),
        "release_name": os.environ["BAKERY_RELEASE_NAME"].strip(),
        "tags": os.environ["BAKERY_TAGS"].strip(),
    }

    async def disable_dummy_plugin() -> None:
        await disable_service_plugin_and_tasks(
            requester_service_type="api",
            service_type="dummy",
            health_status="disabled",
            status_message=(
                "Disabled automatically when Bakery devstack adapter is configured"
            ),
            task_status="disabled",
        )

    try:
        await write_adapter_credential(
            service_type="bakery",
            credential_type="bakery_monitor_hmac",
            credential_key_id="default",
            payload={
                "monitor_id": monitor_id,
                "monitor_uuid": monitor_uuid,
                "hmac_key_id": key_id,
                "hmac_secret": secret,
            },
        )

        adapter = BakeryExecutionAdapter().with_operator_config(plugin_config)
        health = await adapter.test_connection(credential_key_id="default")
        status = str(health.status or "").strip().lower()
        healthy = status == "healthy"
        initializing = status == "initializing"
        now = utc_now_db()
        updated = await update_service_plugin_state(
            requester_service_type="api",
            service_type="bakery",
            plugin_config=plugin_config,
            health_status=(
                PLUGIN_RUN_STATE_HEALTHY
                if healthy
                else PLUGIN_RUN_STATE_INITIALIZING
                if initializing
                else PLUGIN_RUN_STATE_FAILED
            ),
            health_message=health.message,
            health_error_code=health.error_code,
            health_latency_ms=health.latency_ms,
            last_health_check_at=now,
        )
        if not updated:
            raise SystemExit("bakery service plugin is not registered")
        await disable_dummy_plugin()
        if not healthy and not initializing:
            raise SystemExit(
                f"Bakery test_connection failed after configuration: {health.message}"
            )
        print(f"configured bakery adapter url={url} status={status}")
    finally:
        await dispose_adapter_runtime_resources()


asyncio.run(main())
PY

log "Bakery adapter configured"

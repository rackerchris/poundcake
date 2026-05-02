#!/usr/bin/env bash
# Configure PoundCake's StackStorm adapter for the Helm devstack topology.

set -euo pipefail

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-${NAMESPACE:-poundcake}}"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
STACKSTORM_API_SERVICE="${STACKSTORM_API_SERVICE:-stackstorm-api}"
STACKSTORM_API_PORT="${STACKSTORM_API_PORT:-9101}"
STACKSTORM_API_KEY_SECRET="${STACKSTORM_API_KEY_SECRET:-stackstorm-apikeys}"
STACKSTORM_API_KEY_FIELD="${STACKSTORM_API_KEY_FIELD:-st2_api_key}"
STACKSTORM_URL="${STACKSTORM_URL:-http://${STACKSTORM_API_SERVICE}.${STACKSTORM_NAMESPACE}.svc.cluster.local:${STACKSTORM_API_PORT}}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"

log() {
    printf '[helm-devstack-stackstorm-adapter] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-stackstorm-adapter] ERROR: %s\n' "$*" >&2
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

decode_b64() {
    if base64 --help 2>&1 | grep -q -- '--decode'; then
        base64 --decode
    else
        base64 -D
    fi
}

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

log "waiting for PoundCake API and StackStorm API"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" wait --for=condition=Available deployment/stackstorm-api --timeout="$WAIT_TIMEOUT"

log "waiting for StackStorm API key secret ${STACKSTORM_API_KEY_SECRET}/${STACKSTORM_API_KEY_FIELD}"
deadline=$((SECONDS + 600))
api_key=""
while [ "$SECONDS" -lt "$deadline" ]; do
    api_key="$(
        "$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" get secret "$STACKSTORM_API_KEY_SECRET" \
            -o "jsonpath={.data.${STACKSTORM_API_KEY_FIELD}}" 2>/dev/null | decode_b64
    )"
    if [ -n "$api_key" ]; then
        break
    fi
    sleep 5
done
[ -n "$api_key" ] || fail "$STACKSTORM_API_KEY_SECRET/$STACKSTORM_API_KEY_FIELD is empty after waiting"

log "writing StackStorm adapter URL and API key through PoundCake runtime"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" exec -i deploy/poundcake-api -- \
    env STACKSTORM_API_KEY="$api_key" STACKSTORM_URL="$STACKSTORM_URL" python3 - <<'PY'
import asyncio
import os

from api.core.time import utc_now_db
from api.plugins.state import PLUGIN_RUN_STATE_FAILED, PLUGIN_RUN_STATE_HEALTHY
from api.plugins.stackstorm.service import StackStormClient
from api.services.adapter_runtime import dispose_adapter_runtime_resources
from api.services.credential_manager import write_adapter_credential
from api.services.plugin_operations import update_service_plugin_state


async def main() -> None:
    key = os.environ["STACKSTORM_API_KEY"].strip()
    url = os.environ["STACKSTORM_URL"].strip().rstrip("/")
    if not key:
        raise SystemExit("STACKSTORM_API_KEY is empty")
    if not url:
        raise SystemExit("STACKSTORM_URL is empty")

    try:
        await write_adapter_credential(
            service_type="stackstorm",
            credential_type="stackstorm_api_key",
            credential_key_id="default",
            payload={"api_key": key, "st2_api_key": key},
        )
        client = StackStormClient(base_url=url, verify_ssl=False)
        healthy = await client.health_check(req_id="devstack-stackstorm-configure")
        now = utc_now_db()
        updated = await update_service_plugin_state(
            requester_service_type="api",
            service_type="stackstorm",
            plugin_config={"url": url, "verify_ssl": False},
            health_status=PLUGIN_RUN_STATE_HEALTHY if healthy else PLUGIN_RUN_STATE_FAILED,
            health_message=(
                "StackStorm API accepted the configured credential"
                if healthy
                else "StackStorm health check failed after adapter configuration"
            ),
            health_error_code=None if healthy else "stackstorm_health_check_failed",
            health_latency_ms=None,
            last_health_check_at=now,
        )
        if not updated:
            raise SystemExit("stackstorm service plugin is not registered")
        if not healthy:
            raise SystemExit("StackStorm health check failed after adapter configuration")
        print(f"configured stackstorm adapter url={url}")
    finally:
        await dispose_adapter_runtime_resources()


asyncio.run(main())
PY

log "StackStorm adapter configured"

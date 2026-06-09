#!/usr/bin/env bash
# StackStorm action_execution e2e runner for a running helm/devstack cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
source "${SCRIPT_DIR}/lib.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
STACKSTORM_API_LOCAL_PORT="${STACKSTORM_API_LOCAL_PORT:-19101}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-stackstorm-action-e2e}"
CONFIGURE_STACKSTORM_ADAPTER="${CONFIGURE_STACKSTORM_ADAPTER:-true}"

log() {
    printf '[stackstorm-action-e2e] %s\n' "$*"
}

fail() {
    printf '[stackstorm-action-e2e] ERROR: %s\n' "$*" >&2
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
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
        PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
    else
        PYTHON_BIN="$(detect_executable PYTHON_BIN python3 /opt/homebrew/bin/python3 /usr/local/bin/python3)"
    fi
fi

decode_b64() {
    if base64 --help 2>&1 | grep -q -- '--decode'; then
        base64 --decode
    else
        base64 -D
    fi
}

secret_value() {
    local namespace="$1"
    local secret_name="$2"
    local key="$3"
    "$KUBECTL_BIN" -n "$namespace" get secret "$secret_name" \
        -o "jsonpath={.data.${key}}" 2>/dev/null | decode_b64
}

wait_for_port() {
    local host="$1"
    local port="$2"
    local name="$3"
    local deadline=$((SECONDS + 60))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if "$PYTHON_BIN" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.create_connection((host, port), timeout=1):
    pass
PY
        then
            return 0
        fi
        sleep 1
    done
    fail "port-forward did not become ready: ${name} (${host}:${port})"
}

wait_for_stackstorm_core_echo() {
    local expected_message="$1"
    local start now body code response
    start=$(date +%s)
    while true; do
        response="$(
            curl -sS \
                -H "St2-Api-Key: ${STACKSTORM_API_KEY}" \
                "http://127.0.0.1:${STACKSTORM_API_LOCAL_PORT}/v1/executions?limit=25" \
                -w $'\n%{http_code}'
        )"
        code="${response##*$'\n'}"
        body="${response%$'\n'*}"
        if [ "$code" = "200" ] && echo "$body" | jq -e --arg message "$expected_message" '
          [.[] | select(
            (
              .action.ref? == "core.echo"
              or .action? == "core.echo"
              or (.action.name? == "echo" and .action.pack? == "core")
            )
            and .status == "succeeded"
            and ((.parameters? | tostring) | contains($message))
            and ((.result? | tostring) | contains($message))
          )] | length >= 1
        ' >/dev/null; then
            echo "$body"
            return 0
        fi
        now=$(date +%s)
        if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
            log_error "Timed out waiting for StackStorm core.echo execution with expected message"
            if echo "$body" | jq -e . >/dev/null 2>&1; then
                echo "$body" | jq . >&2
            else
                echo "$body" >&2
            fi
            exit 1
        fi
        sleep "${POLL_INTERVAL_SEC}"
    done
}

start_port_forward() {
    local name="$1"
    local namespace="$2"
    local resource="$3"
    local mapping="$4"
    local log_file="$STATE_DIR/${name}.log"
    local pid_file="$STATE_DIR/${name}.pid"

    log "starting port-forward ${namespace}/${resource} ${mapping}"
    nohup "$KUBECTL_BIN" -n "$namespace" port-forward "$resource" "$mapping" >"$log_file" 2>&1 &
    printf '%s\n' "$!" > "$pid_file"
}

cleanup() {
    local pid_file pid
    for pid_file in "$STATE_DIR"/*.pid; do
        [ -f "$pid_file" ] || continue
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -n "$pid" ]; then
            kill "$pid" >/dev/null 2>&1 || true
            wait "$pid" >/dev/null 2>&1 || true
        fi
        rm -f "$pid_file"
    done
}
trap cleanup EXIT

mkdir -p "$STATE_DIR"
rm -f "$STATE_DIR"/*.pid "$STATE_DIR"/*.log

require_cmd curl
require_cmd jq

log "waiting for StackStorm and PoundCake deployments"
"$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" wait --for=condition=Available deployment/stackstorm-api --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" wait --for=condition=Available deployment/stackstorm-auth --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-dishwasher --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-prep-chef --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-timer --timeout="$WAIT_TIMEOUT"

if [ "$CONFIGURE_STACKSTORM_ADAPTER" = "true" ]; then
    log "configuring PoundCake StackStorm adapter credentials and service URL"
    POUNDCAKE_NAMESPACE="$POUNDCAKE_NAMESPACE" STACKSTORM_NAMESPACE="$STACKSTORM_NAMESPACE" \
        "$PROJECT_ROOT/helm/devstack/configure-stackstorm-adapter.sh"
fi

export API_ROOT_URL="http://127.0.0.1:${API_LOCAL_PORT}"
export API_URL="${API_ROOT_URL}/api/v1"
start_port_forward api "$POUNDCAKE_NAMESPACE" svc/poundcake-api "${API_LOCAL_PORT}:8000"
start_port_forward stackstorm-api "$STACKSTORM_NAMESPACE" svc/stackstorm-api "${STACKSTORM_API_LOCAL_PORT}:9101"
wait_for_port 127.0.0.1 "$API_LOCAL_PORT" poundcake-api
wait_for_port 127.0.0.1 "$STACKSTORM_API_LOCAL_PORT" stackstorm-api

log "waiting for PoundCake API readiness and StackStorm plugin health"
wait_for_api_ready
if [ -z "$AUTH_PASSWORD_WAS_SET" ]; then
    AUTH_PASSWORD="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-admin" "password")"
    export AUTH_PASSWORD
fi
if [ -z "$WEBHOOK_BEARER_TOKEN_WAS_SET" ]; then
    WEBHOOK_BEARER_TOKEN="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-secrets" "WEBHOOK_BEARER_TOKEN")"
    export WEBHOOK_BEARER_TOKEN
fi
STACKSTORM_API_KEY="$(secret_value "$STACKSTORM_NAMESPACE" "stackstorm-apikeys" "st2_api_key")"
[ -n "$STACKSTORM_API_KEY" ] || fail "stackstorm-apikeys/st2_api_key is empty"
authenticate_api_if_required
wait_for_plugin_health "stackstorm" "healthy" >/dev/null

registry="$(api_request_json GET "/service-registry/ingredients")"
ingredient_id="$(
    echo "$registry" | jq -r '
      .[]
      | select(
          .service_type == "stackstorm"
          and .service_exec == "action_execution"
          and .task_key_template == "stackstorm-action-execution"
        )
      | .id
    ' | head -n 1
)"
[ -n "$ingredient_id" ] && [ "$ingredient_id" != "null" ] || fail "stackstorm-action-execution ingredient is not registered"

suffix="$(generate_test_suffix)"
recipe_name="e2e-stackstorm-core-echo-${suffix}"
message="poundcake stackstorm core.echo e2e ${suffix}"

log "creating temporary recipe ${recipe_name} with stackstorm-action-execution"
recipe_payload="$(
    jq -n \
      --arg name "$recipe_name" \
      --arg message "$message" \
      --argjson ingredient_id "$ingredient_id" \
      '{
        name: $name,
        description: "E2E StackStorm core.echo action_execution",
        enabled: true,
        recipe_ingredients: [
          {
            ingredient_id: $ingredient_id,
            step_order: 1,
            on_success: "continue",
            parallel_group: 0,
            depth: 0,
            service_payload: {
              action_ref: "core.echo",
              parameters: {message: $message}
            },
            service_exec_expected_secs: 5,
            service_exec_timeout: 120,
            service_exec_expected_outcome: {status: "succeeded"},
            run_phase: "firing",
            run_condition: "always"
          }
        ]
      }'
)"
recipe="$(api_request_json POST "/recipes/" "$recipe_payload")"
assert_json "$recipe" \
    '.recipe_ingredients[0].ingredient.task_key_template == "stackstorm-action-execution"' \
    "created recipe did not use stackstorm-action-execution"

req_id="E2E-STACKSTORM-CORE-ECHO-${suffix}"
fingerprint="stackstorm-core-echo-${suffix}"
log "posting webhook for ${recipe_name}"
order_id="$(post_alert "$recipe_name" "firing" "$req_id" "$fingerprint" "stackstorm-action-e2e.local" "critical")"
order="$(wait_for_order_status "$order_id" "complete")"
assert_json "$order" '.processing_status == "complete"' "StackStorm action order did not complete"

dishes="$(collect_order_dishes "$order_id")"
dish_id="$(echo "$dishes" | jq -r '.[0].id // empty')"
[ -n "$dish_id" ] || fail "order ${order_id} did not create a dish"

ingredients="$(api_request_json GET "/dishes/${dish_id}/ingredient-status")"
assert_json "$ingredients" \
    '[.[] | select(
      .service_type == "stackstorm"
      and .service_exec == "action_execution"
      and (.task_key | endswith("stackstorm-action-execution"))
      and .service_exec_status == "succeeded"
    )] | length == 1' \
    "StackStorm core.echo ingredient did not succeed"
executions="$(wait_for_stackstorm_core_echo "$message")"
execution_id="$(
    echo "$executions" | jq -r --arg message "$message" '
      .[]
      | select(
          (
            .action.ref? == "core.echo"
            or .action? == "core.echo"
            or (.action.name? == "echo" and .action.pack? == "core")
          )
          and .status == "succeeded"
          and ((.parameters? | tostring) | contains($message))
          and ((.result? | tostring) | contains($message))
        )
      | .id
    ' | head -n 1
)"

log "PASS stackstorm core.echo action_execution order_id=${order_id} dish_id=${dish_id} execution_id=${execution_id}"

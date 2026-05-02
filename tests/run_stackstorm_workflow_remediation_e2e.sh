#!/usr/bin/env bash
# StackStorm workflow_execution remediation e2e runner for a running helm/devstack cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
source "${SCRIPT_DIR}/lib.sh"

NAMESPACE="${NAMESPACE:-poundcake}"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
ALERTMANAGER_SERVICE="${ALERTMANAGER_SERVICE:-kube-prometheus-stack-alertmanager}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-300}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
STACKSTORM_API_LOCAL_PORT="${STACKSTORM_API_LOCAL_PORT:-19101}"
ALERTMANAGER_LOCAL_PORT="${ALERTMANAGER_LOCAL_PORT:-19093}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-stackstorm-workflow-remediation-e2e}"
CONFIGURE_STACKSTORM_ADAPTER="${CONFIGURE_STACKSTORM_ADAPTER:-true}"
WORKFLOW_REF="${WORKFLOW_REF:-poundcake.host_down_remediation}"
recipe_ids=()

log() {
    printf '[stackstorm-workflow-remediation-e2e] %s\n' "$*"
}

fail() {
    printf '[stackstorm-workflow-remediation-e2e] ERROR: %s\n' "$*" >&2
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

cleanup_recipe_best_effort() {
    local id="$1"
    local cake_ctl="${CAKECTL:-cakectl}"
    ${cake_ctl} -u "${API_URL%/api/v1}" recipes delete "${id}" --yes >/dev/null 2>&1 || true
}

cleanup() {
    local pid_file pid
    for recipe_id in "${recipe_ids[@]:-}"; do
        [ -n "$recipe_id" ] && cleanup_recipe_best_effort "$recipe_id"
    done
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

iso_now() {
    "$PYTHON_BIN" - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
}

iso_future() {
    "$PYTHON_BIN" - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
}

workflow_action_name() {
    printf '%s\n' "${WORKFLOW_REF#poundcake.}"
}

seed_alertmanager_alert() {
    local recipe="$1"
    local starts_at ends_at payload response body code start now fingerprint alerts
    starts_at="$(iso_now)"
    ends_at="$(iso_future)"
    payload="$(
        jq -n \
          --arg recipe "$recipe" \
          --arg starts_at "$starts_at" \
          --arg ends_at "$ends_at" \
          '[
            {
              labels: {
                alertname: $recipe,
                group_name: $recipe,
                severity: "critical",
                namespace: "kube-system",
                daemonset: "e2e-daemonset",
                node: "e2e-node",
                instance: "e2e-node.kube-system"
              },
              annotations: {
                summary: "StackStorm workflow remediation e2e",
                description: "Seeded directly into Alertmanager so PoundCake guards see this alert firing."
              },
              startsAt: $starts_at,
              endsAt: $ends_at,
              generatorURL: "http://prometheus:9090/graph"
            }
          ]'
    )"
    response="$(
        curl -sS -X POST \
            -H "Content-Type: application/json" \
            --data "$payload" \
            "http://127.0.0.1:${ALERTMANAGER_LOCAL_PORT}/api/v2/alerts" \
            -w $'\n%{http_code}'
    )"
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [ "$code" -lt 200 ] || [ "$code" -ge 300 ]; then
        log_error "Alertmanager seed failed with HTTP ${code}"
        echo "$body" >&2
        exit 1
    fi

    start="$(date +%s)"
    while true; do
        alerts="$(
            curl -sS --get \
                --data-urlencode 'active=true' \
                --data-urlencode "filter=alertname=\"${recipe}\"" \
                "http://127.0.0.1:${ALERTMANAGER_LOCAL_PORT}/api/v2/alerts"
        )"
        fingerprint="$(
            echo "$alerts" | jq -r --arg recipe "$recipe" '
              [
                .[]
                | select(.labels.alertname == $recipe and .labels.group_name == $recipe)
                | .fingerprint
              ][0] // empty
            '
        )"
        if [ -n "$fingerprint" ]; then
            printf '%s\n' "$fingerprint"
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
            log_error "Timed out waiting for seeded Alertmanager alert fingerprint"
            echo "$alerts" | jq . >&2 || echo "$alerts" >&2
            exit 1
        fi
        sleep "$POLL_INTERVAL_SEC"
    done
}

post_remediation_alert() {
    local recipe="$1"
    local req_id="$2"
    local fingerprint="$3"
    local now payload response order_id
    now="$(iso_now)"
    payload="$(
        jq -n \
          --arg recipe "$recipe" \
          --arg fingerprint "$fingerprint" \
          --arg now "$now" \
          '{
            receiver: "poundcake",
            status: "firing",
            alerts: [
              {
                status: "firing",
                fingerprint: $fingerprint,
                labels: {
                  alertname: $recipe,
                  group_name: $recipe,
                  severity: "critical",
                  namespace: "kube-system",
                  daemonset: "e2e-daemonset",
                  node: "e2e-node",
                  instance: "e2e-node.kube-system"
                },
                annotations: {
                  summary: "StackStorm workflow remediation e2e",
                  description: "Generated by tests/run_stackstorm_workflow_remediation_e2e.sh"
                },
                startsAt: $now,
                endsAt: null,
                generatorURL: "http://prometheus:9090/graph"
              }
            ],
            groupLabels: {alertname: $recipe, group_name: $recipe},
            commonLabels: {alertname: $recipe, group_name: $recipe, namespace: "kube-system"},
            commonAnnotations: {},
            externalURL: "http://alertmanager:9093",
            version: "4",
            groupKey: ("{}:{alertname=\"" + $recipe + "\"}")
          }'
    )"
    response="$(REQUEST_ID="$req_id" api_request_json POST "/webhook" "$payload")"
    order_id="$(echo "$response" | jq -r '.order_id // .results[0].order_id // .id // empty')"
    [ -n "$order_id" ] && [ "$order_id" != "null" ] || fail "webhook response did not include an order id"
    echo "$order_id"
}

ingredient_id_for() {
    local registry="$1"
    local service_type="$2"
    local service_exec="$3"
    local task_key_template="$4"
    local id
    id="$(
        echo "$registry" | jq -r \
          --arg service_type "$service_type" \
          --arg service_exec "$service_exec" \
          --arg task_key_template "$task_key_template" \
          '.[] | select(
            .service_type == $service_type
            and .service_exec == $service_exec
            and .task_key_template == $task_key_template
          ) | .id' | head -n 1
    )"
    [ -n "$id" ] && [ "$id" != "null" ] || fail "ingredient not registered: ${service_type}/${service_exec}/${task_key_template}"
    printf '%s\n' "$id"
}

wait_for_stackstorm_workflow_execution() {
    local expected_action="$1"
    local start now response code body
    start="$(date +%s)"
    while true; do
        response="$(
            curl -sS \
                -H "St2-Api-Key: ${STACKSTORM_API_KEY}" \
                "http://127.0.0.1:${STACKSTORM_API_LOCAL_PORT}/v1/executions?limit=50" \
                -w $'\n%{http_code}'
        )"
        code="${response##*$'\n'}"
        body="${response%$'\n'*}"
        if [ "$code" = "200" ] && echo "$body" | jq -e --arg action "$expected_action" '
          [.[] | select(
            (
              .action.ref? == ("poundcake." + $action)
              or .action? == ("poundcake." + $action)
              or (.action.name? == $action and .action.pack? == "poundcake")
            )
            and .status == "succeeded"
          )] | length >= 1
        ' >/dev/null; then
            echo "$body"
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
            log_error "Timed out waiting for StackStorm workflow execution ${expected_action}"
            if echo "$body" | jq -e . >/dev/null 2>&1; then
                echo "$body" | jq . >&2
            else
                echo "$body" >&2
            fi
            exit 1
        fi
        sleep "$POLL_INTERVAL_SEC"
    done
}

count_stackstorm_workflow_executions() {
    local expected_action="$1"
    curl -sS \
        -H "St2-Api-Key: ${STACKSTORM_API_KEY}" \
        "http://127.0.0.1:${STACKSTORM_API_LOCAL_PORT}/v1/executions?limit=100" \
      | jq -r --arg action "$expected_action" '
          [.[] | select(
            .action.ref? == ("poundcake." + $action)
            or .action? == ("poundcake." + $action)
            or (.action.name? == $action and .action.pack? == "poundcake")
          )] | length
        '
}

mkdir -p "$STATE_DIR"
rm -f "$STATE_DIR"/*.pid "$STATE_DIR"/*.log

require_cmd curl
require_cmd jq

log "waiting for StackStorm, Alertmanager, and PoundCake deployments"
"$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" wait --for=condition=Available deployment/stackstorm-api --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$STACKSTORM_NAMESPACE" wait --for=condition=Available deployment/stackstorm-auth --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$MONITORING_NAMESPACE" rollout status "statefulset/alertmanager-${ALERTMANAGER_SERVICE}" --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$NAMESPACE" wait --for=condition=Available deployment/poundcake-dishwasher --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$NAMESPACE" wait --for=condition=Available deployment/poundcake-prep-chef --timeout="$WAIT_TIMEOUT"
"$KUBECTL_BIN" -n "$NAMESPACE" wait --for=condition=Available deployment/poundcake-timer --timeout="$WAIT_TIMEOUT"

if [ "$CONFIGURE_STACKSTORM_ADAPTER" = "true" ]; then
    log "configuring PoundCake StackStorm adapter credentials and service URL"
    NAMESPACE="$NAMESPACE" STACKSTORM_NAMESPACE="$STACKSTORM_NAMESPACE" \
        "$PROJECT_ROOT/helm/devstack/configure-stackstorm-adapter.sh"
fi

export API_ROOT_URL="http://127.0.0.1:${API_LOCAL_PORT}"
export API_URL="${API_ROOT_URL}/api/v1"
start_port_forward api "$NAMESPACE" svc/poundcake-api "${API_LOCAL_PORT}:8000"
start_port_forward stackstorm-api "$STACKSTORM_NAMESPACE" svc/stackstorm-api "${STACKSTORM_API_LOCAL_PORT}:9101"
start_port_forward alertmanager "$MONITORING_NAMESPACE" "svc/${ALERTMANAGER_SERVICE}" "${ALERTMANAGER_LOCAL_PORT}:9093"
wait_for_port 127.0.0.1 "$API_LOCAL_PORT" poundcake-api
wait_for_port 127.0.0.1 "$STACKSTORM_API_LOCAL_PORT" stackstorm-api
wait_for_port 127.0.0.1 "$ALERTMANAGER_LOCAL_PORT" alertmanager

log "waiting for PoundCake API readiness and plugin health"
wait_for_api_ready
if [ -z "$AUTH_PASSWORD_WAS_SET" ]; then
    AUTH_PASSWORD="$(secret_value "$NAMESPACE" "poundcake-admin" "password")"
    export AUTH_PASSWORD
fi
if [ -z "$WEBHOOK_BEARER_TOKEN_WAS_SET" ]; then
    WEBHOOK_BEARER_TOKEN="$(secret_value "$NAMESPACE" "poundcake-secrets" "WEBHOOK_BEARER_TOKEN")"
    export WEBHOOK_BEARER_TOKEN
fi
STACKSTORM_API_KEY="$(secret_value "$STACKSTORM_NAMESPACE" "stackstorm-apikeys" "st2_api_key")"
[ -n "$STACKSTORM_API_KEY" ] || fail "stackstorm-apikeys/st2_api_key is empty"
authenticate_api_if_required
wait_for_plugin_health "stackstorm" "healthy" >/dev/null
wait_for_plugin_health "alertmanager" "healthy" >/dev/null
wait_for_plugin_health "k8s" "healthy" >/dev/null

registry="$(api_request_json GET "/service-registry/ingredients")"
guard_ingredient_id="$(ingredient_id_for "$registry" "alertmanager" "inspect" "alertmanager-firing-guard")"
evidence_ingredient_id="$(ingredient_id_for "$registry" "k8s" "node_triage" "k8s-node-triage")"
workflow_ingredient_id="$(ingredient_id_for "$registry" "stackstorm" "workflow_execution" "stackstorm-workflow-execution")"

suffix="$(generate_test_suffix)"
recipe_name="e2e-stackstorm-workflow-remediation-${suffix}"
req_id="E2E-STACKSTORM-WORKFLOW-REMEDIATION-${suffix}"
workflow_action="$(workflow_action_name)"

log "seeding Alertmanager with firing alert for ${recipe_name}"
fingerprint="$(seed_alertmanager_alert "$recipe_name")"
log "Alertmanager assigned fingerprint=${fingerprint}"

log "creating temporary 4-step remediation recipe ${recipe_name}"
recipe_payload="$(
    jq -n \
      --arg name "$recipe_name" \
      --arg fingerprint "$fingerprint" \
      --arg workflow_ref "$WORKFLOW_REF" \
      --arg req_id "$req_id" \
      --argjson guard_ingredient_id "$guard_ingredient_id" \
      --argjson evidence_ingredient_id "$evidence_ingredient_id" \
      --argjson workflow_ingredient_id "$workflow_ingredient_id" \
      '{
        name: $name,
        description: "E2E StackStorm workflow remediation through Alertmanager guards",
        enabled: true,
        recipe_ingredients: [
          {
            ingredient_id: $guard_ingredient_id,
            step_order: 10,
            on_success: "continue",
            parallel_group: 0,
            depth: 10,
            service_payload: {
              fingerprint: $fingerprint,
              labels: {alertname: $name, group_name: $name, severity: "critical"},
              active: true,
              limit: 1
            },
            service_exec_parameters_override: {
              operation: "verify_firing",
              guard_role: "remediation_precondition",
              false_outcome: "cancel_downstream_no_remediation"
            },
            service_exec_expected_secs: 5,
            service_exec_timeout: 30,
            service_exec_expected_outcome: {is_firing: true},
            run_phase: "firing",
            run_condition: "always"
          },
          {
            ingredient_id: $evidence_ingredient_id,
            step_order: 20,
            on_success: "continue",
            parallel_group: 0,
            depth: 20,
            service_payload: {limit: 20},
            service_exec_parameters_override: {operation: "list_nodes"},
            service_exec_expected_secs: 20,
            service_exec_timeout: 180,
            service_exec_expected_outcome: {success: true},
            run_phase: "firing",
            run_condition: "always"
          },
          {
            ingredient_id: $guard_ingredient_id,
            step_order: 30,
            on_success: "continue",
            parallel_group: 0,
            depth: 30,
            service_payload: {
              fingerprint: $fingerprint,
              labels: {alertname: $name, group_name: $name, severity: "critical"},
              active: true,
              limit: 1
            },
            service_exec_parameters_override: {
              operation: "verify_firing",
              guard_role: "remediation_precondition",
              false_outcome: "cancel_downstream_no_remediation"
            },
            service_exec_expected_secs: 5,
            service_exec_timeout: 30,
            service_exec_expected_outcome: {is_firing: true},
            run_phase: "firing",
            run_condition: "always"
          },
          {
            ingredient_id: $workflow_ingredient_id,
            step_order: 40,
            on_success: "continue",
            parallel_group: 0,
            depth: 40,
            service_payload: {
              workflow_ref: $workflow_ref,
              inputs: {
                alert_group_name: $name,
                host: "e2e-node"
              }
            },
            service_exec_parameters_override: {operation: "execute_workflow"},
            service_exec_expected_secs: 60,
            service_exec_timeout: 600,
            service_exec_expected_outcome: {status: "succeeded"},
            run_phase: "firing",
            run_condition: "always"
          }
        ]
      }'
)"
recipe="$(api_request_json POST "/recipes/" "$recipe_payload")"
recipe_id="$(echo "$recipe" | jq -r '.id // empty')"
[ -n "$recipe_id" ] && [ "$recipe_id" != "null" ] || fail "created recipe did not include an id"
recipe_ids+=("$recipe_id")
assert_json "$recipe" \
    '.recipe_ingredients | length == 4' \
    "created recipe did not include four remediation steps"

log "posting PoundCake webhook for ${recipe_name}"
order_id="$(post_remediation_alert "$recipe_name" "$req_id" "$fingerprint")"
order="$(wait_for_order_status "$order_id" "complete")"
assert_json "$order" '.processing_status == "complete"' "workflow remediation order did not complete"
assert_json "$order" '.remediation_outcome == "succeeded"' "workflow remediation outcome was not succeeded"

dishes="$(collect_order_dishes "$order_id")"
dish_id="$(echo "$dishes" | jq -r '.[0].id // empty')"
[ -n "$dish_id" ] || fail "order ${order_id} did not create a dish"

ingredients="$(api_request_json GET "/dishes/${dish_id}/ingredient-status")"
assert_json "$ingredients" \
    '[.[] | select(
      .service_type == "alertmanager"
      and .service_exec == "inspect"
      and .service_exec_status == "succeeded"
    )] | length == 2' \
    "expected both Alertmanager firing guards to succeed"
assert_json "$ingredients" \
    '[.[] | select(
      .service_type == "k8s"
      and .service_exec == "node_triage"
      and .service_exec_status == "succeeded"
    )] | length == 1' \
    "expected k8s node evidence step to succeed"
assert_json "$ingredients" \
    '[.[] | select(
      .service_type == "stackstorm"
      and .service_exec == "workflow_execution"
      and .service_exec_status == "succeeded"
    )] | length == 1' \
    "expected StackStorm workflow remediation step to succeed"

executions="$(wait_for_stackstorm_workflow_execution "$workflow_action")"
execution_id="$(
    echo "$executions" | jq -r --arg action "$workflow_action" '
      .[]
      | select(
          (
            .action.ref? == ("poundcake." + $action)
            or .action? == ("poundcake." + $action)
            or (.action.name? == $action and .action.pack? == "poundcake")
          )
          and .status == "succeeded"
        )
      | .id
    ' | head -n 1
)"
[ -n "$execution_id" ] && [ "$execution_id" != "null" ] || fail "StackStorm workflow execution id was not found"

guard_false_recipe_name="${recipe_name}-guard-false"
guard_false_req_id="${req_id}-GUARD-FALSE"
guard_false_fingerprint="missing-${fingerprint}"
before_guard_false_executions="$(count_stackstorm_workflow_executions "$workflow_action")"

log "creating guard-false remediation recipe ${guard_false_recipe_name}"
guard_false_recipe_payload="$(
    echo "$recipe_payload" | jq \
      --arg name "$guard_false_recipe_name" \
      --arg fingerprint "$guard_false_fingerprint" \
      --arg req_id "$guard_false_req_id" \
      '
        .name = $name
        | .recipe_ingredients[0].service_payload.fingerprint = $fingerprint
        | .recipe_ingredients[0].service_payload.labels = {alertname: $name, group_name: $name, severity: "critical"}
        | .recipe_ingredients[2].service_payload.fingerprint = $fingerprint
        | .recipe_ingredients[2].service_payload.labels = {alertname: $name, group_name: $name, severity: "critical"}
        | .recipe_ingredients[3].service_payload.inputs.alert_name = $name
        | .recipe_ingredients[3].service_payload.inputs.alert_group_name = $name
      '
)"
guard_false_recipe="$(api_request_json POST "/recipes/" "$guard_false_recipe_payload")"
guard_false_recipe_id="$(echo "$guard_false_recipe" | jq -r '.id // empty')"
[ -n "$guard_false_recipe_id" ] && [ "$guard_false_recipe_id" != "null" ] || fail "created guard-false recipe did not include an id"
recipe_ids+=("$guard_false_recipe_id")

log "posting PoundCake webhook for guard-false ${guard_false_recipe_name}"
guard_false_order_id="$(post_remediation_alert "$guard_false_recipe_name" "$guard_false_req_id" "$guard_false_fingerprint")"
guard_false_order="$(wait_for_order_status "$guard_false_order_id" "complete")"
assert_json "$guard_false_order" '.remediation_outcome == "none"' "guard-false remediation outcome was not none"

guard_false_dishes="$(collect_order_dishes "$guard_false_order_id")"
guard_false_dish_id="$(echo "$guard_false_dishes" | jq -r '.[0].id // empty')"
[ -n "$guard_false_dish_id" ] || fail "guard-false order ${guard_false_order_id} did not create a dish"
guard_false_ingredients="$(api_request_json GET "/dishes/${guard_false_dish_id}/ingredient-status")"
assert_json "$guard_false_ingredients" \
    '[.[] | select(
      .service_type == "alertmanager"
      and .service_exec == "inspect"
      and .service_exec_status == "failed"
    )] | length == 1' \
    "expected first Alertmanager guard to fail when alert was not firing"
assert_json "$guard_false_ingredients" \
    '[.[] | select(.service_exec_status == "canceled")] | length == 3' \
    "expected downstream guard-false remediation rows to be canceled"
assert_json "$guard_false_ingredients" \
    '[.[] | select(
      .service_type == "stackstorm"
      and .service_exec == "workflow_execution"
      and .service_exec_status == "canceled"
    )] | length == 1' \
    "expected StackStorm remediation row to be canceled before execution"

after_guard_false_executions="$(count_stackstorm_workflow_executions "$workflow_action")"
[ "$after_guard_false_executions" = "$before_guard_false_executions" ] \
    || fail "guard-false path created an unexpected StackStorm workflow execution"

log "PASS stackstorm workflow remediation order_id=${order_id} dish_id=${dish_id} execution_id=${execution_id} guard_false_order_id=${guard_false_order_id}"

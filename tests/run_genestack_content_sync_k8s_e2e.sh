#!/usr/bin/env bash
# Genestack content-sync + PrometheusRule CRD publication e2e runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
API_URL_WAS_SET="${API_URL+x}"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
source "${SCRIPT_DIR}/lib.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18003}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-genestack-content-sync-e2e}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-300}"
CONTENT_SYNC_TASK_KEY="${CONTENT_SYNC_TASK_KEY:-plugin-content-sync:genestack_monitoring}"
K8S_RECIPE_NAMES="${K8S_RECIPE_NAMES:-kube-pod-crash-looping-critical kube-pod-container-restarts-critical}"
KUBECTL_BIN="${KUBECTL_BIN:-}"

log() {
  printf '[genestack-content-sync-k8s-e2e] %s\n' "$*"
}

fail() {
  printf '[genestack-content-sync-k8s-e2e] ERROR: %s\n' "$*" >&2
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

secret_value() {
  local namespace="$1"
  local secret_name="$2"
  local key="$3"
  "$KUBECTL_BIN" -n "$namespace" get secret "$secret_name" \
    -o "jsonpath={.data.${key}}" 2>/dev/null | decode_b64
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

wait_for_scheduled_task_idle() {
  local task_id="$1"
  local start now
  start="$(date +%s)"
  while true; do
    local task_json status
    task_json="$(api_request_json GET "/scheduled-tasks/${task_id}")"
    status="$(echo "$task_json" | jq -r '.status')"
    if [ "$status" = "idle" ]; then
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for scheduled task ${task_id} to become idle; actual=${status}"
      echo "$task_json" | jq . >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

wait_for_scheduled_task_new_order() {
  local task_id="$1"
  local previous_order_id="$2"
  local start now
  start="$(date +%s)"
  while true; do
    local task_json order_id
    task_json="$(api_request_json GET "/scheduled-tasks/${task_id}")"
    order_id="$(echo "$task_json" | jq -r '.last_order_id // empty')"
    if [ -n "$order_id" ] && [ "$order_id" != "null" ] && [ "$order_id" != "$previous_order_id" ]; then
      printf '%s\n' "$order_id"
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for scheduled task ${task_id} to attach a new order"
      echo "$task_json" | jq . >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

wait_for_scheduled_task_order_status() {
  local task_id="$1"
  local order_id="$2"
  local expected_status="$3"
  local start now
  start="$(date +%s)"
  while true; do
    local task_json status last_order_id
    task_json="$(api_request_json GET "/scheduled-tasks/${task_id}")"
    status="$(echo "$task_json" | jq -r '.last_status // empty')"
    last_order_id="$(echo "$task_json" | jq -r '.last_order_id // empty')"
    if [ "$last_order_id" = "$order_id" ] && [ "$status" = "$expected_status" ]; then
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for scheduled task ${task_id} last_status=${expected_status}; actual=${status}"
      echo "$task_json" | jq . >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

crd_name_for_alert() {
  local alert_name="$1"
  local raw normalized suffix max_suffix_len prefix
  raw="$(printf '%s' "$alert_name" | sed -E 's/([a-z0-9])([A-Z])/\1-\2/g')"
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"
  suffix="${normalized:-alert}"
  prefix="genestack-monitoring-"
  max_suffix_len=$((63 - ${#prefix}))
  suffix="${suffix:0:${max_suffix_len}}"
  suffix="$(printf '%s' "$suffix" | sed -E 's/-+$//')"
  [ -n "$suffix" ] || suffix="alert"
  printf 'genestack-monitoring-%s\n' "$suffix"
}

assert_recipe_action_provider() {
  local recipe_json="$1"
  local expected_service_type="$2"
  local expected_service_exec="$3"
  local actual
  actual="$(
    echo "$recipe_json" | jq -r '
      [
        .recipe_ingredients[]?
        | select((.service_exec_parameters_override.managed_role // "") == "action_alert")
        | .ingredient
        | "\(.service_type)//\(.service_exec)"
      ][0] // empty
    '
  )"
  [ "$actual" = "${expected_service_type}//${expected_service_exec}" ] || fail \
    "Expected managed recipe provider ${expected_service_type}/${expected_service_exec}; actual=${actual:-missing}"
}

get_recipe_from_list() {
  local recipes_json="$1"
  local recipe_name="$2"
  echo "$recipes_json" | jq -c --arg recipe_name "$recipe_name" '
    [
      .[]
      | select(.name == $recipe_name)
    ][0]
  '
}

assert_prometheus_rule_crd() {
  local alert_name="$1"
  local crd_name crd_json
  crd_name="$(crd_name_for_alert "$alert_name")"
  crd_json="$("$KUBECTL_BIN" -n "$MONITORING_NAMESPACE" get prometheusrule "$crd_name" -o json 2>/dev/null || true)"
  [ -n "$crd_json" ] || fail "expected PrometheusRule CRD ${MONITORING_NAMESPACE}/${crd_name} for alert ${alert_name}"
  echo "$crd_json" | jq -e --arg alert_name "$alert_name" '
    .metadata.labels["managed-by"] == "poundcake"
    and ([.spec.groups[]?.rules[]? | select(.alert == $alert_name)] | length) >= 1
  ' >/dev/null 2>&1 || {
    echo "$crd_json" | jq . >&2 || true
    fail "PrometheusRule CRD ${MONITORING_NAMESPACE}/${crd_name} did not contain alert ${alert_name}"
  }
}

mkdir -p "$STATE_DIR"
rm -f "$STATE_DIR"/*.pid "$STATE_DIR"/*.log

require_cmd curl
require_cmd jq
KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

"$KUBECTL_BIN" get namespace "$POUNDCAKE_NAMESPACE" >/dev/null
"$KUBECTL_BIN" get namespace "$MONITORING_NAMESPACE" >/dev/null
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-dishwasher --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-prep-chef --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-timer --timeout=5m

if [ -z "$API_URL_WAS_SET" ]; then
  export API_ROOT_URL="http://127.0.0.1:${API_LOCAL_PORT}"
  export API_URL="${API_ROOT_URL}/api/v1"
  start_port_forward api "$POUNDCAKE_NAMESPACE" svc/poundcake-api "${API_LOCAL_PORT}:8000"
fi

wait_for_api_ready
if [ -z "$AUTH_PASSWORD_WAS_SET" ]; then
  AUTH_PASSWORD="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-admin" "password")"
  export AUTH_PASSWORD
fi
if [ -z "$WEBHOOK_BEARER_TOKEN_WAS_SET" ]; then
  WEBHOOK_BEARER_TOKEN="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-secrets" "WEBHOOK_BEARER_TOKEN")"
  export WEBHOOK_BEARER_TOKEN
fi

authenticate_api_if_required
wait_for_plugin_health "genestack_monitoring" "healthy" >/dev/null
wait_for_plugin_health "k8s" "healthy" >/dev/null
wait_for_plugin_health "prometheus" "healthy" >/dev/null
wait_for_plugin_health "github" "healthy" >/dev/null

log "configuring GitHub adapter public-read policy for Genestack content sync"
POUNDCAKE_NAMESPACE="$POUNDCAKE_NAMESPACE" WAIT_TIMEOUT=5m \
  bash "$PROJECT_ROOT/helm/devstack/configure-github-adapter.sh" >/dev/null

log "finding Genestack content-sync scheduled task"
scheduled_tasks="$(api_request_json GET "/scheduled-tasks?service_type=genestack_monitoring")"
content_sync_task_id="$(
  echo "$scheduled_tasks" | jq -r --arg task_key "$CONTENT_SYNC_TASK_KEY" '
    [.[] | select(.task_key == $task_key) | .id][0] // empty
  '
)"
[ -n "$content_sync_task_id" ] || fail "could not find scheduled task ${CONTENT_SYNC_TASK_KEY}"

wait_for_scheduled_task_idle "$content_sync_task_id"
previous_content_sync_order_id="$(
  api_request_json GET "/scheduled-tasks/${content_sync_task_id}" | jq -r '.last_order_id // empty'
)"

log "requesting immediate Genestack content sync"
api_request_json POST "/scheduled-tasks/${content_sync_task_id}/run-now" "" >/dev/null
content_sync_order_id="$(wait_for_scheduled_task_new_order "$content_sync_task_id" "$previous_content_sync_order_id")"
content_sync_order="$(wait_for_order_terminal "$content_sync_order_id")"
assert_json "$content_sync_order" '.processing_status == "complete"' "Genestack content sync order did not complete"
wait_for_scheduled_task_order_status "$content_sync_task_id" "$content_sync_order_id" "succeeded"

recipes_json="$(api_request_json GET "/recipes/")"
IFS=' ' read -r -a k8s_recipe_names <<< "$K8S_RECIPE_NAMES"
for recipe_name in "${k8s_recipe_names[@]}"; do
  recipe_json="$(get_recipe_from_list "$recipes_json" "$recipe_name")"
  echo "$recipe_json" | jq -e '.name != null' >/dev/null 2>&1 || fail \
    "could not find managed Genestack k8s recipe ${recipe_name}"
  assert_recipe_action_provider "$recipe_json" "k8s" "pod_action"
  assert_prometheus_rule_crd "$recipe_name"
done

log "PASS content_sync_order_id=${content_sync_order_id} recipes=${K8S_RECIPE_NAMES}"
exit 0

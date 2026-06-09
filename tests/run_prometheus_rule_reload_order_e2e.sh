#!/usr/bin/env bash
# PrometheusRule edit -> reload -> cluster alert -> order execution e2e runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
source "${SCRIPT_DIR}/lib.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
TEST_NAMESPACE="${TEST_NAMESPACE:-$POUNDCAKE_NAMESPACE}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18004}"
PROMETHEUS_LOCAL_PORT="${PROMETHEUS_LOCAL_PORT:-19090}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-prometheus-rule-reload-order-e2e}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-300}"
POD_CONTAINER_NAME="${POD_CONTAINER_NAME:-crasher}"
CONTENT_SYNC_TASK_KEY="${CONTENT_SYNC_TASK_KEY:-plugin-content-sync:genestack_monitoring}"
K8S_RECIPE_PATTERNS="${K8S_RECIPE_PATTERNS:-kube-pod-crash-looping kube-pod-container-restarts}"
PROMETHEUS_SERVICE="${PROMETHEUS_SERVICE:-kube-prometheus-stack-prometheus}"
NO_ORDER_WINDOW_SEC="${NO_ORDER_WINDOW_SEC:-45}"
KUBECTL_BIN="${KUBECTL_BIN:-}"

log() {
  printf '[prometheus-rule-reload-order-e2e] %s\n' "$*"
}

fail() {
  printf '[prometheus-rule-reload-order-e2e] ERROR: %s\n' "$*" >&2
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

uri_encode() {
  jq -nr --arg value "$1" '$value|@uri'
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

wait_for_crash_loop() {
  local pod="$1"
  local start now pod_json reason
  start="$(date +%s)"
  while true; do
    pod_json="$("$KUBECTL_BIN" -n "$TEST_NAMESPACE" get pod "$pod" -o json 2>/dev/null || true)"
    if echo "$pod_json" | jq -e . >/dev/null 2>&1; then
      reason="$(
        echo "$pod_json" | jq -r --arg container "$POD_CONTAINER_NAME" '
          [
            .status.containerStatuses[]?
            | select(.name == $container)
            | if .state.waiting.reason == "CrashLoopBackOff" then
                "CrashLoopBackOff"
              elif ((.restartCount // 0) > 0)
                and (
                  (.lastState.terminated.exitCode // null) != null
                  or (.state.terminated.exitCode // null) != null
                ) then
                "CrashLoopBackOff"
              else
                empty
              end
          ][0] // empty
        '
      )"
      if [ "$reason" = "CrashLoopBackOff" ]; then
        return 0
      fi
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for pod ${TEST_NAMESPACE}/${pod} to enter CrashLoopBackOff; reason=${reason:-unknown}"
      "$KUBECTL_BIN" -n "$TEST_NAMESPACE" describe pod "$pod" >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

wait_for_pod_deleted() {
  local pod="$1"
  local start now
  start="$(date +%s)"
  while true; do
    if ! "$KUBECTL_BIN" -n "$TEST_NAMESPACE" get pod "$pod" >/dev/null 2>&1; then
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for pod ${TEST_NAMESPACE}/${pod} to be deleted"
      "$KUBECTL_BIN" -n "$TEST_NAMESPACE" get pod "$pod" -o wide >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

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

find_recipe_name_by_patterns() {
  local recipes_json="$1"
  shift
  local pattern
  for pattern in "$@"; do
    local name
    name="$(
      echo "$recipes_json" | jq -r \
        --arg marker "[managed-by:poundcake-genestack-monitoring]" \
        --arg pattern "$pattern" '
          [
            .[]
            | select((.description // "") | contains($marker))
            | select((.name | ascii_downcase) | contains($pattern))
            | .name
          ][0] // empty
        '
    )"
    if [ -n "$name" ]; then
      printf '%s\n' "$name"
      return 0
    fi
  done
  return 1
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

wait_for_prometheus_alert_firing() {
  local recipe_name="$1"
  local pod="$2"
  local start now alerts
  start="$(date +%s)"
  while true; do
    alerts="$(curl -fsS "http://127.0.0.1:${PROMETHEUS_LOCAL_PORT}/api/v1/alerts" 2>/dev/null || true)"
    if echo "$alerts" | jq -e \
      --arg recipe_name "$recipe_name" \
      --arg namespace "$TEST_NAMESPACE" \
      --arg pod "$pod" '
        .status == "success"
        and (
          [
            .data.alerts[]?
            | select(.labels.alertname == $recipe_name)
            | select(.labels.namespace == $namespace)
            | select(.labels.pod == $pod)
            | select(.state == "firing")
          ] | length
        ) >= 1
      ' >/dev/null 2>&1; then
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for Prometheus alert ${recipe_name} to fire for pod ${pod}"
      echo "$alerts" | jq . >&2 || echo "$alerts" >&2
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

wait_for_prometheus_alert_absent() {
  local recipe_name="$1"
  local pod="$2"
  local start now alerts
  start="$(date +%s)"
  while true; do
    alerts="$(curl -fsS "http://127.0.0.1:${PROMETHEUS_LOCAL_PORT}/api/v1/alerts" 2>/dev/null || true)"
    if echo "$alerts" | jq -e \
      --arg recipe_name "$recipe_name" \
      --arg namespace "$TEST_NAMESPACE" \
      --arg pod "$pod" '
        .status == "success"
        and (
          [
            .data.alerts[]?
            | select(.labels.alertname == $recipe_name)
            | select(.labels.namespace == $namespace)
            | select(.labels.pod == $pod)
            | select(.state == "firing")
          ] | length
        ) == 0
      ' >/dev/null 2>&1; then
      return 0
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for Prometheus alert ${recipe_name} to stop firing for pod ${pod}"
      echo "$alerts" | jq . >&2 || echo "$alerts" >&2
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

latest_order_id_for_group() {
  local recipe_name="$1"
  local encoded
  encoded="$(uri_encode "$recipe_name")"
  api_request_json GET "/orders/status?alert_group_name=${encoded}&limit=1" \
    | jq -r '.[0].id // empty'
}

wait_for_new_order_for_group_and_pod() {
  local recipe_name="$1"
  local previous_order_id="$2"
  local pod="$3"
  local start now encoded statuses order_id
  encoded="$(uri_encode "$recipe_name")"
  start="$(date +%s)"
  while true; do
    statuses="$(api_request_json GET "/orders/status?alert_group_name=${encoded}&limit=5")"
    order_id="$(echo "$statuses" | jq -r '.[0].id // empty')"
    if [ -n "$order_id" ] && [ "$order_id" != "null" ]; then
      if [ "$order_id" != "$previous_order_id" ]; then
        printf '%s\n' "$order_id"
        return 0
      fi
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$TEST_TIMEOUT_SEC" ]; then
      log_error "Timed out waiting for a new order for ${recipe_name} and pod ${pod}"
      echo "$statuses" | jq . >&2 || true
      exit 1
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

assert_no_new_order_for_group() {
  local recipe_name="$1"
  local previous_order_id="$2"
  local window_sec="$3"
  local encoded start now statuses order_id
  encoded="$(uri_encode "$recipe_name")"
  start="$(date +%s)"
  while true; do
    statuses="$(api_request_json GET "/orders/status?alert_group_name=${encoded}&limit=5")"
    order_id="$(echo "$statuses" | jq -r '.[0].id // empty')"
    if [ -n "$order_id" ] && [ "$order_id" != "null" ] && [ "$order_id" != "$previous_order_id" ]; then
      log_error "Unexpected new order ${order_id} appeared for ${recipe_name} during non-firing validation"
      echo "$statuses" | jq . >&2 || true
      exit 1
    fi
    now="$(date +%s)"
    if [ $((now - start)) -ge "$window_sec" ]; then
      return 0
    fi
    sleep "$POLL_INTERVAL_SEC"
  done
}

restore_original_rule() {
  if [ -z "${restore_rule_json_file:-}" ] || [ ! -f "${restore_rule_json_file}" ]; then
    return 0
  fi
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  ${CAKECTL} ${cf:--u "${API_URL%/api/v1}"} plugins k8s rule set \
    --crd-name "${crd_name}" \
    --group-name "${group_name}" \
    --rule-name "${rule_name}" \
    --rule-file "${restore_rule_json_file}" \
    --namespace "${MONITORING_NAMESPACE}" >/dev/null 2>&1 || true
}

cleanup() {
  local pid_file pid
  restore_original_rule
  if [ -n "${pod_name:-}" ]; then
    "$KUBECTL_BIN" -n "$TEST_NAMESPACE" delete pod "$pod_name" --ignore-not-found >/dev/null 2>&1 || true
  fi
  rm -f "${restore_rule_json_file:-}" "${mutated_rule_json_file:-}"
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
KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

"$KUBECTL_BIN" get namespace "$TEST_NAMESPACE" >/dev/null
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-dishwasher --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-prep-chef --timeout=5m
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-timer --timeout=5m

export API_ROOT_URL="http://127.0.0.1:${API_LOCAL_PORT}"
export API_URL="${API_ROOT_URL}/api/v1"
start_port_forward api "$POUNDCAKE_NAMESPACE" svc/poundcake-api "${API_LOCAL_PORT}:8000"
start_port_forward prometheus "$MONITORING_NAMESPACE" "svc/${PROMETHEUS_SERVICE}" "${PROMETHEUS_LOCAL_PORT}:9090"

wait_for_api_ready
if [ -z "$AUTH_PASSWORD_WAS_SET" ]; then
  AUTH_PASSWORD="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-admin" "password")"
  export AUTH_PASSWORD
fi
if [ -z "${AUTH_USERNAME:-}" ]; then
  AUTH_USERNAME="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-admin" "username")"
  export AUTH_USERNAME
fi
if [ -z "${AUTH_PROVIDER:-}" ] && [ -n "${AUTH_USERNAME:-}" ]; then
  AUTH_PROVIDER="local"
  export AUTH_PROVIDER
fi
if [ -z "$WEBHOOK_BEARER_TOKEN_WAS_SET" ]; then
  WEBHOOK_BEARER_TOKEN="$(secret_value "$POUNDCAKE_NAMESPACE" "poundcake-secrets" "WEBHOOK_BEARER_TOKEN")"
  export WEBHOOK_BEARER_TOKEN
fi

authenticate_api_if_required
wait_for_plugin_health "genestack_monitoring" "healthy" >/dev/null
wait_for_plugin_health "k8s" "healthy" >/dev/null
wait_for_plugin_health "prometheus" "healthy" >/dev/null

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
IFS=' ' read -r -a k8s_patterns <<< "$K8S_RECIPE_PATTERNS"
k8s_recipe_name="$(find_recipe_name_by_patterns "$recipes_json" "${k8s_patterns[@]}")" || fail \
  "could not find managed Genestack k8s recipe matching: ${K8S_RECIPE_PATTERNS}"
k8s_recipe="$(
  echo "$recipes_json" | jq -c --arg recipe_name "$k8s_recipe_name" '
    [.[] | select(.name == $recipe_name)][0]
  '
)"
assert_recipe_action_provider "$k8s_recipe" "k8s" "pod_action"

suffix="$(generate_test_suffix)"
pod_name="e2e-prom-rule-reload-${suffix}"
log "creating crash-looping pod ${TEST_NAMESPACE}/${pod_name}"
cat <<YAML | "$KUBECTL_BIN" -n "$TEST_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  labels:
    app.kubernetes.io/name: poundcake-prometheus-rule-reload-e2e
    poundcake.e2e/run: "${suffix}"
spec:
  restartPolicy: Always
  containers:
    - name: ${POD_CONTAINER_NAME}
      image: busybox:1.36
      imagePullPolicy: IfNotPresent
      command:
        - sh
        - -c
        - "echo poundcake-prometheus-rule-reload-${suffix}; sleep 1; exit 1"
YAML

wait_for_crash_loop "$pod_name"

crd_name="$(crd_name_for_alert "$k8s_recipe_name")"
crd_json="$("$KUBECTL_BIN" -n "$MONITORING_NAMESPACE" get prometheusrule "$crd_name" -o json 2>/dev/null || true)"
[ -n "$crd_json" ] || fail "expected PrometheusRule CRD ${MONITORING_NAMESPACE}/${crd_name} for alert ${k8s_recipe_name}"
group_name="$(
  echo "$crd_json" | jq -r --arg rule_name "$k8s_recipe_name" '
    [
      .spec.groups[]?
      | select(any(.rules[]?; (.alert // .record // "") == $rule_name))
      | .name
    ][0] // empty
  '
)"
[ -n "$group_name" ] || fail "could not find group for ${k8s_recipe_name} in ${crd_name}"
rule_name="$k8s_recipe_name"

current_rule_json="$(
  api_request_json GET "/plugins/k8s/prometheus-rules/${crd_name}/rules/${rule_name}?group_name=$(uri_encode "$group_name")&namespace=$(uri_encode "$MONITORING_NAMESPACE")"
)"
restore_rule_json_file="$(mktemp "${STATE_DIR}/restore-rule.XXXXXX.json")"
mutated_rule_json_file="$(mktemp "${STATE_DIR}/mutated-rule.XXXXXX.json")"
echo "$current_rule_json" | jq '.rule_data' >"${restore_rule_json_file}"

if [[ "${k8s_recipe_name}" == *"crash-loop"* ]]; then
  mutated_expr="kube_pod_container_status_restarts_total{namespace=\"${TEST_NAMESPACE}\",pod=\"${pod_name}\",container=\"${POD_CONTAINER_NAME}\"} > 0"
else
  mutated_expr="kube_pod_container_status_restarts_total{namespace=\"${TEST_NAMESPACE}\",pod=\"${pod_name}\",container=\"${POD_CONTAINER_NAME}\"} > 0"
fi

echo "$current_rule_json" | jq \
  --arg expr "${mutated_expr}" \
  --arg group_name_label "${k8s_recipe_name}" \
  '.rule_data
   | .expr = $expr
   | .for = "0m"
   | .labels.group_name = $group_name_label' >"${mutated_rule_json_file}"

expected_order_group_name="$k8s_recipe_name"
previous_order_id="$(latest_order_id_for_group "$expected_order_group_name")"

log "editing live PrometheusRule ${crd_name}/${group_name}/${rule_name} through cakectl"
cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
${CAKECTL} ${cf:--u "${API_URL%/api/v1}"} plugins k8s rule set \
  --crd-name "${crd_name}" \
  --group-name "${group_name}" \
  --rule-name "${rule_name}" \
  --rule-file "${mutated_rule_json_file}" \
  --namespace "${MONITORING_NAMESPACE}" >/dev/null

wait_for_prometheus_alert_firing "${k8s_recipe_name}" "${pod_name}"
order_id="$(wait_for_new_order_for_group_and_pod "${expected_order_group_name}" "${previous_order_id}" "${pod_name}")"
order="$(wait_for_order_terminal "${order_id}")"
assert_json "$order" '.processing_status == "complete"' "cluster-generated order did not complete after PrometheusRule edit"

ingredients="$(collect_order_ingredients "$order_id")"
assert_json "$ingredients" \
  '[.[] | select(.service_type == "k8s" and .service_exec == "pod_action" and .service_exec_status == "succeeded")] | length >= 1' \
  "edited PrometheusRule did not drive successful k8s remediation"
assert_json "$ingredients" \
  '[.[] | select(.service_type == "alertmanager" and .service_exec == "inspect" and .operation == "verify_firing" and .service_exec_status == "succeeded")] | length >= 1' \
  "edited PrometheusRule did not keep Alertmanager firing verification active"

wait_for_pod_deleted "$pod_name"
pod_name=""

second_suffix="$(generate_test_suffix)"
pod_name="e2e-prom-rule-reload-off-${second_suffix}"
log "creating second crash-looping pod ${TEST_NAMESPACE}/${pod_name} for non-firing rollback validation"
cat <<YAML | "$KUBECTL_BIN" -n "$TEST_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  labels:
    app.kubernetes.io/name: poundcake-prometheus-rule-reload-e2e
    poundcake.e2e/run: "${second_suffix}"
spec:
  restartPolicy: Always
  containers:
    - name: ${POD_CONTAINER_NAME}
      image: busybox:1.36
      imagePullPolicy: IfNotPresent
      command:
        - sh
        - -c
        - "echo poundcake-prometheus-rule-reload-off-${second_suffix}; sleep 1; exit 1"
YAML

wait_for_crash_loop "$pod_name"

echo "$current_rule_json" | jq '.rule_data | .expr = "vector(0)" | .for = "0m"' >"${mutated_rule_json_file}"
previous_no_fire_order_id="$(latest_order_id_for_group "$k8s_recipe_name")"
previous_no_fire_order_id="$(latest_order_id_for_group "$expected_order_group_name")"

log "editing live PrometheusRule ${crd_name}/${group_name}/${rule_name} back to a non-firing expression"
${CAKECTL} ${cf:--u "${API_URL%/api/v1}"} plugins k8s rule set \
  --crd-name "${crd_name}" \
  --group-name "${group_name}" \
  --rule-name "${rule_name}" \
  --rule-file "${mutated_rule_json_file}" \
  --namespace "${MONITORING_NAMESPACE}" >/dev/null

wait_for_prometheus_alert_absent "${k8s_recipe_name}" "${pod_name}"
assert_no_new_order_for_group "${expected_order_group_name}" "${previous_no_fire_order_id}" "${NO_ORDER_WINDOW_SEC}"

log "PASS order_id=${order_id} recipe=${k8s_recipe_name} crd=${crd_name} no_order_window=${NO_ORDER_WINDOW_SEC}s"
exit 0

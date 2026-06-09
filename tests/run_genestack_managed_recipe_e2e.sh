#!/usr/bin/env bash
# Genestack-managed advertised-capability e2e runner for a running helm/devstack cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
source "${SCRIPT_DIR}/lib.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
TEST_NAMESPACE="${TEST_NAMESPACE:-$POUNDCAKE_NAMESPACE}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18002}"
ALERTMANAGER_LOCAL_PORT="${ALERTMANAGER_LOCAL_PORT:-19093}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-genestack-managed-recipe-e2e}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-300}"
POD_CONTAINER_NAME="${POD_CONTAINER_NAME:-crasher}"
CONTENT_SYNC_TASK_KEY="${CONTENT_SYNC_TASK_KEY:-plugin-content-sync:genestack_monitoring}"
K8S_RECIPE_PATTERNS="${K8S_RECIPE_PATTERNS:-kube-pod-crash-looping kube-pod-container-restarts}"
ALERTMANAGER_SERVICE="${ALERTMANAGER_SERVICE:-kube-prometheus-stack-alertmanager}"
KUBECTL_BIN="${KUBECTL_BIN:-}"

log() {
  printf '[genestack-managed-recipe-e2e] %s\n' "$*"
}

fail() {
  printf '[genestack-managed-recipe-e2e] ERROR: %s\n' "$*" >&2
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
  if [ -n "${k8s_recipe_name:-}" ]; then
    ${CAKECTL} -u "${API_URL%/api/v1}" recipes show "${k8s_recipe_name}" >/dev/null 2>&1 || true
  fi
  if [ -n "${pod_name:-}" ]; then
    "$KUBECTL_BIN" -n "$TEST_NAMESPACE" delete pod "$pod_name" --ignore-not-found >/dev/null 2>&1 || true
  fi
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
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
}

iso_future() {
  python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
}

seed_alertmanager_k8s_alert() {
  local recipe="$1"
  local starts_at ends_at payload response body code start now fingerprint alerts
  starts_at="$(iso_now)"
  ends_at="$(iso_future)"
  payload="$(
    jq -n \
      --arg recipe "$recipe" \
      --arg namespace "$TEST_NAMESPACE" \
      --arg pod "$pod_name" \
      --arg container "$POD_CONTAINER_NAME" \
      --arg starts_at "$starts_at" \
      --arg ends_at "$ends_at" \
      '[
        {
          labels: {
            alertname: $recipe,
            group_name: $recipe,
            severity: "critical",
            namespace: $namespace,
            pod: $pod,
            container: $container,
            instance: ($pod + "." + $namespace)
          },
          annotations: {
            summary: "Genestack managed k8s remediation e2e",
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
      echo "$alerts" | jq -r --arg recipe "$recipe" --arg namespace "$TEST_NAMESPACE" --arg pod "$pod_name" '
        [
          .[]
          | select(.labels.alertname == $recipe and .labels.group_name == $recipe)
          | select(.labels.namespace == $namespace and .labels.pod == $pod)
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

post_k8s_managed_alert() {
  local recipe="$1"
  local req_id="$2"
  local fingerprint="$3"
  local now payload response order_id
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  payload="$(
    jq -n \
      --arg recipe "$recipe" \
      --arg fingerprint "$fingerprint" \
      --arg namespace "$TEST_NAMESPACE" \
      --arg pod "$pod_name" \
      --arg container "$POD_CONTAINER_NAME" \
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
              namespace: $namespace,
              pod: $pod,
              container: $container,
              instance: ($pod + "." + $namespace)
            },
            annotations: {
              summary: "Genestack managed k8s remediation e2e",
              description: "Generated by tests/run_genestack_managed_recipe_e2e.sh"
            },
            startsAt: $now,
            endsAt: null,
            generatorURL: "http://prometheus:9090/graph"
          }
        ],
        groupLabels: {alertname: $recipe, group_name: $recipe},
        commonLabels: {alertname: $recipe, group_name: $recipe, namespace: $namespace, pod: $pod, container: $container},
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

post_stackstorm_managed_alert() {
  local recipe="$1"
  local req_id="$2"
  local fingerprint="$3"
  local now payload response order_id
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "$recipe" == *"etcd-members-down"* ]]; then
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
                instance: "etcd-0.monitoring.svc.cluster.local:2379",
                job: "etcd",
                cluster: "e2e-cluster"
              },
              annotations: {
                summary: "Genestack managed etcd remediation e2e",
                description: "Generated by tests/run_genestack_managed_recipe_e2e.sh"
              },
              startsAt: $now,
              endsAt: null,
              generatorURL: "http://prometheus:9090/graph"
            }
          ],
          groupLabels: {alertname: $recipe, group_name: $recipe},
          commonLabels: {alertname: $recipe, group_name: $recipe},
          commonAnnotations: {},
          externalURL: "http://alertmanager:9093",
          version: "4",
          groupKey: ("{}:{alertname=\"" + $recipe + "\"}")
        }'
    )"
  else
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
                instance: "https://127.0.0.1:8080/"
              },
              annotations: {
                summary: "Genestack managed blackbox remediation e2e",
                description: "Generated by tests/run_genestack_managed_recipe_e2e.sh"
              },
              startsAt: $now,
              endsAt: null,
              generatorURL: "http://prometheus:9090/graph"
            }
          ],
          groupLabels: {alertname: $recipe, group_name: $recipe},
          commonLabels: {alertname: $recipe, group_name: $recipe},
          commonAnnotations: {},
          externalURL: "http://alertmanager:9093",
          version: "4",
          groupKey: ("{}:{alertname=\"" + $recipe + "\"}")
        }'
    )"
  fi
  response="$(REQUEST_ID="$req_id" api_request_json POST "/webhook" "$payload")"
  order_id="$(echo "$response" | jq -r '.order_id // .results[0].order_id // .id // empty')"
  [ -n "$order_id" ] && [ "$order_id" != "null" ] || fail "webhook response did not include an order id"
  echo "$order_id"
}

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
start_port_forward alertmanager "$MONITORING_NAMESPACE" "svc/${ALERTMANAGER_SERVICE}" "${ALERTMANAGER_LOCAL_PORT}:9093"

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
wait_for_plugin_health "alertmanager" "healthy" >/dev/null

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
k8s_recipe="$(get_recipe_from_list "$recipes_json" "$k8s_recipe_name")"
assert_recipe_action_provider "$k8s_recipe" "k8s" "pod_action"

suffix="$(generate_test_suffix)"
pod_name="e2e-genestack-managed-${suffix}"
log "creating crash-looping pod ${TEST_NAMESPACE}/${pod_name}"
cat <<YAML | "$KUBECTL_BIN" -n "$TEST_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  labels:
    app.kubernetes.io/name: poundcake-genestack-managed-recipe-e2e
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
        - "echo poundcake-genestack-managed-${suffix}; sleep 1; exit 1"
YAML

wait_for_crash_loop "$pod_name"

log "seeding Alertmanager with firing alert for ${k8s_recipe_name}"
seeded_fingerprint="$(seed_alertmanager_k8s_alert "$k8s_recipe_name")"

log "posting managed k8s alert for ${k8s_recipe_name}"
k8s_order_id="$(post_k8s_managed_alert "$k8s_recipe_name" "E2E-GENESTACK-K8S-${suffix}" "$seeded_fingerprint")"
wait_for_runtime_match "$k8s_order_id" '
  .service_type == "alertmanager"
  and .service_exec == "inspect"
  and .operation == "verify_firing"
  and .service_exec_status == "succeeded"
' >/dev/null
wait_for_runtime_match "$k8s_order_id" '
  .execution_role == "gather_evidence"
  and .service_exec_status == "succeeded"
' >/dev/null
wait_for_runtime_match "$k8s_order_id" '
  .service_type == "k8s"
  and .service_exec == "pod_action"
  and .service_exec_status == "succeeded"
' >/dev/null

k8s_ingredients="$(collect_order_ingredients "$k8s_order_id")"
assert_json "$k8s_ingredients" \
  '[.[] | select(.service_type == "alertmanager" and .service_exec == "inspect" and .operation == "verify_firing" and .service_exec_status == "succeeded")] | length >= 1' \
  "managed k8s order did not keep the Alertmanager firing guard active"
assert_json "$k8s_ingredients" \
  '[.[] | select(.execution_role == "gather_evidence" and .service_exec_status == "succeeded")] | length >= 1' \
  "managed k8s order did not execute evidence before remediation"
assert_json "$k8s_ingredients" \
  '[.[] | select(.service_type == "k8s" and .service_exec == "pod_action" and .service_exec_status == "succeeded")] | length >= 1' \
  "managed k8s order did not execute native k8s pod_action"
wait_for_pod_deleted "$pod_name"
pod_name=""

log "PASS content_sync_order_id=${content_sync_order_id} k8s_order_id=${k8s_order_id}"
exit 0

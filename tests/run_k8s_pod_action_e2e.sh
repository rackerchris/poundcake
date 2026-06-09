#!/usr/bin/env bash
# Kubernetes pod_action e2e runner for a running helm/devstack cluster.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
API_URL_WAS_SET="${API_URL+x}"
AUTH_PASSWORD_WAS_SET="${AUTH_PASSWORD+x}"
WEBHOOK_BEARER_TOKEN_WAS_SET="${WEBHOOK_BEARER_TOKEN+x}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-180}"
source "${SCRIPT_DIR}/lib.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
TEST_NAMESPACE="${TEST_NAMESPACE:-$POUNDCAKE_NAMESPACE}"
API_LOCAL_PORT="${API_LOCAL_PORT:-18000}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-k8s-pod-action-e2e}"
POD_CONTAINER_NAME="${POD_CONTAINER_NAME:-crasher}"
KUBECTL_BIN="${KUBECTL_BIN:-}"

log() {
  printf '[k8s-pod-action-e2e] %s\n' "$*"
}

fail() {
  printf '[k8s-pod-action-e2e] ERROR: %s\n' "$*" >&2
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

cleanup_recipe_best_effort() {
  local id="$1"
  local cake_ctl="${CAKECTL:-cakectl}"
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  ${cake_ctl} ${cf:--u "${API_URL%/api/v1}"} recipes delete "${id}" --yes >/dev/null 2>&1 || true
}

cleanup() {
  local pid_file pid
  if [ -n "${recipe_id:-}" ]; then
    cleanup_recipe_best_effort "$recipe_id"
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
  return 0
}

finish() {
  local status=$?
  set +e
  cleanup
  exit "$status"
}
trap finish EXIT

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

post_k8s_alert() {
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
              instance: ($pod + "." + $namespace)
            },
            annotations: {
              summary: "Kubernetes pod_action e2e",
              description: "Generated by tests/run_k8s_pod_action_e2e.sh"
            },
            startsAt: $now,
            endsAt: null,
            generatorURL: "http://prometheus:9090/graph"
          }
        ],
        groupLabels: {alertname: $recipe, group_name: $recipe},
        commonLabels: {alertname: $recipe, group_name: $recipe, namespace: $namespace, pod: $pod},
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
wait_for_plugin_health "k8s" "healthy" >/dev/null

suffix="$(generate_test_suffix)"
pod_name="e2e-k8s-pod-action-${suffix}"
recipe_name="e2e-k8s-pod-action-${suffix}"
log_marker="poundcake-k8s-pod-action-e2e-${suffix}"
recipe_id=""

log "creating crash-looping pod ${TEST_NAMESPACE}/${pod_name}"
cat <<YAML | "$KUBECTL_BIN" -n "$TEST_NAMESPACE" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  labels:
    app.kubernetes.io/name: poundcake-k8s-pod-action-e2e
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
        - "echo ${log_marker}; sleep 1; exit 1"
YAML

wait_for_crash_loop "$pod_name"

registry="$(api_request_json GET "/service-registry/ingredients")"
ingredient_id="$(
  echo "$registry" | jq -r '
    .[]
    | select(
        .service_type == "k8s"
        and .service_exec == "pod_action"
        and .task_key_template == "k8s-pod-action"
      )
    | .id
  ' | head -n 1
)"
[ -n "$ingredient_id" ] && [ "$ingredient_id" != "null" ] || fail "k8s pod_action ingredient is not registered"

log "creating temporary recipe ${recipe_name}"
recipe_payload="$(
  jq -n \
    --arg name "$recipe_name" \
    --arg namespace "$TEST_NAMESPACE" \
    --arg pod "$pod_name" \
    --arg container "$POD_CONTAINER_NAME" \
    --argjson ingredient_id "$ingredient_id" \
    '{
      name: $name,
      description: "E2E Kubernetes pod_action logs/events/delete",
      enabled: true,
      recipe_ingredients: [
        {
          ingredient_id: $ingredient_id,
          step_order: 1,
          on_success: "continue",
          parallel_group: 0,
          depth: 1,
          service_payload: {
            namespace: $namespace,
            pod_name: $pod,
            container: $container,
            tail_lines: 50,
            previous: true
          },
          service_exec_parameters_override: {operation: "logs"},
          service_exec_expected_secs: 10,
          service_exec_timeout: 120,
          service_exec_expected_outcome: {success: true},
          run_phase: "firing",
          run_condition: "always"
        },
        {
          ingredient_id: $ingredient_id,
          step_order: 2,
          on_success: "continue",
          parallel_group: 0,
          depth: 2,
          service_payload: {namespace: $namespace, pod_name: $pod},
          service_exec_parameters_override: {operation: "events"},
          service_exec_expected_secs: 10,
          service_exec_timeout: 120,
          service_exec_expected_outcome: {success: true},
          run_phase: "firing",
          run_condition: "always"
        },
        {
          ingredient_id: $ingredient_id,
          step_order: 3,
          on_success: "continue",
          parallel_group: 0,
          depth: 3,
          service_payload: {namespace: $namespace, pod_name: $pod},
          service_exec_parameters_override: {operation: "delete"},
          service_exec_expected_secs: 10,
          service_exec_timeout: 120,
          service_exec_expected_outcome: {success: true},
          run_phase: "firing",
          run_condition: "always"
        }
      ]
    }'
)"
recipe="$(api_request_json POST "/recipes/" "$recipe_payload")"
recipe_id="$(echo "$recipe" | jq -r '.id')"
assert_json "$recipe" '.recipe_ingredients | length == 3' "created recipe did not include three k8s pod_action steps"

req_id="E2E-K8S-POD-ACTION-${suffix}"
fingerprint="k8s-pod-action-${suffix}"
log "posting webhook for ${recipe_name}"
order_id="$(post_k8s_alert "$recipe_name" "$req_id" "$fingerprint")"
order="$(wait_for_order_status "$order_id" "complete")"
assert_json "$order" '.processing_status == "complete"' "k8s pod_action order did not complete"

ingredients="$(collect_order_ingredients "$order_id")"
assert_json "$ingredients" \
  '[.[] | select(.service_type == "k8s" and .service_exec == "pod_action" and .service_exec_status == "succeeded")] | length == 3' \
  "expected three succeeded k8s pod_action runtime rows"
assert_json "$ingredients" \
  '[
    .[]
    | select(.service_type == "k8s" and .service_exec == "pod_action")
    | {step_order, depth, status: .service_exec_status}
  ] | sort_by(.step_order) == [
    {step_order: 1, depth: 1, status: "succeeded"},
    {step_order: 2, depth: 2, status: "succeeded"},
    {step_order: 3, depth: 3, status: "succeeded"}
  ]' \
  "k8s pod_action rows did not run as blocking steps in logs/events/delete order"

wait_for_pod_deleted "$pod_name"
pod_name=""
log "PASS k8s pod_action order_id=${order_id} recipe_id=${recipe_id}"
exit 0

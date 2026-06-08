#!/usr/bin/env bash
# Shared helpers for PoundCake E2E tests — all API calls via cakectl CLI.

set -euo pipefail

: "${API_URL:=http://127.0.0.1:8000/api/v1}"
: "${API_ROOT_URL:=}"
: "${AUTH_USERNAME:=admin}"
: "${AUTH_PASSWORD:=cjK1c6tYTsUYf8cDHmE49FjS}"
: "${AUTH_PROVIDER:=local}"
: "${WEBHOOK_BEARER_TOKEN:=}"
: "${TEST_TIMEOUT_SEC:=60}"
: "${POLL_INTERVAL_SEC:=1}"
: "${DEBUG:=0}"
: "${CAKECTL:=cakectl}"
: "${CAKECTL_URL:=}"

log_info() {
  echo "[INFO] $*"
}

log_error() {
  echo "[ERROR] $*" >&2
}

debug_log() {
  if [ "${DEBUG}" = "1" ]; then
    echo "[DEBUG] $*" >&2
  fi
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "${cmd} is required"
    exit 1
  fi
}

# Ensure the Kind API port-forward is active
# This is idempotent: it only starts the process if port 8000 is not listening.
ensure_port_forward() {
  if ! curl -sf http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    debug_log "starting kind port-forward to poundcake-api:8000"
    nohup /opt/homebrew/bin/kubectl port-forward svc/poundcake-api -n poundcake 8000:8000 --address 127.0.0.1 >/dev/null 2>&1 &
    disown
  fi
}

generate_test_suffix() {
  local suffix
  suffix="$(date +%s%N 2>/dev/null || true)"
  case "${suffix}" in
    ""|*N*) suffix="$(date +%s)-$$-${RANDOM}" ;;
  esac
  echo "${suffix}"
}

api_request_json() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local url="${API_URL}${path}"

  local extra=()
  if [ -n "${REQUEST_ID:-}" ]; then
    extra+=("--header" "X-Request-ID: ${REQUEST_ID}")
  fi

  # Webhook endpoint: no-session + Bearer token
  if [ "${path}" = "/webhook" ] && [ -n "${WEBHOOK_BEARER_TOKEN}" ]; then
    extra=("--no-session" "--header" "Authorization: Bearer ${WEBHOOK_BEARER_TOKEN}")
    method="POST"
  fi

  local body_file=""
  if [ -n "${data}" ]; then
    body_file="$(mktemp)"
    printf '%s' "${data}" > "${body_file}"
  fi

  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  if [ -z "${cf}" ]; then
    cf="-u ${API_URL%/api/v1}"
  fi
  local resp
  resp="$(${CAKECTL} ${cf:-} -f json api request \
      $method "${path#}" \
      ${extra[@]+"${extra[@]}"} \
      ${body_file:+--body-file "${body_file}"})" 2>/dev/null || {
    log_error "API request failed: ${method} ${url}"
    echo "Command was: ${CAKECTL} ${cf:-} -f json api request ${method} ${path#} ${body_file:+--body-file ${body_file}}" >&2
    rm -f "${body_file}"
    exit 1
  }
  rm -f "${body_file}"
  printf '%s' "${resp}"
}

wait_for_api_ready() {
  local start now
  local api_root="${API_ROOT_URL:-${API_URL%/api/v1}}"
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${api_root}}"
  start=$(date +%s)

  ensure_port_forward

  while true; do
    local status
    if status=$(${CAKECTL} ${cake_flags} -f json ready 2>/dev/null); then
      local ready_status
      ready_status="$(echo "${status}" | jq -r '.status // ""' 2>/dev/null || true)"
      if [ "${ready_status}" = "healthy" ]; then
        return 0
      fi
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
      log_error "Timed out waiting for ready health check"
      echo "${status}" >&2
      exit 1
    fi
    sleep "${POLL_INTERVAL_SEC}"
  done
}

authenticate_api_if_required() {
  local api_root="${API_URL%/api/v1}"
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${api_root}}"

  local providers
  providers="$(${CAKECTL} ${cake_flags} -f json auth providers 2>/dev/null)" || return 0
  if ! echo "${providers}" | jq -e \
    --arg provider "${AUTH_PROVIDER}" \
    '.[] | select(.name == $provider and .password_login == true)' >/dev/null 2>&1; then
    return 0
  fi
  ${CAKECTL} ${cake_flags} -f json auth login \
     --provider "${AUTH_PROVIDER}" \
     --username "${AUTH_USERNAME}" \
     --password "${AUTH_PASSWORD}" >/dev/null 2>&1
}

wait_for_plugin_health() {
  local service_type="$1"
  local expected="${2:-healthy}"
  local start now
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${API_URL%/api/v1}}"
  ensure_port_forward
  start=$(date +%s)
  while true; do
    local health status
    health="$(${CAKECTL} ${cake_flags} -f json plugin health "${service_type}" 2>/dev/null)" || true
    status="$(echo "${health}" | jq -r '.health_status' 2>/dev/null)" || status="unknown"
    if [ "${status}" = "${expected}" ]; then
      printf '%s' "${health}"
      return 0
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
      log_error "Timed out waiting for ${service_type} plugin health=${expected}; actual=${status}"
      echo "${health:-no output}" | jq . >&2 || true
      exit 1
    fi
    sleep "${POLL_INTERVAL_SEC}"
  done
}

assert_json() {
  local json="$1"
  local filter="$2"
  local message="$3"
  if ! echo "${json}" | jq -e "${filter}" >/dev/null 2>&1; then
    log_error "${message}"
    echo "${json}" | jq . >&2
    exit 1
  fi
}

alertmanager_payload() {
  local recipe="$1"
  local status="$2"
  local fingerprint="$3"
  local instance="${4:-localhost:9090}"
  local severity="${5:-critical}"
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq -n \
    --arg recipe "${recipe}" \
    --arg status "${status}" \
    --arg fingerprint "${fingerprint}" \
    --arg instance "${instance}" \
    --arg severity "${severity}" \
    --arg now "${now}" \
    '{
      receiver: "poundcake",
      status: $status,
      alerts: [
        {
          status: $status,
          fingerprint: $fingerprint,
          labels: {
            alertname: "DummyContractE2E",
            group_name: $recipe,
            severity: $severity,
            instance: $instance
          },
          annotations: {
            summary: "Dummy service plugin contract e2e",
            description: "Generated by tests/run_e2e.sh"
          },
          startsAt: $now,
          endsAt: (if $status == "resolved" then $now else null end),
          generatorURL: "http://prometheus:9090/graph"
        }
      ],
      groupLabels: {alertname: "DummyContractE2E"},
      commonLabels: {
        alertname: "DummyContractE2E",
        group_name: $recipe
      },
      commonAnnotations: {},
      externalURL: "http://alertmanager:9093",
      version: "4",
      groupKey: "{}:{alertname=\"DummyContractE2E\"}"
    }'
}

post_alert() {
  local recipe="$1"
  local status="$2"
  local req_id="$3"
  local fingerprint="$4"
  local instance="${5:-localhost:9090}"
  local severity="${6:-critical}"
  local payload order_id

  payload="$(alertmanager_payload "${recipe}" "${status}" "${fingerprint}" "${instance}" "${severity}")"

  local tmpfile
  tmpfile="$(mktemp)"
  printf '%s' "${payload}" > "${tmpfile}"

  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${API_URL%/api/v1}}"

  order_id="$(${CAKECTL} ${cake_flags} \
    --webhook-token "${WEBHOOK_BEARER_TOKEN}" \
    webhook post -f "${tmpfile}" --order-id-only 2>/dev/null)" || true
  rm -f "${tmpfile}"

  if [ -z "${order_id}" ] || [ "${order_id}" = "null" ]; then
    log_error "Webhook response did not include an order id"
    echo "${payload}" >&2
    exit 1
  fi
  echo "${order_id}"
}

wait_for_order_status() {
  local order_id="$1"
  local expected="$2"
  local start now
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${API_URL%/api/v1}}"
  start=$(date +%s)
  while true; do
    local order_json status
    order_json="$(${CAKECTL} ${cake_flags} -f json orders show "${order_id}" 2>/dev/null)"
    status="$(echo "${order_json}" | jq -r '.processing_status')"
    if [ "${status}" = "${expected}" ]; then
      printf '%s' "${order_json}"
      return 0
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
      log_error "Timed out waiting for order ${order_id} status=${expected}; actual=${status}"
      echo "${order_json}" | jq . >&2 || true
      exit 1
    fi
    sleep "${POLL_INTERVAL_SEC}"
  done
}

wait_for_order_terminal() {
  local order_id="$1"
  local start now status order_json
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${API_URL%/api/v1}}"
  start=$(date +%s)
  while true; do
    order_json="$(${CAKECTL} ${cake_flags} -f json orders show "${order_id}" 2>/dev/null)"
    status="$(echo "${order_json}" | jq -r '.processing_status')"
    case "${status}" in
      complete|failed|canceled)
        printf '%s' "${order_json}"
        return 0
        ;;
    esac
    now=$(date +%s)
    if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
      log_error "Timed out waiting for order ${order_id} to become terminal; actual=${status}"
      echo "${order_json}" | jq . >&2 || true
      exit 1
    fi
    sleep "${POLL_INTERVAL_SEC}"
  done
}

collect_order_dishes() {
  local order_id="$1"
  local cf="${CAKECTL_URL:+-u ${CAKECTL_URL}}"
  local cake_flags="${cf:--u ${API_URL%/api/v1}}"
  ${CAKECTL} ${cake_flags} -f json dishes list \
    --order-id "${order_id}" 2>/dev/null
}

collect_order_ingredients() {
  local order_id="$1"
  local dishes dish_id all_ingredients
  dishes="$(collect_order_dishes "${order_id}" 2>&1)"
  if [ -z "${dishes}" ] || [ "${dishes}" = "[]" ]; then
    echo "[]"
    return 0
  fi
  all_ingredients="[]"
  while IFS= read -r dish_id; do
    [ -z "${dish_id}" ] && continue
    [ "${dish_id}" = "null" ] && continue
    local ingredients
    ingredients="$(${CAKECTL} -u "${API_URL%/api/v1}" -f json dishes show "${dish_id}" 2>/dev/null | jq -c '.ingredient_status // []')"
    all_ingredients="$(jq -n --argjson a "${all_ingredients}" --argjson b "${ingredients}" '$a + $b')"
  done <<< "$(echo "${dishes}" | jq -r '.[].id')"
  echo "${all_ingredients}"
}

wait_for_runtime_match() {
  local order_id="$1"
  local filter="$2"
  local start now ingredients count
  start=$(date +%s)
  while true; do
    ingredients="$(collect_order_ingredients "${order_id}")"
    count="$(echo "${ingredients}" | jq -r "[.[] | select(${filter})] | length")"
    if [ "${count}" -gt 0 ]; then
      printf '%s' "${ingredients}"
      return 0
    fi
    now=$(date +%s)
    if [ $((now - start)) -ge "${TEST_TIMEOUT_SEC}" ]; then
      log_error "Timed out waiting for runtime match on order ${order_id}: ${filter}"
      echo "${ingredients}" | jq . >&2 || true
      exit 1
    fi
    sleep "${POLL_INTERVAL_SEC}"
  done
}

configure_webhook_token() {
  local payload
  payload="$(jq -n \
    --arg token "${WEBHOOK_BEARER_TOKEN}" \
    '{config: {webhook_bearer_token: $token}}')"

  ${CAKECTL} -u "${API_URL%/api/v1}" -f json plugin configuration alertmanager \
    --config-json "${payload}" >/dev/null 2>&1
}

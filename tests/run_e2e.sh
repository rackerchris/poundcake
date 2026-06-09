#!/usr/bin/env bash
# Dummy service-plugin contract e2e runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

single_case=""

usage() {
  cat <<'EOF'
Usage:
  ./tests/run_e2e.sh [--api-url <url>] [--single <case>] [--list]

Cases:
  positive
  negative
  expected-negative
  cancel-parallel
  rapid-parallel
  cancel-during-flurry
  multi-group-blocking
  multi-group-preblock-failure
  suppressed

Examples:
  ./tests/run_e2e.sh
  ./tests/run_e2e.sh --single positive
  ./tests/run_e2e.sh --api-url http://127.0.0.1:8000/api/v1
EOF
}

list_cases() {
  printf '%s\n' positive negative expected-negative cancel-parallel rapid-parallel cancel-during-flurry multi-group-blocking multi-group-preblock-failure suppressed
}

while [ $# -gt 0 ]; do
  case "$1" in
    --api-url)
      API_URL="${2:-}"
      if [ -z "${API_URL}" ] || [[ "${API_URL}" == --* ]]; then
        log_error "--api-url requires a value"
        exit 1
      fi
      shift 2
      ;;
    --single)
      single_case="${2:-}"
      if [ -z "${single_case}" ] || [[ "${single_case}" == --* ]]; then
        log_error "--single requires a value"
        exit 1
      fi
      shift 2
      ;;
    --list)
      list_cases
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log_error "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

export API_URL
export CAKECTL="/Users/chris.breu/code/poundcake/.venv/bin/cakectl"
export AUTH_USERNAME="${AUTH_USERNAME:-}"
export AUTH_PROVIDER="${AUTH_PROVIDER:-}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-}"
export WEBHOOK_BEARER_TOKEN="${WEBHOOK_BEARER_TOKEN:-}"

preflight() {
  require_cmd curl
  require_cmd jq
  wait_for_api_ready
  authenticate_api_if_required

  local health registry
  health="$(wait_for_plugin_health "dummy" "healthy")"
  assert_json "${health}" '.health_status == "healthy"' "dummy plugin health is not healthy"

  registry="$(api_request_json GET "/service-registry/ingredients")"
  assert_json "${registry}" '[.[] | select(.service_type == "dummy" and .service_exec == "positive_result")] | length >= 1' \
    "dummy positive_result template is not registered"
  assert_json "${registry}" '[.[] | select(.service_type == "dummy" and .service_exec == "negative_result")] | length >= 1' \
    "dummy negative_result template is not registered"
  assert_json "${registry}" '[.[] | select(.service_type == "dummy" and .service_exec == "slow_result")] | length >= 1' \
    "dummy slow_result template is not registered"
}

run_positive() {
  local suffix req_id fingerprint order_id order ingredients
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-POSITIVE-${suffix}"
  fingerprint="dummy-positive-${suffix}"
  order_id="$(post_alert "dummy-positive-result" "firing" "${req_id}" "${fingerprint}")"
  order="$(wait_for_order_status "${order_id}" "complete")"
  ingredients="$(wait_for_runtime_match "${order_id}" '.service_exec == "positive_result" and .service_exec_status == "succeeded"')"

  assert_json "${order}" '.processing_status == "complete"' "positive order did not complete"
  assert_json "${ingredients}" \
    '[.[] | select(.service_exec == "positive_result" and .service_exec_status == "succeeded")] | length == 1' \
    "positive runtime row did not succeed"
  log_info "PASS positive order_id=${order_id}"
}

run_negative() {
  local suffix req_id fingerprint order_id order ingredients
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-NEGATIVE-${suffix}"
  fingerprint="dummy-negative-${suffix}"
  order_id="$(post_alert "dummy-negative-result" "firing" "${req_id}" "${fingerprint}")"
  order="$(wait_for_order_status "${order_id}" "failed")"
  ingredients="$(wait_for_runtime_match "${order_id}" '.service_exec == "negative_result" and .service_exec_status == "failed"')"

  assert_json "${order}" '.processing_status == "failed"' "negative order did not fail"
  assert_json "${ingredients}" \
    '[.[] | select(.service_exec == "negative_result" and .service_exec_status == "failed")] | length == 1' \
    "negative runtime row did not fail"
  log_info "PASS negative order_id=${order_id}"
}

run_expected_negative() {
  local suffix req_id fingerprint order_id order ingredients
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-EXPECTED-NEGATIVE-${suffix}"
  fingerprint="dummy-expected-negative-${suffix}"
  order_id="$(post_alert "dummy-expected-negative-result" "firing" "${req_id}" "${fingerprint}")"
  order="$(wait_for_order_status "${order_id}" "complete")"
  ingredients="$(wait_for_runtime_match "${order_id}" '.service_exec == "negative_result" and .service_exec_status == "succeeded"')"

  assert_json "${order}" '.processing_status == "complete"' "expected-negative order did not complete"
  assert_json "${ingredients}" \
    '[.[] | select(.service_exec == "negative_result" and .service_exec_status == "succeeded")] | length == 1' \
    "expected-negative runtime row did not convert expected failure into success"
  log_info "PASS expected-negative order_id=${order_id}"
}

run_cancel_parallel() {
  local suffix req_id fingerprint order_id firing_rows order ingredients slow_count canceled_count
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-CANCEL-PARALLEL-${suffix}"
  fingerprint="dummy-parallel-cancel-${suffix}"
  order_id="$(post_alert "dummy-parallel-slow-cancel-result" "firing" "${req_id}" "${fingerprint}")"

  firing_rows="$(wait_for_runtime_match "${order_id}" '.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched")')"
  assert_json "${firing_rows}" \
    '[.[] | select(.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched"))] | length == 2' \
    "parallel cancel setup did not dispatch two slow_result rows"

  post_alert "dummy-parallel-slow-cancel-result" "resolved" "${req_id}-RESOLVED" "${fingerprint}" >/dev/null
  order="$(wait_for_order_status "${order_id}" "complete")"
  ingredients="$(wait_for_runtime_match "${order_id}" '.service_exec == "slow_result" and .service_exec_status == "canceled"')"

  slow_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result")] | length')"
  canceled_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and .service_exec_status == "canceled")] | length')"
  if [ "${slow_count}" != "2" ] || [ "${canceled_count}" != "2" ]; then
    log_error "Expected both slow_result rows to be canceled; slow_count=${slow_count}, canceled_count=${canceled_count}"
    echo "${ingredients}" | jq . >&2
    exit 1
  fi
  assert_json "${order}" '.processing_status == "complete"' "cancel-parallel order did not complete after resolving work"
  log_info "PASS cancel-parallel order_id=${order_id}"
}

run_rapid_parallel() {
  local count suffix recipe idx req_id fingerprint order_id rows running_count order ingredients canceled_count
  local order_ids=()
  local fingerprints=()
  count="${RAPID_ALERT_COUNT:-3}"
  suffix="$(generate_test_suffix)"
  recipe="dummy-parallel-slow-cancel-result"

  for idx in $(seq 1 "${count}"); do
    req_id="E2E-DUMMY-RAPID-PARALLEL-${suffix}-${idx}"
    fingerprint="dummy-rapid-parallel-${suffix}-${idx}"
    order_id="$(post_alert "${recipe}" "firing" "${req_id}" "${fingerprint}")"
    order_ids+=("${order_id}")
    fingerprints+=("${fingerprint}")
  done

  for order_id in "${order_ids[@]}"; do
    rows="$(wait_for_runtime_match "${order_id}" '.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched")')"
    running_count="$(echo "${rows}" | jq -r '[.[] | select(.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched"))] | length')"
    if [ "${running_count}" != "2" ]; then
      log_error "Expected two parallel slow_result rows for order ${order_id}; running_count=${running_count}"
      echo "${rows}" | jq . >&2
      exit 1
    fi
  done

  for idx in "${!order_ids[@]}"; do
    req_id="E2E-DUMMY-RAPID-PARALLEL-${suffix}-${idx}-RESOLVED"
    post_alert "${recipe}" "resolved" "${req_id}" "${fingerprints[$idx]}" >/dev/null
  done

  for order_id in "${order_ids[@]}"; do
    order="$(wait_for_order_status "${order_id}" "complete")"
    ingredients="$(wait_for_runtime_match "${order_id}" '.service_exec == "slow_result" and .service_exec_status == "canceled"')"
    canceled_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and .service_exec_status == "canceled")] | length')"
    if [ "${canceled_count}" != "2" ]; then
      log_error "Expected both rapid parallel rows canceled for order ${order_id}; canceled_count=${canceled_count}"
      echo "${ingredients}" | jq . >&2
      exit 1
    fi
    assert_json "${order}" '.processing_status == "complete"' "rapid-parallel order did not complete"
  done

  log_info "PASS rapid-parallel order_count=${count} order_ids=${order_ids[*]}"
}

run_cancel_during_flurry() {
  local count suffix recipe idx target_idx target_order_id req_id fingerprint order_id rows running_count order ingredients canceled_count still_running_count
  local order_ids=()
  local fingerprints=()
  count="${FLURRY_ALERT_COUNT:-5}"
  suffix="$(generate_test_suffix)"
  recipe="dummy-parallel-slow-cancel-result"
  target_idx=$((count / 2))

  for idx in $(seq 0 $((count - 1))); do
    req_id="E2E-DUMMY-CANCEL-FLURRY-${suffix}-${idx}"
    fingerprint="dummy-cancel-flurry-${suffix}-${idx}"
    order_id="$(post_alert "${recipe}" "firing" "${req_id}" "${fingerprint}")"
    order_ids+=("${order_id}")
    fingerprints+=("${fingerprint}")
  done

  for order_id in "${order_ids[@]}"; do
    rows="$(wait_for_runtime_match "${order_id}" '.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched")')"
    running_count="$(echo "${rows}" | jq -r '[.[] | select(.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched"))] | length')"
    if [ "${running_count}" != "2" ]; then
      log_error "Expected two in-flight slow_result rows for flurry order ${order_id}; running_count=${running_count}"
      echo "${rows}" | jq . >&2
      exit 1
    fi
  done

  target_order_id="${order_ids[$target_idx]}"
  post_alert "${recipe}" "resolved" "E2E-DUMMY-CANCEL-FLURRY-${suffix}-${target_idx}-RESOLVED" "${fingerprints[$target_idx]}" >/dev/null
  order="$(wait_for_order_status "${target_order_id}" "complete")"
  ingredients="$(wait_for_runtime_match "${target_order_id}" '.service_exec == "slow_result" and .service_exec_status == "canceled"')"
  canceled_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and .service_exec_status == "canceled")] | length')"
  if [ "${canceled_count}" != "2" ]; then
    log_error "Expected target flurry order ${target_order_id} to cancel both slow_result rows; canceled_count=${canceled_count}"
    echo "${ingredients}" | jq . >&2
    exit 1
  fi
  assert_json "${order}" '.processing_status == "complete"' "target flurry order did not complete after cancellation"

  for idx in $(seq 0 $((count - 1))); do
    if [ "${idx}" -eq "${target_idx}" ]; then
      continue
    fi
    ingredients="$(collect_order_ingredients "${order_ids[$idx]}")"
    still_running_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and (.service_exec_status == "running" or .service_exec_status == "dispatched"))] | length')"
    canceled_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and .service_exec_status == "canceled")] | length')"
    if [ "${still_running_count}" != "2" ] || [ "${canceled_count}" != "0" ]; then
      log_error "Non-target flurry order ${order_ids[$idx]} was affected before its resolve; running=${still_running_count}, canceled=${canceled_count}"
      echo "${ingredients}" | jq . >&2
      exit 1
    fi
  done

  for idx in $(seq 0 $((count - 1))); do
    if [ "${idx}" -eq "${target_idx}" ]; then
      continue
    fi
    post_alert "${recipe}" "resolved" "E2E-DUMMY-CANCEL-FLURRY-${suffix}-${idx}-RESOLVED" "${fingerprints[$idx]}" >/dev/null
  done

  for idx in $(seq 0 $((count - 1))); do
    order="$(wait_for_order_status "${order_ids[$idx]}" "complete")"
    ingredients="$(wait_for_runtime_match "${order_ids[$idx]}" '.service_exec == "slow_result" and .service_exec_status == "canceled"')"
    canceled_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "slow_result" and .service_exec_status == "canceled")] | length')"
    if [ "${canceled_count}" != "2" ]; then
      log_error "Expected flurry order ${order_ids[$idx]} to finish with two canceled slow_result rows; canceled_count=${canceled_count}"
      echo "${ingredients}" | jq . >&2
      exit 1
    fi
    assert_json "${order}" '.processing_status == "complete"' "flurry order did not complete"
  done

  log_info "PASS cancel-during-flurry order_count=${count} target_order_id=${target_order_id} order_ids=${order_ids[*]}"
}

run_multi_group_blocking() {
  local suffix req_id fingerprint order_id order ingredients depth1_count depth2_count depth3_count total_count
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-MULTI-GROUP-BLOCKING-${suffix}"
  fingerprint="dummy-multi-group-blocking-${suffix}"
  order_id="$(post_alert "dummy-multi-group-blocking-result" "firing" "${req_id}" "${fingerprint}")"

  wait_for_runtime_match "${order_id}" '.depth == 1 and .service_exec == "positive_result"' >/dev/null
  order="$(wait_for_order_status "${order_id}" "complete")"
  ingredients="$(collect_order_ingredients "${order_id}")"

  total_count="$(echo "${ingredients}" | jq -r '[.[] | select(.service_exec == "positive_result")] | length')"
  depth1_count="$(echo "${ingredients}" | jq -r '[.[] | select(.depth == 1 and .service_exec_status == "succeeded")] | length')"
  depth2_count="$(echo "${ingredients}" | jq -r '[.[] | select(.depth == 2 and .service_exec_status == "succeeded")] | length')"
  depth3_count="$(echo "${ingredients}" | jq -r '[.[] | select(.depth == 3 and .service_exec_status == "succeeded")] | length')"
  if [ "${total_count}" != "5" ] || [ "${depth1_count}" != "2" ] || [ "${depth2_count}" != "1" ] || [ "${depth3_count}" != "2" ]; then
    log_error "Expected multi-group blocking order to complete 2/1/2 rows; total=${total_count}, depth1=${depth1_count}, depth2=${depth2_count}, depth3=${depth3_count}"
    echo "${ingredients}" | jq . >&2
    exit 1
  fi
  assert_json "${order}" '.processing_status == "complete"' "multi-group blocking order did not complete"
  log_info "PASS multi-group-blocking order_id=${order_id}"
}

run_multi_group_preblock_failure() {
  local suffix req_id fingerprint order_id order ingredients failed_count succeeded_count canceled_future_count active_future_count
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-MULTI-GROUP-PREBLOCK-FAILURE-${suffix}"
  fingerprint="dummy-multi-group-preblock-failure-${suffix}"
  order_id="$(post_alert "dummy-multi-group-preblock-failure-result" "firing" "${req_id}" "${fingerprint}")"

  order="$(wait_for_order_status "${order_id}" "failed")"
  ingredients="$(collect_order_ingredients "${order_id}")"
  failed_count="$(echo "${ingredients}" | jq -r '[.[] | select(.depth == 1 and .service_exec == "negative_result" and .service_exec_status == "failed")] | length')"
  succeeded_count="$(echo "${ingredients}" | jq -r '[.[] | select(.depth == 1 and .service_exec == "positive_result" and .service_exec_status == "succeeded")] | length')"
  canceled_future_count="$(echo "${ingredients}" | jq -r '[.[] | select((.depth == 2 or .depth == 3) and .service_exec_status == "canceled")] | length')"
  active_future_count="$(echo "${ingredients}" | jq -r '[.[] | select((.depth == 2 or .depth == 3) and (.service_exec_status == "pending" or .service_exec_status == "dispatched" or .service_exec_status == "running"))] | length')"
  if [ "${failed_count}" != "1" ] || [ "${succeeded_count}" != "1" ] || [ "${canceled_future_count}" != "3" ] || [ "${active_future_count}" != "0" ]; then
    log_error "Expected pre-block failure to fail first group and cancel future rows; failed=${failed_count}, succeeded=${succeeded_count}, canceled_future=${canceled_future_count}, active_future=${active_future_count}"
    echo "${ingredients}" | jq . >&2
    exit 1
  fi
  assert_json "${order}" '.processing_status == "failed"' "multi-group preblock failure order did not fail"
  log_info "PASS multi-group-preblock-failure order_id=${order_id}"
}

run_suppressed() {
  local suffix req_id fingerprint instance suppression_payload suppression suppression_id order_id order dishes ingredients activity dish_count runtime_count activity_count
  suffix="$(generate_test_suffix)"
  req_id="E2E-DUMMY-SUPPRESSED-${suffix}"
  fingerprint="dummy-suppressed-${suffix}"
  instance="suppressed-${suffix}:9090"
  suppression_payload="$(
    jq -n \
      --arg name "e2e-suppression-${suffix}" \
      --arg reason "dummy e2e cook suppression gate" \
      --arg instance "${instance}" \
      --arg starts_at "2000-01-01T00:00:00Z" \
      --arg ends_at "2999-01-01T00:00:00Z" \
      '{
        name: $name,
        starts_at: $starts_at,
        ends_at: $ends_at,
        reason: $reason,
        created_by: "tests/run_e2e.sh",
        summary_ticket_enabled: false,
        matchers: [
          {label_key: "instance", operator: "eq", value: $instance}
        ]
      }'
  )"
  suppression="$(api_request_json POST "/suppressions" "${suppression_payload}")"
  assert_json "${suppression}" '.status == "active"' "suppression was not active after creation"
  suppression_id="$(echo "${suppression}" | jq -r '.id')"

  order_id="$(post_alert "dummy-positive-result" "firing" "${req_id}" "${fingerprint}" "${instance}")"
  order="$(wait_for_order_status "${order_id}" "complete")"
  local order_req_id
  order_req_id="$(echo "${order}" | jq -r '.req_id')"
  dishes="$(collect_order_dishes "${order_id}")"
   ingredients="$(collect_order_ingredients "${order_id}")"
   sleep 2
   activity="$(${CAKECTL} -u "${API_URL%/api/v1}" -f json activity suppressed "${suppression_id}" 2>/dev/null)"

  dish_count="$(echo "${dishes}" | jq -r 'length')"
  runtime_count="$(echo "${ingredients}" | jq -r 'length')"
  activity_count="$(echo "${activity}" | jq -r --arg req_id "${order_req_id}" '[.[] | select(.req_id == $req_id)] | length')"
  if [ "${dish_count}" != "1" ] || [ "${runtime_count}" != "0" ] || [ "${activity_count}" != "1" ]; then
    log_error "Expected suppressed order to complete with one dish, no visible runtime rows, and one suppression activity row; dish_count=${dish_count}, runtime_count=${runtime_count}, activity_count=${activity_count}"
    echo "${order}" | jq . >&2
    echo "${dishes}" | jq . >&2
    echo "${ingredients}" | jq . >&2
    echo "${activity}" | jq . >&2
    exit 1
  fi
  assert_json "${order}" '.processing_status == "complete" and .remediation_outcome == "none"' \
    "suppressed order did not complete with remediation_outcome=none"
  assert_json "${dishes}" '.[0].processing_status == "complete" and .[0].dish_exec_status == "succeeded"' \
    "suppressed dish did not complete cleanly"
  log_info "PASS suppressed order_id=${order_id}"
}

run_case() {
  case "$1" in
    positive) run_positive ;;
    negative) run_negative ;;
    expected-negative) run_expected_negative ;;
    cancel-parallel) run_cancel_parallel ;;
    rapid-parallel) run_rapid_parallel ;;
    cancel-during-flurry) run_cancel_during_flurry ;;
    multi-group-blocking) run_multi_group_blocking ;;
    multi-group-preblock-failure) run_multi_group_preblock_failure ;;
    suppressed) run_suppressed ;;
    *)
      log_error "Unknown e2e case: $1"
      list_cases >&2
      exit 1
      ;;
  esac
}

preflight

if [ -n "${single_case}" ]; then
  run_case "${single_case}"
else
  for case_name in positive negative expected-negative cancel-parallel rapid-parallel cancel-during-flurry multi-group-blocking multi-group-preblock-failure suppressed; do
    run_case "${case_name}"
  done
fi

#!/usr/bin/env bash
# Devstack negative security abuse runner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

: "${API_ROOT_URL:=http://127.0.0.1:8000}"
: "${API_URL:=${API_ROOT_URL}/api/v1}"
: "${DB_ROOT_PASSWORD:=rootpassword}"
: "${DB_NAME:=poundcake}"
: "${POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY:=poundcake-dev-service-identity-key}"
: "${WEBHOOK_BEARER_TOKEN:=}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

usage() {
  cat <<'EOF'
Usage:
  ./tests/run_security_abuse_e2e.sh [--api-url <url>] [--api-root-url <url>]
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --api-url)
      API_URL="${2:-}"
      shift 2
      ;;
    --api-root-url)
      API_ROOT_URL="${2:-}"
      shift 2
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

export API_URL API_ROOT_URL WEBHOOK_BEARER_TOKEN

raw_request() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  shift 3 || true
  local url="${API_URL}${path}"
  local args=("-sS" "-X" "${method}" "${url}" "-w" $'\n%{http_code}')

  if [ -n "${data}" ]; then
    args+=("-H" "Content-Type: application/json" "--data" "${data}")
  fi
  while [ $# -gt 0 ]; do
    args+=("-H" "$1")
    shift
  done

  curl "${args[@]}"
}

assert_http_status() {
  local response="$1"
  local expected="$2"
  local label="$3"
  local code="${response##*$'\n'}"
  local body="${response%$'\n'*}"
  if [ "${code}" != "${expected}" ]; then
    log_error "${label} expected HTTP ${expected}, got ${code}"
    echo "${body}" >&2
    exit 1
  fi
  log_info "PASS ${label} -> HTTP ${code}"
}

assert_body_not_leaky() {
  local response="$1"
  local label="$2"
  local body="${response%$'\n'*}"
  if echo "${body}" | grep -Eiq '(authorization["=:]|password["=:]|secret["=:]|token["=:])'; then
    log_error "${label} response leaked secret-like material"
    echo "${body}" >&2
    exit 1
  fi
}

configure_webhook_token() {
  local payload
  payload="$(jq -n \
    --arg token "${WEBHOOK_BEARER_TOKEN}" \
    '{config: {webhook_bearer_token: $token}}')"
  api_request_json PUT "/plugins/alertmanager/configuration" "${payload}" >/dev/null
}

fetch_internal_hmac_material() {
  local service_type="$1"
  local row key_id encrypted secret
  row="$(
    docker exec poundcake-mariadb mariadb -N -B \
      -uroot "-p${DB_ROOT_PASSWORD}" "${DB_NAME}" \
      -e "SELECT sic.credential_key_id, sic.encrypted_payload
          FROM service_identity_credentials sic
          JOIN service_plugins sp ON sp.id = sic.service_plugin_id
          WHERE sp.service_type = '${service_type}'
            AND sic.credential_type = 'internal_control_plane_hmac'
          LIMIT 1;"
  )"
  key_id="${row%%$'\t'*}"
  encrypted="${row#*$'\t'}"
  if [ -z "${key_id}" ] || [ "${encrypted}" = "${row}" ]; then
    log_error "Could not load internal HMAC material for ${service_type}"
    exit 1
  fi
  secret="$(
    POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY="${POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY}" \
      .venv/bin/python -c '
from api.services.credentials import decrypt_service_identity_payload
import os, sys
payload = decrypt_service_identity_payload(sys.argv[1])
print(str(payload["hmac_secret"]))
' "${encrypted}"
  )"
  printf '%s\t%s\n' "${key_id}" "${secret}"
}

signed_request() {
  local service_type="$1"
  local method="$2"
  local path="$3"
  local body="${4:-}"
  local nonce="${5:-}"
  local override_path="${6:-}"
  local material key_id secret
  material="$(fetch_internal_hmac_material "${service_type}")"
  key_id="${material%%$'\t'*}"
  secret="${material#*$'\t'}"
  INTERNAL_SERVICE_TYPE="${service_type}" \
  INTERNAL_METHOD="${method}" \
  INTERNAL_PATH="${path}" \
  INTERNAL_SIGN_PATH="${override_path:-$path}" \
  INTERNAL_BODY="${body}" \
  INTERNAL_KEY_ID="${key_id}" \
  INTERNAL_SECRET="${secret}" \
  INTERNAL_NONCE="${nonce}" \
  INTERNAL_API_ROOT="${API_ROOT_URL}" \
    .venv/bin/python -c '
import json
import os
import sys
import urllib.request
import urllib.error
from shared.internal_hmac import build_internal_hmac_headers

method = os.environ["INTERNAL_METHOD"]
path = os.environ["INTERNAL_PATH"]
sign_path = os.environ["INTERNAL_SIGN_PATH"]
body_text = os.environ.get("INTERNAL_BODY", "")
nonce = os.environ.get("INTERNAL_NONCE", "")
body = body_text.encode("utf-8")
headers = build_internal_hmac_headers(
    key_id=os.environ["INTERNAL_KEY_ID"],
    secret=os.environ["INTERNAL_SECRET"],
    method=method,
    url_or_path=sign_path,
    body=body,
    nonce=(nonce or None),
)
request = urllib.request.Request(
    os.environ["INTERNAL_API_ROOT"].rstrip("/") + path,
    data=(body if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} else None),
    headers=headers,
    method=method,
)
if body:
    request.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(request) as response:
        payload = response.read().decode("utf-8", "replace")
        sys.stdout.write(payload + "\n" + str(response.status))
except urllib.error.HTTPError as exc:
    payload = exc.read().decode("utf-8", "replace")
    sys.stdout.write(payload + "\n" + str(exc.code))
'
}

preflight() {
  require_cmd curl
  require_cmd jq
  require_cmd docker
  wait_for_api_ready
  authenticate_api_if_required
  configure_webhook_token
  log_info "Live devstack exposes anonymous traffic and local superuser auth; reader/operator human boundary abuse stays in pytest coverage."
}

run_human_abuse_cases() {
  local response

  # Anonymous GET via cakectl with --no-session still returns JSON with HTTP status
  response="$(cakectl -u "${API_URL%/api/v1}" api get /recipes/status \
    --no-session --format json 2>/dev/null)" || response="$(raw_request GET "/recipes/status")"
  assert_http_status "${response}" "401" "anonymous reader route"

  response="$(cakectl -u "${API_URL%/api/v1}" api get /recipes/ \
    --no-session --format json 2>/dev/null)" || response="$(raw_request GET "/recipes/")"
  assert_http_status "${response}" "401" "anonymous operator route"

  response="$(raw_request GET "/recipes/status" "" "Authorization: Bearer ${WEBHOOK_BEARER_TOKEN}")"
  assert_http_status "${response}" "401" "webhook bearer on reader route"
}

run_internal_abuse_cases() {
  local response nonce

  response="$(raw_request POST "/cook/orders/1" "{}" "Authorization: Bearer not-a-real-service-token" "Content-Type: application/json")"
  assert_http_status "${response}" "401" "generic bearer on internal route"

  response="$(signed_request "timer" "POST" "/api/v1/cook/orders/1" "{}" "security-cross-service-1")"
  assert_http_status "${response}" "403" "timer HMAC on prep-chef route"
  assert_body_not_leaky "${response}" "timer cross-service denial"

  response="$(signed_request "timer" "GET" "/api/v1/auth/bindings")"
  assert_http_status "${response}" "403" "timer HMAC on admin route"
  assert_body_not_leaky "${response}" "timer admin denial"

  nonce="security-replay-$(generate_test_suffix)"
  response="$(signed_request "prep-chef" "POST" "/api/v1/cook/orders/999999999" "{}" "${nonce}")"
  assert_http_status "${response}" "404" "prep-chef first signed request"

  response="$(signed_request "prep-chef" "POST" "/api/v1/cook/orders/999999999" "{}" "${nonce}")"
  assert_http_status "${response}" "401" "prep-chef replayed nonce"

  response="$(signed_request "prep-chef" "POST" "/api/v1/cook/orders/999999999" "{}" "security-tamper-$(generate_test_suffix)" "/api/v1/cook/orders/1")"
  assert_http_status "${response}" "401" "tampered signed path"
}

main() {
  preflight
  run_human_abuse_cases
  run_internal_abuse_cases
  log_info "Security abuse harness completed with zero unexpected successes"
}

main "$@"

#!/bin/sh
# PoundCake UI entrypoint script.
# Renders nginx config with API_URL and refuses privileged listen ports.

set -eu

TEMPLATE_PATH="${NGINX_TEMPLATE_PATH:-/etc/nginx/templates/default.conf.template}"
OUTPUT_PATH="${NGINX_OUTPUT_PATH:-/etc/nginx/conf.d/default.conf}"
API_URL="${API_URL:-http://poundcake:8080}"
UI_NGINX_ACCESS_LOG="${UI_NGINX_ACCESS_LOG:-off}"
NGINX_RESOLVER="${NGINX_RESOLVER:-$(awk '/^nameserver[[:space:]]+/ { print $2; exit }' /etc/resolv.conf)}"
NGINX_RESOLVER="${NGINX_RESOLVER:-127.0.0.11}"
export API_URL UI_NGINX_ACCESS_LOG NGINX_RESOLVER

echo "[ui-entrypoint] Rendering ${TEMPLATE_PATH} -> ${OUTPUT_PATH}"
envsubst '${API_URL} ${UI_NGINX_ACCESS_LOG} ${NGINX_RESOLVER}' < "${TEMPLATE_PATH}" > "${OUTPUT_PATH}"

privileged_ports="$(
  awk '
    /^[[:space:]]*#/ { next }
    {
      line=$0
      sub(/#.*/, "", line)
      if (match(line, /^[[:space:]]*listen[[:space:]]+([^;]+)/, m)) {
        endpoint=m[1]
        split(endpoint, fields, /[[:space:]]+/)
        addr=fields[1]
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", addr)

        if (addr ~ /^\[[^]]+\]:[0-9]+$/) {
          sub(/.*]:/, "", addr)
        } else if (addr ~ /^.+:[0-9]+$/) {
          sub(/.*:/, "", addr)
        }

        if (addr ~ /^[0-9]+$/ && addr < 1024) {
          print addr
        }
      }
    }
  ' "${OUTPUT_PATH}" | sort -u | tr '\n' ' '
)"

if [ -n "${privileged_ports}" ]; then
  echo "[ui-entrypoint] ERROR: Privileged nginx listen ports detected (${privileged_ports}). Non-root containers must use ports >= 1024."
  echo "[ui-entrypoint] Rendered config excerpt:"
  grep -n "listen" "${OUTPUT_PATH}" || true
  exit 1
fi

echo "[ui-entrypoint] Validating nginx configuration"
nginx -t

echo "[ui-entrypoint] Starting nginx (API_URL=${API_URL}, UI_NGINX_ACCESS_LOG=${UI_NGINX_ACCESS_LOG}, NGINX_RESOLVER=${NGINX_RESOLVER})"
exec "$@"

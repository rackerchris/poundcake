#!/usr/bin/env bash
# Build and start the local PoundCake dummy-plugin development environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000/api/v1}"
API_ROOT_URL="${API_ROOT_URL:-${API_BASE_URL%/api/v1}}"
UI_URL="${UI_URL:-http://127.0.0.1:8080}"
WAIT_SECONDS="${WAIT_SECONDS:-180}"
AUTH_USERNAME="${POUNDCAKE_AUTH_DEV_USERNAME:-admin}"
AUTH_PASSWORD="${POUNDCAKE_AUTH_DEV_PASSWORD:-poundcake-dev}"

cd "$PROJECT_ROOT"

log() {
    printf '[docker-devstack-create] %s\n' "$*"
}

fail() {
    printf '[docker-devstack-create] ERROR: %s\n' "$*" >&2
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

detect_docker() {
    detect_executable \
        DOCKER_BIN \
        docker \
        /opt/homebrew/bin/docker \
        /usr/local/bin/docker \
        /usr/bin/docker
}

detect_docker_compose() {
    detect_executable \
        DOCKER_COMPOSE_BIN \
        docker-compose \
        /opt/homebrew/bin/docker-compose \
        /usr/local/bin/docker-compose \
        /usr/bin/docker-compose
}

detect_curl() {
    detect_executable \
        CURL_BIN \
        curl \
        /opt/homebrew/bin/curl \
        /usr/local/bin/curl \
        /usr/bin/curl
}

detect_compose() {
    if [ -n "${COMPOSE:-}" ]; then
        read -r -a COMPOSE_CMD <<< "$COMPOSE"
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            return 0
        fi
        fail "COMPOSE is set but not available: $COMPOSE"
    fi

    local docker_bin
    if docker_bin="$(detect_docker 2>/dev/null)"; then
        COMPOSE_CMD=("$docker_bin" compose)
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            return 0
        fi
    fi

    local docker_compose_bin
    if docker_compose_bin="$(detect_docker_compose 2>/dev/null)"; then
        COMPOSE_CMD=("$docker_compose_bin")
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            return 0
        fi
    fi

    fail "Docker Compose is not available. Start Colima with '--runtime docker' and install docker-compose if needed."
}

wait_for_url() {
    local name="$1"
    local url="$2"
    local deadline=$((SECONDS + WAIT_SECONDS))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if "$CURL_BIN" -fsS "$url" >/dev/null 2>&1; then
            log "$name is ready"
            return 0
        fi
        sleep 2
    done
    fail "timed out waiting for $name at $url"
}

login_api() {
    local cookie_jar="$1"
    local payload
    payload="$(printf '{"provider":"local","username":"%s","password":"%s"}' "$AUTH_USERNAME" "$AUTH_PASSWORD")"
    "$CURL_BIN" -fsS \
        -c "$cookie_jar" \
        -H "Content-Type: application/json" \
        --data "$payload" \
        "$API_BASE_URL/auth/login" >/dev/null
}

CURL_BIN="$(detect_curl)"
COMPOSE_CMD=()
detect_compose

mkdir -p logs
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

log "building and starting local PoundCake plugin dev stack with: ${COMPOSE_CMD[*]}"
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" up --build -d

wait_for_url "API live probe" "$API_ROOT_URL/livez"
wait_for_url "API ready probe" "$API_ROOT_URL/readyz"
wait_for_url "UI" "$UI_URL"
login_api "$COOKIE_JAR"

log "compose status"
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" ps

log "plugin health"
"$CURL_BIN" -fsS -b "$COOKIE_JAR" "$API_BASE_URL/plugins/dummy/health"
printf '\n'

log "service registry"
"$CURL_BIN" -fsS -b "$COOKIE_JAR" "$API_BASE_URL/service-registry/ingredients"
printf '\n'

log "recipes"
"$CURL_BIN" -fsS -b "$COOKIE_JAR" "$API_BASE_URL/recipes/"
printf '\n'

log "development environment is ready"
log "UI: $UI_URL"
log "API: $API_BASE_URL"
log "Dev login: $AUTH_USERNAME / $AUTH_PASSWORD"

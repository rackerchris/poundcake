#!/usr/bin/env bash
# Tear down the local PoundCake dummy-plugin development environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
REMOVE_VOLUMES="${REMOVE_VOLUMES:-true}"
REMOVE_BOOTSTRAP_MARKER="${REMOVE_BOOTSTRAP_MARKER:-true}"

cd "$PROJECT_ROOT"

log() {
    printf '[docker-devstack-destroy] %s\n' "$*"
}

fail() {
    printf '[docker-devstack-destroy] ERROR: %s\n' "$*" >&2
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

set_container_cmd_for_compose() {
    local compose_bin
    compose_bin="$(basename "${COMPOSE_CMD[0]}")"

    case "$compose_bin" in
        docker)
            CONTAINER_CMD=("${COMPOSE_CMD[0]}")
            ;;
        docker-compose)
            CONTAINER_CMD=("$(detect_docker)")
            ;;
        *)
            CONTAINER_CMD=("$(detect_docker)")
            ;;
    esac
}

detect_compose() {
    if [ -n "${COMPOSE:-}" ]; then
        read -r -a COMPOSE_CMD <<< "$COMPOSE"
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            set_container_cmd_for_compose
            return 0
        fi
        fail "COMPOSE is set but not available: $COMPOSE"
    fi

    local docker_bin
    if docker_bin="$(detect_docker 2>/dev/null)"; then
        COMPOSE_CMD=("$docker_bin" compose)
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            CONTAINER_CMD=("$docker_bin")
            return 0
        fi
    fi

    local docker_compose_bin
    if docker_compose_bin="$(detect_docker_compose 2>/dev/null)"; then
        COMPOSE_CMD=("$docker_compose_bin")
        if "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" version >/dev/null 2>&1; then
            CONTAINER_CMD=("$(detect_docker)")
            return 0
        fi
    fi

    fail "Docker Compose is not available. Start Colima with '--runtime docker' and install docker-compose if needed."
}

remove_known_dev_containers() {
    local container
    for container in \
        poundcake-timer \
        poundcake-expediter-runner \
        poundcake-dishwasher \
        poundcake-prep-chef \
        poundcake-ui \
        poundcake-api \
        poundcake-bootstrap \
        poundcake-mariadb
    do
        if "${CONTAINER_CMD[@]}" container inspect "$container" >/dev/null 2>&1; then
            "${CONTAINER_CMD[@]}" rm -f "$container" >/dev/null
            log "removed container $container"
        fi
    done
}

COMPOSE_CMD=()
CONTAINER_CMD=()
detect_compose

if [ "$REMOVE_BOOTSTRAP_MARKER" = "true" ]; then
    rm -f config/poundcake_bootstrap_ready
    log "removed local bootstrap marker"
fi

if [ "$REMOVE_VOLUMES" = "true" ]; then
    log "tearing down local PoundCake stack and removing volumes"
    if ! "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" down --volumes --remove-orphans; then
        log "compose down failed; removing known local dev containers directly"
        remove_known_dev_containers
    fi
    remove_known_dev_containers

    # Compose providers can leave the named MariaDB volume behind on macOS even
    # after reporting a removed volume id. Remove the known local dev volume
    # explicitly so every create starts from a real greenfield database.
    for volume in docker_mariadb_data; do
        if "${CONTAINER_CMD[@]}" volume inspect "$volume" >/dev/null 2>&1; then
            "${CONTAINER_CMD[@]}" volume rm "$volume" >/dev/null
            log "removed volume $volume"
        fi
    done
else
    log "tearing down local PoundCake stack without removing volumes"
    if ! "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" down --remove-orphans; then
        log "compose down failed; removing known local dev containers directly"
        remove_known_dev_containers
    fi
fi

log "teardown complete"

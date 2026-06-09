#!/usr/bin/env bash
# Install or refresh metrics-server in the local kind devstack.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_FILE="${METRICS_SERVER_MANIFEST_FILE:-$SCRIPT_DIR/manifests/metrics-server.yaml}"
WAIT="${WAIT:-true}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-5m}"

log() {
    printf '[helm-devstack-metrics-server] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-metrics-server] ERROR: %s\n' "$*" >&2
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

[ -f "$MANIFEST_FILE" ] || fail "metrics-server manifest not found: $MANIFEST_FILE"

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

log "applying metrics-server manifest from $MANIFEST_FILE"
"$KUBECTL_BIN" apply -f "$MANIFEST_FILE"

if [ "$WAIT" = "true" ]; then
    log "waiting for metrics-server deployment rollout"
    "$KUBECTL_BIN" -n kube-system rollout status deployment/metrics-server --timeout "$WAIT_TIMEOUT"
else
    log "WAIT=false; skipping metrics-server rollout wait"
fi

log "metrics-server is configured"

#!/usr/bin/env bash
# Install the external StackStorm Helm release for kind/devstack.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
STACKSTORM_RELEASE_NAME="${STACKSTORM_RELEASE_NAME:-stackstorm}"
STACKSTORM_CHART_SOURCE="${STACKSTORM_CHART_SOURCE:-https://github.com/rackerlabs/poundcake-stackstorm.git}"
STACKSTORM_CHART_REF="${STACKSTORM_CHART_REF:-main}"
STACKSTORM_CHART_PATH="${STACKSTORM_CHART_PATH:-helm}"
STACKSTORM_VALUES_FILE="${STACKSTORM_VALUES_FILE:-}"
STACKSTORM_CLIENT_ENABLED="${STACKSTORM_CLIENT_ENABLED:-true}"
STACKSTORM_WEB_ENABLED="${STACKSTORM_WEB_ENABLED:-false}"
WAIT="${WAIT:-true}"
WAIT_TIMEOUT="${STACKSTORM_WAIT_TIMEOUT:-15m}"

log() {
    printf '[helm-devstack-stackstorm] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-stackstorm] ERROR: %s\n' "$*" >&2
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

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"
HELM_BIN="$(detect_executable HELM_BIN helm /opt/homebrew/bin/helm /usr/local/bin/helm)"
GIT_BIN="$(detect_executable GIT_BIN git /opt/homebrew/bin/git /usr/local/bin/git)"

tmp_dir=""
cleanup() {
    if [ -n "$tmp_dir" ] && [ -d "$tmp_dir" ]; then
        rm -rf "$tmp_dir"
    fi
}
trap cleanup EXIT

chart_dir="$STACKSTORM_CHART_SOURCE"
case "$STACKSTORM_CHART_SOURCE" in
    github.com/*)
        STACKSTORM_CHART_SOURCE="https://${STACKSTORM_CHART_SOURCE}.git"
        tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/poundcake-stackstorm-chart.XXXXXX")"
        log "cloning StackStorm chart from $STACKSTORM_CHART_SOURCE ref $STACKSTORM_CHART_REF"
        "$GIT_BIN" clone --depth 1 --branch "$STACKSTORM_CHART_REF" "$STACKSTORM_CHART_SOURCE" "$tmp_dir"
        chart_dir="$tmp_dir/$STACKSTORM_CHART_PATH"
        ;;
    http://*|https://*|git@*|*.git)
        tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/poundcake-stackstorm-chart.XXXXXX")"
        log "cloning StackStorm chart from $STACKSTORM_CHART_SOURCE ref $STACKSTORM_CHART_REF"
        "$GIT_BIN" clone --depth 1 --branch "$STACKSTORM_CHART_REF" "$STACKSTORM_CHART_SOURCE" "$tmp_dir"
        chart_dir="$tmp_dir/$STACKSTORM_CHART_PATH"
        ;;
esac

[ -f "$chart_dir/Chart.yaml" ] || fail "StackStorm chart not found at $chart_dir"
if [ -z "$STACKSTORM_VALUES_FILE" ] && [ -f "$chart_dir/devstack/values/stackstorm-kind.yaml" ]; then
    STACKSTORM_VALUES_FILE="$chart_dir/devstack/values/stackstorm-kind.yaml"
fi
if [ -n "$STACKSTORM_VALUES_FILE" ]; then
    [ -f "$STACKSTORM_VALUES_FILE" ] || fail "STACKSTORM_VALUES_FILE does not exist: $STACKSTORM_VALUES_FILE"
fi

if [ -f "$chart_dir/Chart.lock" ] || grep -q '^dependencies:' "$chart_dir/Chart.yaml"; then
    log "building StackStorm chart dependencies"
    "$HELM_BIN" dependency build "$chart_dir"
fi

"$KUBECTL_BIN" create namespace "$STACKSTORM_NAMESPACE" --dry-run=client -o yaml | "$KUBECTL_BIN" apply -f -

helm_wait_args=()
if [ "$WAIT" = "true" ]; then
    helm_wait_args+=(--wait --timeout "$WAIT_TIMEOUT")
fi

values_args=()
if [ -n "$STACKSTORM_VALUES_FILE" ]; then
    values_args+=(-f "$STACKSTORM_VALUES_FILE")
fi
if [ "$STACKSTORM_CLIENT_ENABLED" = "true" ]; then
    values_args+=(--set stackstormServices.client.enabled=true)
fi
if [ "$STACKSTORM_WEB_ENABLED" != "true" ]; then
    values_args+=(--set stackstormServices.web.enabled=false)
fi

log "installing StackStorm release $STACKSTORM_RELEASE_NAME in namespace $STACKSTORM_NAMESPACE"
"$HELM_BIN" upgrade --install "$STACKSTORM_RELEASE_NAME" "$chart_dir" \
    --namespace "$STACKSTORM_NAMESPACE" \
    "${values_args[@]}" \
    "${helm_wait_args[@]}"

log "StackStorm is ready"
log "api: http://stackstorm-api.$STACKSTORM_NAMESPACE.svc.cluster.local:9101"

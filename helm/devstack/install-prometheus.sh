#!/usr/bin/env bash
# Install the Prometheus Operator stack for the Helm devstack.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
PROMETHEUS_RELEASE_NAME="${PROMETHEUS_RELEASE_NAME:-poundcake-prometheus}"
PROMETHEUS_CRDS_RELEASE_NAME="${PROMETHEUS_CRDS_RELEASE_NAME:-poundcake-prometheus-operator-crds}"
PROMETHEUS_REPO_NAME="${PROMETHEUS_REPO_NAME:-prometheus-community}"
PROMETHEUS_REPO_URL="${PROMETHEUS_REPO_URL:-https://prometheus-community.github.io/helm-charts}"
PROMETHEUS_CRDS_CHART="${PROMETHEUS_CRDS_CHART:-prometheus-community/prometheus-operator-crds}"
PROMETHEUS_CRDS_CHART_VERSION="${PROMETHEUS_CRDS_CHART_VERSION:-28.0.1}"
PROMETHEUS_CHART="${PROMETHEUS_CHART:-prometheus-community/kube-prometheus-stack}"
PROMETHEUS_CHART_VERSION="${PROMETHEUS_CHART_VERSION:-84.5.0}"
PROMETHEUS_VALUES_FILE="${PROMETHEUS_VALUES_FILE:-$SCRIPT_DIR/values/prometheus-kind.yaml}"
POUNDCAKE_WEBHOOK_SECRET_NAME="${POUNDCAKE_WEBHOOK_SECRET_NAME:-poundcake-alertmanager-webhook}"
POUNDCAKE_WEBHOOK_TOKEN_KEY="${POUNDCAKE_WEBHOOK_TOKEN_KEY:-webhook-bearer-token}"
WAIT="${WAIT:-true}"
WAIT_TIMEOUT="${MONITORING_WAIT_TIMEOUT:-10m}"

log() {
    printf '[helm-devstack-prometheus] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-prometheus] ERROR: %s\n' "$*" >&2
    exit 1
}

helm_release_status() {
    local release_name="$1"
    local namespace="$2"

    "$HELM_BIN" status "$release_name" -n "$namespace" 2>/dev/null \
        | awk '/^STATUS:/ {print $2; exit}'
}

recover_non_deployed_release() {
    local release_name="$1"
    local namespace="$2"
    local status

    status="$(helm_release_status "$release_name" "$namespace" || true)"
    case "$status" in
        "")
            return 0
            ;;
        deployed)
            return 0
            ;;
    esac

    log "found Helm release $release_name in status $status; deleting stale Helm release metadata"
    "$KUBECTL_BIN" delete secret -n "$namespace" \
        -l "owner=helm,name=$release_name" \
        --ignore-not-found
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

[ -f "$PROMETHEUS_VALUES_FILE" ] || fail "PROMETHEUS_VALUES_FILE does not exist: $PROMETHEUS_VALUES_FILE"

if ! "$HELM_BIN" repo list | awk 'NR > 1 {print $1}' | grep -Fxq "$PROMETHEUS_REPO_NAME"; then
    log "adding Helm repo $PROMETHEUS_REPO_NAME"
    "$HELM_BIN" repo add "$PROMETHEUS_REPO_NAME" "$PROMETHEUS_REPO_URL"
fi
log "updating Helm repo $PROMETHEUS_REPO_NAME"
"$HELM_BIN" repo update "$PROMETHEUS_REPO_NAME"

"$KUBECTL_BIN" create namespace "$MONITORING_NAMESPACE" --dry-run=client -o yaml | "$KUBECTL_BIN" apply -f -

token_file="$(mktemp "${TMPDIR:-/tmp}/poundcake-webhook-token.XXXXXX")"
chmod 600 "$token_file"
trap 'rm -f "$token_file"' EXIT

log "copying PoundCake webhook bearer token into namespace $MONITORING_NAMESPACE"
"$KUBECTL_BIN" get secret poundcake-secrets -n "$POUNDCAKE_NAMESPACE" \
    -o go-template='{{ index .data "WEBHOOK_BEARER_TOKEN" | base64decode }}' > "$token_file"
"$KUBECTL_BIN" create secret generic "$POUNDCAKE_WEBHOOK_SECRET_NAME" \
    -n "$MONITORING_NAMESPACE" \
    --from-file="$POUNDCAKE_WEBHOOK_TOKEN_KEY=$token_file" \
    --dry-run=client \
    -o yaml | "$KUBECTL_BIN" apply -f -

helm_wait_args=()
if [ "$WAIT" = "true" ]; then
    helm_wait_args+=(--wait --timeout "$WAIT_TIMEOUT")
fi

recover_non_deployed_release "$PROMETHEUS_CRDS_RELEASE_NAME" "$MONITORING_NAMESPACE"
log "installing Prometheus Operator CRDs release $PROMETHEUS_CRDS_RELEASE_NAME in namespace $MONITORING_NAMESPACE"
"$HELM_BIN" upgrade --install "$PROMETHEUS_CRDS_RELEASE_NAME" "$PROMETHEUS_CRDS_CHART" \
    --version "$PROMETHEUS_CRDS_CHART_VERSION" \
    --namespace "$MONITORING_NAMESPACE" \
    "${helm_wait_args[@]}"

recover_non_deployed_release "$PROMETHEUS_RELEASE_NAME" "$MONITORING_NAMESPACE"
log "installing Prometheus Operator stack release $PROMETHEUS_RELEASE_NAME in namespace $MONITORING_NAMESPACE"
"$HELM_BIN" upgrade --install "$PROMETHEUS_RELEASE_NAME" "$PROMETHEUS_CHART" \
    --version "$PROMETHEUS_CHART_VERSION" \
    --namespace "$MONITORING_NAMESPACE" \
    -f "$PROMETHEUS_VALUES_FILE" \
    --set-string "poundcakeWebhook.namespace=$POUNDCAKE_NAMESPACE" \
    --set-string "poundcakeWebhook.secretName=$POUNDCAKE_WEBHOOK_SECRET_NAME" \
    --set-string "poundcakeWebhook.tokenKey=$POUNDCAKE_WEBHOOK_TOKEN_KEY" \
    "${helm_wait_args[@]}"

log "prometheus operator stack is ready"
log "prometheus-operator-crds: $PROMETHEUS_CRDS_RELEASE_NAME"
log "prometheus: http://kube-prometheus-stack-prometheus.$MONITORING_NAMESPACE.svc.cluster.local:9090"
log "alertmanager: http://kube-prometheus-stack-alertmanager.$MONITORING_NAMESPACE.svc.cluster.local:9093"

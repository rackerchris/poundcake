#!/usr/bin/env bash
# Destroy the local PoundCake Helm/kind devstack.

set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-poundcake}"
POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
RELEASE_NAME="${RELEASE_NAME:-poundcake}"
UNINSTALL_RELEASE="${UNINSTALL_RELEASE:-true}"
UNINSTALL_MONITORING="${UNINSTALL_MONITORING:-true}"
UNINSTALL_STACKSTORM="${UNINSTALL_STACKSTORM:-true}"
PORT_FORWARD="${PORT_FORWARD:-true}"
DELETE_NAMESPACE="${DELETE_NAMESPACE:-true}"
DELETE_MONITORING_NAMESPACE="${DELETE_MONITORING_NAMESPACE:-true}"
DELETE_STACKSTORM_NAMESPACE="${DELETE_STACKSTORM_NAMESPACE:-true}"
DELETE_CLUSTER="${DELETE_CLUSTER:-false}"
WAIT="${WAIT:-true}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
PROMETHEUS_CRDS_RELEASE_NAME="${PROMETHEUS_CRDS_RELEASE_NAME:-poundcake-prometheus-operator-crds}"
PROMETHEUS_RELEASE_NAME="${PROMETHEUS_RELEASE_NAME:-poundcake-prometheus}"
ALERTMANAGER_RELEASE_NAME="${ALERTMANAGER_RELEASE_NAME:-poundcake-alertmanager}"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
STACKSTORM_RELEASE_NAME="${STACKSTORM_RELEASE_NAME:-stackstorm}"

log() {
    printf '[helm-devstack-destroy] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-destroy] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [FLAGS]

Remove one or more Helm devstack components.

Flags:
  --poundcake              Remove the PoundCake Helm release and namespace
  --monitoring             Remove the monitoring/observability releases and namespace
  --observability          Alias for --monitoring
  --stackstorm             Remove the StackStorm Helm release and namespace
  --kind-cluster           Remove the kind cluster
  --all                    Completely tear down the devstack, including the kind cluster
  -h, --help               Show this help text

When no component flags are provided, the script preserves the current default
behavior: uninstall PoundCake, monitoring, and StackStorm, delete their
namespaces, stop local port-forwards, and keep the kind cluster unless
DELETE_CLUSTER=true is set in the environment.
EOF
}

set_target_defaults() {
    UNINSTALL_RELEASE="false"
    UNINSTALL_MONITORING="false"
    UNINSTALL_STACKSTORM="false"
    DELETE_NAMESPACE="false"
    DELETE_MONITORING_NAMESPACE="false"
    DELETE_STACKSTORM_NAMESPACE="false"
    DELETE_CLUSTER="false"
}

enable_poundcake_target() {
    UNINSTALL_RELEASE="true"
    DELETE_NAMESPACE="true"
}

enable_monitoring_target() {
    UNINSTALL_MONITORING="true"
    DELETE_MONITORING_NAMESPACE="true"
}

enable_stackstorm_target() {
    UNINSTALL_STACKSTORM="true"
    DELETE_STACKSTORM_NAMESPACE="true"
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

target_flags_provided="false"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --poundcake)
            if [ "$target_flags_provided" = "false" ]; then
                set_target_defaults
                target_flags_provided="true"
            fi
            enable_poundcake_target
            ;;
        --monitoring|--observability)
            if [ "$target_flags_provided" = "false" ]; then
                set_target_defaults
                target_flags_provided="true"
            fi
            enable_monitoring_target
            ;;
        --stackstorm)
            if [ "$target_flags_provided" = "false" ]; then
                set_target_defaults
                target_flags_provided="true"
            fi
            enable_stackstorm_target
            ;;
        --kind-cluster)
            if [ "$target_flags_provided" = "false" ]; then
                set_target_defaults
                target_flags_provided="true"
            fi
            DELETE_CLUSTER="true"
            ;;
        --all)
            set_target_defaults
            target_flags_provided="true"
            enable_poundcake_target
            enable_monitoring_target
            enable_stackstorm_target
            DELETE_CLUSTER="true"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            fail "unknown argument: $1"
            ;;
    esac
    shift
done

KIND_BIN="$(detect_executable KIND_BIN kind /opt/homebrew/bin/kind /usr/local/bin/kind)"
KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"
HELM_BIN="$(detect_executable HELM_BIN helm /opt/homebrew/bin/helm /usr/local/bin/helm)"

cluster_exists=false
if "$KIND_BIN" get clusters | grep -Fxq "$KIND_CLUSTER_NAME"; then
    cluster_exists=true
    "$KUBECTL_BIN" config use-context "kind-$KIND_CLUSTER_NAME" >/dev/null
fi

if [ "$PORT_FORWARD" = "true" ]; then
    log "stopping local port-forwards"
    POUNDCAKE_NAMESPACE="$POUNDCAKE_NAMESPACE" "$(dirname "$0")/ui-port-forward.sh" stop
else
    log "PORT_FORWARD=false; leaving local port-forwards untouched"
fi

if [ "$cluster_exists" = true ] && [ "$UNINSTALL_RELEASE" = "true" ]; then
    if "$HELM_BIN" status "$RELEASE_NAME" --namespace "$POUNDCAKE_NAMESPACE" >/dev/null 2>&1; then
        helm_args=(uninstall "$RELEASE_NAME" --namespace "$POUNDCAKE_NAMESPACE")
        if [ "$WAIT" = "true" ]; then
            helm_args+=(--wait --timeout "$WAIT_TIMEOUT")
        fi
        log "uninstalling Helm release $RELEASE_NAME from namespace $POUNDCAKE_NAMESPACE"
        "$HELM_BIN" "${helm_args[@]}"
    else
        log "Helm release not found: $RELEASE_NAME"
    fi
fi

uninstall_release_if_present() {
    local release_name="$1"
    local namespace="$2"
    if "$HELM_BIN" status "$release_name" --namespace "$namespace" >/dev/null 2>&1; then
        local helm_args=(uninstall "$release_name" --namespace "$namespace")
        if [ "$WAIT" = "true" ]; then
            helm_args+=(--wait --timeout "$WAIT_TIMEOUT")
        fi
        log "uninstalling Helm release $release_name from namespace $namespace"
        "$HELM_BIN" "${helm_args[@]}"
    else
        log "Helm release not found: $release_name in namespace $namespace"
    fi
}

delete_namespace_if_present() {
    local namespace="$1"
    if "$KUBECTL_BIN" get namespace "$namespace" >/dev/null 2>&1; then
        log "deleting namespace $namespace"
        "$KUBECTL_BIN" delete namespace "$namespace" --ignore-not-found
    fi
}

if [ "$cluster_exists" = true ]; then
    if [ "$UNINSTALL_MONITORING" = "true" ]; then
        uninstall_release_if_present "$PROMETHEUS_RELEASE_NAME" "$MONITORING_NAMESPACE"
        uninstall_release_if_present "$ALERTMANAGER_RELEASE_NAME" "$MONITORING_NAMESPACE"
        uninstall_release_if_present "$PROMETHEUS_CRDS_RELEASE_NAME" "$MONITORING_NAMESPACE"
    else
        log "UNINSTALL_MONITORING=false; leaving monitoring Helm releases untouched"
    fi

    if [ "$UNINSTALL_STACKSTORM" = "true" ]; then
        uninstall_release_if_present "$STACKSTORM_RELEASE_NAME" "$STACKSTORM_NAMESPACE"
    else
        log "UNINSTALL_STACKSTORM=false; leaving StackStorm Helm release untouched"
    fi
fi

if [ "$cluster_exists" = true ] && [ "$DELETE_NAMESPACE" = "true" ]; then
    delete_namespace_if_present "$POUNDCAKE_NAMESPACE"
fi

if [ "$cluster_exists" = true ] && [ "$DELETE_MONITORING_NAMESPACE" = "true" ]; then
    delete_namespace_if_present "$MONITORING_NAMESPACE"
fi

if [ "$cluster_exists" = true ] && [ "$DELETE_STACKSTORM_NAMESPACE" = "true" ]; then
    delete_namespace_if_present "$STACKSTORM_NAMESPACE"
fi

if [ "$DELETE_CLUSTER" = "true" ]; then
    if [ "$cluster_exists" = true ]; then
        log "deleting kind cluster $KIND_CLUSTER_NAME"
        "$KIND_BIN" delete cluster --name "$KIND_CLUSTER_NAME"
    else
        log "kind cluster not found: $KIND_CLUSTER_NAME"
    fi
else
    log "leaving kind cluster $KIND_CLUSTER_NAME in place (set DELETE_CLUSTER=true to remove it)"
fi

log "Helm devstack teardown complete"

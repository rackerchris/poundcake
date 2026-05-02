#!/usr/bin/env bash
# Manage local kubectl port-forwards for Helm devstack work.

set -euo pipefail

NAMESPACE="${NAMESPACE:-poundcake}"
SERVICE_NAME="${SERVICE_NAME:-poundcake-ui}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
SERVICE_PORT="${SERVICE_PORT:-80}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
PROMETHEUS_SERVICE_NAME="${PROMETHEUS_SERVICE_NAME:-kube-prometheus-stack-prometheus}"
PROMETHEUS_LOCAL_PORT="${PROMETHEUS_LOCAL_PORT:-9090}"
PROMETHEUS_SERVICE_PORT="${PROMETHEUS_SERVICE_PORT:-9090}"
ALERTMANAGER_SERVICE_NAME="${ALERTMANAGER_SERVICE_NAME:-kube-prometheus-stack-alertmanager}"
ALERTMANAGER_LOCAL_PORT="${ALERTMANAGER_LOCAL_PORT:-9093}"
ALERTMANAGER_SERVICE_PORT="${ALERTMANAGER_SERVICE_PORT:-9093}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-helm-devstack}"
PID_FILE="${PID_FILE:-$STATE_DIR/ui-port-forward.pid}"
LOG_FILE="${LOG_FILE:-$STATE_DIR/ui-port-forward.log}"

log() {
    printf '[helm-devstack-ui-port-forward] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-ui-port-forward] ERROR: %s\n' "$*" >&2
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

is_running() {
    local pid_file="$1"
    local pid command
    [ -f "$pid_file" ] || return 1
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" >/dev/null 2>&1
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$command" in
        *kubectl*" port-forward "*)
            return 0
            ;;
    esac
    return 1
}

pid_file_for() {
    local name="$1"
    if [ "$name" = "ui" ]; then
        printf '%s\n' "$PID_FILE"
    else
        printf '%s/%s-port-forward.pid\n' "$STATE_DIR" "$name"
    fi
}

log_file_for() {
    local name="$1"
    if [ "$name" = "ui" ]; then
        printf '%s\n' "$LOG_FILE"
    else
        printf '%s/%s-port-forward.log\n' "$STATE_DIR" "$name"
    fi
}

start_one_forward() {
    local name="$1"
    local namespace="$2"
    local service_name="$3"
    local local_port="$4"
    local service_port="$5"
    local pid_file log_file
    pid_file="$(pid_file_for "$name")"
    log_file="$(log_file_for "$name")"

    if is_running "$pid_file"; then
        log "$name already running on http://127.0.0.1:$local_port (pid $(cat "$pid_file"))"
        return 0
    fi

    mkdir -p "$STATE_DIR"
    rm -f "$pid_file"
    : > "$log_file"

    log "starting $name port-forward svc/$service_name $local_port:$service_port in namespace $namespace"
    nohup "$KUBECTL_BIN" -n "$namespace" port-forward "svc/$service_name" "$local_port:$service_port" >"$log_file" 2>&1 &
    printf '%s\n' "$!" > "$pid_file"

    sleep 1
    if ! is_running "$pid_file"; then
        rm -f "$pid_file"
        log "$name port-forward exited early; log follows"
        sed -n '1,80p' "$log_file" >&2
        exit 1
    fi

    log "$name ready: http://127.0.0.1:$local_port"
    log "$name log: $log_file"
}

start_forward() {
    start_one_forward "ui" "$NAMESPACE" "$SERVICE_NAME" "$LOCAL_PORT" "$SERVICE_PORT"
    start_one_forward "prometheus" "$MONITORING_NAMESPACE" "$PROMETHEUS_SERVICE_NAME" "$PROMETHEUS_LOCAL_PORT" "$PROMETHEUS_SERVICE_PORT"
    start_one_forward "alertmanager" "$MONITORING_NAMESPACE" "$ALERTMANAGER_SERVICE_NAME" "$ALERTMANAGER_LOCAL_PORT" "$ALERTMANAGER_SERVICE_PORT"
}

stop_one_forward() {
    local name="$1"
    local pid_file
    pid_file="$(pid_file_for "$name")"

    if ! is_running "$pid_file"; then
        rm -f "$pid_file"
        log "$name not running"
        return 0
    fi

    local pid
    pid="$(cat "$pid_file")"
    log "stopping $name pid $pid"
    kill "$pid"

    for _ in $(seq 1 20); do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
            rm -f "$pid_file"
            log "$name stopped"
            return 0
        fi
        sleep 0.1
    done

    log "$name pid $pid did not exit after SIGTERM; sending SIGKILL"
    kill -9 "$pid" >/dev/null 2>&1 || true
    rm -f "$pid_file"
    log "$name stopped"
}

stop_forward() {
    stop_one_forward "ui"
    stop_one_forward "prometheus"
    stop_one_forward "alertmanager"
}

status_one_forward() {
    local name="$1"
    local local_port="$2"
    local pid_file log_file
    pid_file="$(pid_file_for "$name")"
    log_file="$(log_file_for "$name")"

    if is_running "$pid_file"; then
        log "$name running on http://127.0.0.1:$local_port (pid $(cat "$pid_file"))"
        log "$name log: $log_file"
        return 0
    fi
    rm -f "$pid_file"
    log "$name not running"
}

status_forward() {
    status_one_forward "ui" "$LOCAL_PORT"
    status_one_forward "prometheus" "$PROMETHEUS_LOCAL_PORT"
    status_one_forward "alertmanager" "$ALERTMANAGER_LOCAL_PORT"
}

show_logs() {
    local name="${1:-all}"
    local log_file

    if [ "$name" = "all" ]; then
        show_logs "ui"
        show_logs "prometheus"
        show_logs "alertmanager"
        return 0
    fi

    log_file="$(log_file_for "$name")"
    if [ -f "$log_file" ]; then
        log "$name log: $log_file"
        tail -n 80 "$log_file"
    else
        log "$name log file does not exist: $log_file"
    fi
}

usage() {
    cat <<EOF
Usage: $(basename "$0") start|stop|restart|status|logs [ui|prometheus|alertmanager]

Environment overrides:
  NAMESPACE=$NAMESPACE
  SERVICE_NAME=$SERVICE_NAME
  LOCAL_PORT=$LOCAL_PORT
  SERVICE_PORT=$SERVICE_PORT
  MONITORING_NAMESPACE=$MONITORING_NAMESPACE
  PROMETHEUS_SERVICE_NAME=$PROMETHEUS_SERVICE_NAME
  PROMETHEUS_LOCAL_PORT=$PROMETHEUS_LOCAL_PORT
  PROMETHEUS_SERVICE_PORT=$PROMETHEUS_SERVICE_PORT
  ALERTMANAGER_SERVICE_NAME=$ALERTMANAGER_SERVICE_NAME
  ALERTMANAGER_LOCAL_PORT=$ALERTMANAGER_LOCAL_PORT
  ALERTMANAGER_SERVICE_PORT=$ALERTMANAGER_SERVICE_PORT
  STATE_DIR=$STATE_DIR
EOF
}

command="${1:-status}"
case "$command" in
    start)
        start_forward
        ;;
    stop)
        stop_forward
        ;;
    restart)
        stop_forward
        start_forward
        ;;
    status)
        status_forward
        ;;
    logs)
        show_logs "${2:-all}"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

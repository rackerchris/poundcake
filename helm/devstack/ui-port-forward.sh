#!/usr/bin/env bash
# Manage local kubectl port-forwards for Helm devstack work.

set -euo pipefail

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
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
ENABLE_PROMETHEUS_PORT_FORWARD="${ENABLE_PROMETHEUS_PORT_FORWARD:-true}"
ENABLE_ALERTMANAGER_PORT_FORWARD="${ENABLE_ALERTMANAGER_PORT_FORWARD:-true}"
STATE_DIR="${STATE_DIR:-/tmp/poundcake-helm-devstack}"
PID_FILE="${PID_FILE:-$STATE_DIR/ui-port-forward.pid}"
LOG_FILE="${LOG_FILE:-$STATE_DIR/ui-port-forward.log}"
VERIFY_TIMEOUT_SEC="${VERIFY_TIMEOUT_SEC:-30}"

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
PYTHON_BIN="$(detect_executable PYTHON_BIN python3 /opt/homebrew/bin/python3 /usr/local/bin/python3)"
CURL_BIN="$(detect_executable CURL_BIN curl /opt/homebrew/bin/curl /usr/local/bin/curl)"

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

verify_path_for() {
    local name="$1"
    case "$name" in
        ui)
            printf '%s\n' "/"
            ;;
        prometheus|alertmanager)
            printf '%s\n' "/-/ready"
            ;;
        *)
            printf '%s\n' "/"
            ;;
    esac
}

verify_codes_for() {
    local name="$1"
    case "$name" in
        ui)
            printf '%s\n' "200,302,304"
            ;;
        prometheus|alertmanager)
            printf '%s\n' "200"
            ;;
        *)
            printf '%s\n' "200"
            ;;
    esac
}

wait_for_port() {
    local host="$1"
    local port="$2"
    local name="$3"
    local deadline=$((SECONDS + VERIFY_TIMEOUT_SEC))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if "$PYTHON_BIN" - "$host" "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.create_connection((host, port), timeout=1):
    pass
PY
        then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_http() {
    local host="$1"
    local port="$2"
    local path="$3"
    local accepted_codes="$4"
    local name="$5"
    local url="http://${host}:${port}${path}"
    local deadline=$((SECONDS + VERIFY_TIMEOUT_SEC))
    local code
    while [ "$SECONDS" -lt "$deadline" ]; do
        code="$("$CURL_BIN" -sS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
        case ",$accepted_codes," in
            *",$code,"*)
                return 0
                ;;
        esac
        sleep 1
    done
    return 1
}

show_failure_context() {
    local name="$1"
    local log_file="$2"
    local local_port="$3"

    if [ -f "$log_file" ]; then
        log "$name log follows"
        sed -n '1,120p' "$log_file" >&2
    fi
    if "$PYTHON_BIN" - "$local_port" <<'PY' >/dev/null 2>&1
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", port))
PY
    then
        :
    else
        log "local port ${local_port} is already in use"
    fi
}

is_transient_start_failure() {
    local log_file="$1"
    [ -f "$log_file" ] || return 1

    if rg -q \
        -e 'pod is not running\. Current status=' \
        -e 'no endpoints available for service' \
        -e 'error: timed out waiting for the condition' \
        "$log_file" 2>/dev/null; then
        return 0
    fi

    return 1
}

verify_one_forward() {
    local name="$1"
    local local_port="$2"
    local pid_file log_file verify_path verify_codes pid
    pid_file="$(pid_file_for "$name")"
    log_file="$(log_file_for "$name")"
    verify_path="$(verify_path_for "$name")"
    verify_codes="$(verify_codes_for "$name")"

    if ! is_running "$pid_file"; then
        rm -f "$pid_file"
        show_failure_context "$name" "$log_file" "$local_port"
        fail "$name port-forward is not running"
    fi

    pid="$(cat "$pid_file")"
    if ! wait_for_port "127.0.0.1" "$local_port" "$name"; then
        show_failure_context "$name" "$log_file" "$local_port"
        fail "$name port-forward did not open local port ${local_port} (pid ${pid})"
    fi

    if ! wait_for_http "127.0.0.1" "$local_port" "$verify_path" "$verify_codes" "$name"; then
        show_failure_context "$name" "$log_file" "$local_port"
        fail "$name port-forward did not pass HTTP verification on ${verify_path}"
    fi

    log "$name ready: http://127.0.0.1:$local_port"
    log "$name log: $log_file"
}

start_one_forward() {
    local name="$1"
    local namespace="$2"
    local service_name="$3"
    local local_port="$4"
    local service_port="$5"
    local pid_file log_file deadline started_pid
    pid_file="$(pid_file_for "$name")"
    log_file="$(log_file_for "$name")"

    if is_running "$pid_file"; then
        if wait_for_port "127.0.0.1" "$local_port" "$name" && \
           wait_for_http "127.0.0.1" "$local_port" "$(verify_path_for "$name")" "$(verify_codes_for "$name")" "$name"; then
            log "$name already running on http://127.0.0.1:$local_port (pid $(cat "$pid_file"))"
            return 0
        fi
        log "$name process exists but local endpoint is unhealthy; restarting"
        stop_one_forward "$name"
    fi

    mkdir -p "$STATE_DIR"
    rm -f "$pid_file"
    : > "$log_file"

    deadline=$((SECONDS + VERIFY_TIMEOUT_SEC))
    while :; do
        : > "$log_file"
        log "starting $name port-forward svc/$service_name $local_port:$service_port in namespace $namespace"
        nohup "$KUBECTL_BIN" -n "$namespace" port-forward "svc/$service_name" "$local_port:$service_port" >"$log_file" 2>&1 &
        started_pid="$!"
        printf '%s\n' "$started_pid" > "$pid_file"

        sleep 1
        if is_running "$pid_file"; then
            break
        fi

        rm -f "$pid_file"
        if [ "$SECONDS" -lt "$deadline" ] && is_transient_start_failure "$log_file"; then
            log "$name target is not ready yet; retrying port-forward startup"
            sleep 1
            continue
        fi

        show_failure_context "$name" "$log_file" "$local_port"
        fail "$name port-forward exited early"
    done

    verify_one_forward "$name" "$local_port"
}

start_forward() {
    start_one_forward "ui" "$POUNDCAKE_NAMESPACE" "$SERVICE_NAME" "$LOCAL_PORT" "$SERVICE_PORT"
    if [ "$ENABLE_PROMETHEUS_PORT_FORWARD" = "true" ]; then
        start_one_forward "prometheus" "$MONITORING_NAMESPACE" "$PROMETHEUS_SERVICE_NAME" "$PROMETHEUS_LOCAL_PORT" "$PROMETHEUS_SERVICE_PORT"
    fi
    if [ "$ENABLE_ALERTMANAGER_PORT_FORWARD" = "true" ]; then
        start_one_forward "alertmanager" "$MONITORING_NAMESPACE" "$ALERTMANAGER_SERVICE_NAME" "$ALERTMANAGER_LOCAL_PORT" "$ALERTMANAGER_SERVICE_PORT"
    fi
}

verify_forward() {
    verify_one_forward "ui" "$LOCAL_PORT"
    if [ "$ENABLE_PROMETHEUS_PORT_FORWARD" = "true" ]; then
        verify_one_forward "prometheus" "$PROMETHEUS_LOCAL_PORT"
    fi
    if [ "$ENABLE_ALERTMANAGER_PORT_FORWARD" = "true" ]; then
        verify_one_forward "alertmanager" "$ALERTMANAGER_LOCAL_PORT"
    fi
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
        if wait_for_port "127.0.0.1" "$local_port" "$name" && \
           wait_for_http "127.0.0.1" "$local_port" "$(verify_path_for "$name")" "$(verify_codes_for "$name")" "$name"; then
            log "$name running on http://127.0.0.1:$local_port (pid $(cat "$pid_file"))"
        else
            log "$name running but local endpoint is unhealthy on http://127.0.0.1:$local_port (pid $(cat "$pid_file"))"
        fi
        log "$name log: $log_file"
        return 0
    fi
    rm -f "$pid_file"
    log "$name not running"
}

status_forward() {
    status_one_forward "ui" "$LOCAL_PORT"
    if [ "$ENABLE_PROMETHEUS_PORT_FORWARD" = "true" ]; then
        status_one_forward "prometheus" "$PROMETHEUS_LOCAL_PORT"
    fi
    if [ "$ENABLE_ALERTMANAGER_PORT_FORWARD" = "true" ]; then
        status_one_forward "alertmanager" "$ALERTMANAGER_LOCAL_PORT"
    fi
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
Usage: $(basename "$0") start|stop|restart|status|verify|logs [ui|prometheus|alertmanager]

Environment overrides:
  POUNDCAKE_NAMESPACE=$POUNDCAKE_NAMESPACE
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
  ENABLE_PROMETHEUS_PORT_FORWARD=$ENABLE_PROMETHEUS_PORT_FORWARD
  ENABLE_ALERTMANAGER_PORT_FORWARD=$ENABLE_ALERTMANAGER_PORT_FORWARD
  STATE_DIR=$STATE_DIR
  VERIFY_TIMEOUT_SEC=$VERIFY_TIMEOUT_SEC
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
    verify)
        verify_forward
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

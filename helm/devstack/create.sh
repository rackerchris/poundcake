#!/usr/bin/env bash
# Create a local kind cluster and optionally install the PoundCake Helm chart.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
CHART_DIR="${CHART_DIR:-$PROJECT_ROOT/helm}"
KIND_CONFIG="${KIND_CONFIG:-$SCRIPT_DIR/kind-config.yaml}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-poundcake}"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-}"
NAMESPACE="${NAMESPACE:-poundcake}"
RELEASE_NAME="${RELEASE_NAME:-poundcake}"
INSTALL_CHART="${INSTALL_CHART:-}"
INSTALL_MONITORING="${INSTALL_MONITORING:-}"
INSTALL_STACKSTORM="${INSTALL_STACKSTORM:-}"
CONFIGURE_STACKSTORM_ADAPTER="${CONFIGURE_STACKSTORM_ADAPTER:-true}"
APPLY_NODE_SYSCTLS="${APPLY_NODE_SYSCTLS:-true}"
PORT_FORWARD="${PORT_FORWARD:-true}"
WAIT="${WAIT:-true}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-15m}"
VALUES_FILE="${VALUES_FILE:-$SCRIPT_DIR/values/poundcake-plugins-kind.yaml}"
HELM_EXTRA_ARGS="${HELM_EXTRA_ARGS:-}"
BUILD_IMAGES="${BUILD_IMAGES:-false}"
LOAD_IMAGES="${LOAD_IMAGES:-false}"
CREATE_CLUSTER="${CREATE_CLUSTER:-true}"
INSTALL_ALL="${INSTALL_ALL:-false}"

# Images to build/load
APP_IMAGE="${APP_IMAGE:-poundcake:local}"
APP_DOCKERFILE="${APP_DOCKERFILE:-$PROJECT_ROOT/Dockerfile}"
UI_IMAGE="${UI_IMAGE:-poundcake-ui:local}"
UI_DOCKERFILE="${UI_DOCKERFILE:-$PROJECT_ROOT/ui/Dockerfile}"

log() {
    printf '[helm-devstack-create] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-create] ERROR: %s\n' "$*" >&2
    exit 1
}

cluster_exists() {
    "$KIND_BIN" get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"
}

usage() {
    cat <<'EOF'
Usage: create.sh [OPTIONS]

Create a local kind devstack and optionally install components.

Cluster options:
  --create-cluster          Create the kind cluster (default when no cluster exists)
  --skip-create-cluster     Skip cluster creation; assume it already exists

Image options:
  --build-images            Build poundcake images via docker build (default: false)
  --load-images             Load images into kind cluster (default: false)
  --app-image IMAGE         Override API image tag (default: poundcake:local)
  --ui-image IMAGE          Override UI image tag (default: poundcake-ui:local)

Install options:
  --install-all             Install poundcake, prometheus, alertmanager, and stackstorm
  --install-poundcake       Install only the PoundCake Helm chart
  --install-monitoring      Install Prometheus/Alertmanager stack
  --install-stackstorm      Install StackStorm (includes adapter configuration)
  --skip-stackstorm-config  Skip StackStorm adapter configuration
  --no-port-forward         Skip local port-forwards

General options:
  --skip-node-sysctls       Skip kind node sysctl tuning
  --no-wait                 Skip helm --wait
  --timeout DURATION        Wait timeout (default: 15m)
  --values FILE             Override values file
  --help                    Show this help message

When no --install-* or --install-all flags are given, only the kind cluster
is created (if --create-cluster is set) and nothing else is installed.
EOF
}

# Parse CLI arguments
INSTALL_CHART="false"
INSTALL_MONITORING="false"
INSTALL_STACKSTORM="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        --create-cluster)
            CREATE_CLUSTER="true"
            shift
            ;;
        --skip-create-cluster)
            CREATE_CLUSTER="false"
            shift
            ;;
        --build-images)
            BUILD_IMAGES="true"
            shift
            ;;
        --load-images)
            LOAD_IMAGES="true"
            shift
            ;;
        --app-image)
            APP_IMAGE="$2"
            shift 2
            ;;
        --ui-image)
            UI_IMAGE="$2"
            shift 2
            ;;
        --install-all)
            INSTALL_ALL="true"
            INSTALL_CHART="true"
            INSTALL_MONITORING="true"
            INSTALL_STACKSTORM="true"
            shift
            ;;
        --install-poundcake)
            INSTALL_CHART="true"
            shift
            ;;
        --install-monitoring)
            INSTALL_MONITORING="true"
            shift
            ;;
        --install-stackstorm)
            INSTALL_STACKSTORM="true"
            shift
            ;;
        --skip-stackstorm-config)
            CONFIGURE_STACKSTORM_ADAPTER="false"
            shift
            ;;
        --skip-node-sysctls)
            APPLY_NODE_SYSCTLS="false"
            shift
            ;;
        --no-port-forward)
            PORT_FORWARD="false"
            shift
            ;;
        --no-wait)
            WAIT="false"
            shift
            ;;
        --timeout)
            WAIT_TIMEOUT="$2"
            shift 2
            ;;
        --values)
            VALUES_FILE="$2"
            shift 2
            ;;
        *)
            fail "Unknown option: $1. Use --help for usage."
            ;;
    esac
done

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

DOCKER_BIN="$(detect_executable DOCKER_BIN docker /opt/homebrew/bin/docker /usr/local/bin/docker)"

# 1. Build images (if requested)
if [ "$BUILD_IMAGES" = "true" ]; then
    log "building API image $APP_IMAGE from $APP_DOCKERFILE"
    "$DOCKER_BIN" build -t "$APP_IMAGE" -f "$APP_DOCKERFILE" "$PROJECT_ROOT"
    log "building UI image $UI_IMAGE from $UI_DOCKERFILE"
    "$DOCKER_BIN" build -t "$UI_IMAGE" -f "$UI_DOCKERFILE" "$PROJECT_ROOT"
fi

# 2. Create kind cluster (must happen before loading images or installing anything)
KIND_BIN="$(detect_executable KIND_BIN kind /opt/homebrew/bin/kind /usr/local/bin/kind)"

if [ "$CREATE_CLUSTER" = "true" ]; then
    if ! cluster_exists; then
        create_args=(create cluster --name "$KIND_CLUSTER_NAME" --config "$KIND_CONFIG")
        if [ -n "$KIND_NODE_IMAGE" ]; then
            create_args+=(--image "$KIND_NODE_IMAGE")
        fi
        log "creating kind cluster $KIND_CLUSTER_NAME from $KIND_CONFIG"
        "$KIND_BIN" "${create_args[@]}"
    else
        log "kind cluster already exists: $KIND_CLUSTER_NAME"
    fi
elif [ "$CREATE_CLUSTER" = "false" ]; then
    if ! cluster_exists; then
        fail "kind cluster $KIND_CLUSTER_NAME does not exist. Use --create-cluster to create it."
    fi
    log "kind cluster already exists: $KIND_CLUSTER_NAME; skipping creation"
fi

# 3. Load images into kind cluster (if requested or images were just built)
if [ "$LOAD_IMAGES" = "true" ] || [ "$BUILD_IMAGES" = "true" ]; then
    if ! cluster_exists; then
        fail "kind cluster $KIND_CLUSTER_NAME does not exist to load images. Run with --create-cluster first."
    fi
    log "loading images into kind cluster $KIND_CLUSTER_NAME"
    "$KIND_BIN" load docker-image "$APP_IMAGE" "$UI_IMAGE" --name "$KIND_CLUSTER_NAME"
fi

# 4. Apply node sysctls
apply_node_sysctls() {
    if [ "$APPLY_NODE_SYSCTLS" != "true" ]; then
        log "APPLY_NODE_SYSCTLS=false; skipping kind node sysctl tuning"
        return 0
    fi

    local nodes
    if ! nodes="$("$DOCKER_BIN" ps --filter "label=io.x-k8s.kind.cluster=$KIND_CLUSTER_NAME" --format '{{.Names}}')"; then
        fail "failed to list Docker containers for kind cluster $KIND_CLUSTER_NAME"
    fi
    if [ -z "$nodes" ]; then
        fail "no kind node containers found for cluster $KIND_CLUSTER_NAME"
    fi

    log "applying kind node sysctls for fsnotify-heavy bootstrap workloads"
    while IFS= read -r node; do
        [ -n "$node" ] || continue
        log "tuning node $node"
        "$DOCKER_BIN" exec "$node" sysctl -w fs.inotify.max_user_watches=1048576 >/dev/null
        "$DOCKER_BIN" exec "$node" sysctl -w fs.inotify.max_user_instances=8192 >/dev/null
        "$DOCKER_BIN" exec "$node" sysctl -w fs.file-max=2097152 >/dev/null
    done <<< "$nodes"
}

apply_node_sysctls

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"
HELM_BIN="$(detect_executable HELM_BIN helm /opt/homebrew/bin/helm /usr/local/bin/helm)"

# Pre-install check: cluster must exist for any helm/kubectl work
if [ "$INSTALL_CHART" = "true" ] || [ "$INSTALL_MONITORING" = "true" ] || [ "$INSTALL_STACKSTORM" = "true" ]; then
    if ! cluster_exists; then
        fail "Cannot install components: kind cluster $KIND_CLUSTER_NAME does not exist. Run with --create-cluster first."
    fi
fi

"$KUBECTL_BIN" config use-context "kind-$KIND_CLUSTER_NAME" >/dev/null
log "using kubectl context kind-$KIND_CLUSTER_NAME"

# If no install flags given, exit with just the cluster
if [ "$INSTALL_CHART" = "false" ] && [ "$INSTALL_MONITORING" = "false" ] && [ "$INSTALL_STACKSTORM" = "false" ]; then
    log "no install flags set; cluster is ready without Helm install"
    exit 0
fi

"$KUBECTL_BIN" create namespace "$NAMESPACE" --dry-run=client -o yaml | "$KUBECTL_BIN" apply -f -

if [ "$INSTALL_CHART" = "true" ]; then
    helm_args=(
        upgrade
        --install
        "$RELEASE_NAME"
        "$CHART_DIR"
        --namespace
        "$NAMESPACE"
    )
    if [ "$WAIT" = "true" ]; then
        helm_args+=(--wait --timeout "$WAIT_TIMEOUT")
    fi
    if [ -n "$VALUES_FILE" ]; then
        [ -f "$VALUES_FILE" ] || fail "VALUES_FILE does not exist: $VALUES_FILE"
        helm_args+=(-f "$VALUES_FILE")
    fi
    if [ -n "$HELM_EXTRA_ARGS" ]; then
        read -r -a extra_args <<< "$HELM_EXTRA_ARGS"
        helm_args+=("${extra_args[@]}")
    fi

    log "installing Helm release $RELEASE_NAME in namespace $NAMESPACE"
    "$HELM_BIN" "${helm_args[@]}"
else
    log "INSTALL_POUNDCAKE=false; skipping PoundCake Helm chart"
fi

if [ "$INSTALL_MONITORING" = "true" ]; then
    log "installing external Prometheus Operator monitoring stack"
    NAMESPACE="$NAMESPACE" WAIT="$WAIT" "$SCRIPT_DIR/install-prometheus.sh"
else
    log "INSTALL_MONITORING=false; skipping external Prometheus Operator monitoring stack"
fi

if [ "$INSTALL_STACKSTORM" = "true" ]; then
    log "installing external StackStorm release"
    NAMESPACE="$NAMESPACE" WAIT="$WAIT" "$SCRIPT_DIR/install-stackstorm.sh"
    if [ "$CONFIGURE_STACKSTORM_ADAPTER" = "true" ]; then
        log "configuring PoundCake StackStorm adapter"
        NAMESPACE="$NAMESPACE" WAIT_TIMEOUT="$WAIT_TIMEOUT" "$SCRIPT_DIR/configure-stackstorm-adapter.sh"
    else
        log "CONFIGURE_STACKSTORM_ADAPTER=false; skipping StackStorm adapter configuration"
    fi
else
    log "INSTALL_STACKSTORM=false; skipping external StackStorm release"
fi

if [ "$PORT_FORWARD" = "true" ]; then
    log "starting local port-forwards"
    NAMESPACE="$NAMESPACE" "$SCRIPT_DIR/ui-port-forward.sh" start
else
    log "PORT_FORWARD=false; skipping local port-forwards"
fi

log "Helm devstack is ready"
log "cluster: kind-$KIND_CLUSTER_NAME"
log "namespace: $NAMESPACE"
log "release: $RELEASE_NAME"
if [ "$PORT_FORWARD" = "true" ]; then
    log "ui: http://127.0.0.1:${LOCAL_PORT:-8080}"
    log "prometheus: http://127.0.0.1:${PROMETHEUS_LOCAL_PORT:-9090}"
    log "alertmanager: http://127.0.0.1:${ALERTMANAGER_LOCAL_PORT:-9093}"
fi

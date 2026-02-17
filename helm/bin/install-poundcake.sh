#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_DIR="$(dirname "$SCRIPT_DIR")"
source "${HELM_DIR}/scripts/common-functions.sh"

SERVICE_NAME="${POUNDCAKE_SERVICE_NAME:-poundcake}"
NAMESPACE="${POUNDCAKE_NAMESPACE:-rackspace}"
RELEASE_NAME="${POUNDCAKE_RELEASE_NAME:-poundcake}"
GHCR_OWNER="${POUNDCAKE_GHCR_OWNER:-aedan}"
CHART_REPO="${POUNDCAKE_CHART_REPO:-oci://ghcr.io/${GHCR_OWNER}/charts/poundcake}"
APP_IMAGE_REPO="${POUNDCAKE_IMAGE_REPO:-ghcr.io/${GHCR_OWNER}/poundcake}"
UI_IMAGE_REPO="${POUNDCAKE_UI_IMAGE_REPO:-ghcr.io/${GHCR_OWNER}/poundcake-ui}"
BAKERY_IMAGE_REPO="${POUNDCAKE_BAKERY_IMAGE_REPO:-ghcr.io/${GHCR_OWNER}/poundcake-bakery}"
STACKSTORM_VERSION="${POUNDCAKE_STACKSTORM_VERSION:-3.9.0}"
STACKSTORM_FULLNAME_OVERRIDE="${POUNDCAKE_STACKSTORM_FULLNAME_OVERRIDE:-st2}"
VERSION_FILE="/etc/genestack/helm-chart-versions.yaml"
GLOBAL_OVERRIDES_DIR="/etc/genestack/helm-configs/global_overrides"
SERVICE_CONFIG_DIR="/etc/genestack/helm-configs/poundcake"
BASE_OVERRIDES_DIR="/opt/genestack/base-helm-configs/poundcake"
BASE_OVERRIDES_FILE="poundcake-helm-overrides.yaml"
KUSTOMIZE_RENDERER="/etc/genestack/kustomize/kustomize.sh"
KUSTOMIZE_OVERLAY_DIR="/etc/genestack/kustomize/poundcake/overlay"
KUSTOMIZE_OVERLAY_ARG="poundcake/overlay"

ROTATE_SECRETS=false
VALIDATE="${POUNDCAKE_HELM_VALIDATE:-false}"
SKIP_PREFLIGHT=false
HELM_WAIT="${POUNDCAKE_HELM_WAIT:-false}"
HELM_ATOMIC="${POUNDCAKE_HELM_ATOMIC:-false}"
HELM_CLEANUP_ON_FAIL="${POUNDCAKE_HELM_CLEANUP_ON_FAIL:-false}"
ALLOW_WAIT_WITH_STAGED_STARTUP="${POUNDCAKE_ALLOW_WAIT_WITH_STAGED_STARTUP:-false}"
PASSTHROUGH_ARGS=()
POST_RENDER_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rotate-secrets)
      ROTATE_SECRETS=true
      shift
      ;;
    --validate)
      VALIDATE=true
      shift
      ;;
    --skip-preflight)
      SKIP_PREFLIGHT=true
      shift
      ;;
    *)
      PASSTHROUGH_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$SKIP_PREFLIGHT" != "true" ]]; then
  perform_preflight_checks
fi

POUNDCAKE_VERSION="$(get_chart_version "poundcake" "$VERSION_FILE")"

if [[ -z "${POUNDCAKE_VERSION}" ]]; then
  echo "Error: could not determine PoundCake chart version from ${VERSION_FILE} (key: poundcake)." >&2
  exit 1
fi

echo "Installing PoundCake chart version: ${POUNDCAKE_VERSION}"
ensure_oci_registry_auth "$CHART_REPO"

echo "Override file accessibility check:"
base_path="${BASE_OVERRIDES_DIR}/${BASE_OVERRIDES_FILE}"
if [[ -r "$base_path" ]]; then
  echo "  [readable] ${base_path}"
elif [[ -e "$base_path" ]]; then
  echo "  [not-readable] ${base_path}"
else
  echo "  [missing] ${base_path}"
fi

if [[ -d "$GLOBAL_OVERRIDES_DIR" ]]; then
  if compgen -G "${GLOBAL_OVERRIDES_DIR}/*.yaml" >/dev/null; then
    for yaml_file in "${GLOBAL_OVERRIDES_DIR}"/*.yaml; do
      if [[ -r "$yaml_file" ]]; then
        echo "  [readable] ${yaml_file}"
      elif [[ -e "$yaml_file" ]]; then
        echo "  [not-readable] ${yaml_file}"
      fi
    fi
  else
    echo "  [none] ${GLOBAL_OVERRIDES_DIR}/*.yaml"
  fi
else
  echo "  [missing-dir] ${GLOBAL_OVERRIDES_DIR}"
fi

if [[ -d "$SERVICE_CONFIG_DIR" ]]; then
  if compgen -G "${SERVICE_CONFIG_DIR}/*.yaml" >/dev/null; then
    for yaml_file in "${SERVICE_CONFIG_DIR}"/*.yaml; do
      if [[ -r "$yaml_file" ]]; then
        echo "  [readable] ${yaml_file}"
      elif [[ -e "$yaml_file" ]]; then
        echo "  [not-readable] ${yaml_file}"
      fi
    fi
  else
    echo "  [none] ${SERVICE_CONFIG_DIR}/*.yaml"
  fi
else
  echo "  [missing-dir] ${SERVICE_CONFIG_DIR}"
fi

OVERRIDE_ARGS=()
if [[ -d "$BASE_OVERRIDES_DIR" ]]; then
  base_path="${BASE_OVERRIDES_DIR}/${BASE_OVERRIDES_FILE}"
  if [[ -f "$base_path" ]]; then
    OVERRIDE_ARGS+=("-f" "$base_path")
  fi
fi

if [[ -d "$GLOBAL_OVERRIDES_DIR" ]] && compgen -G "${GLOBAL_OVERRIDES_DIR}/*.yaml" >/dev/null; then
  for yaml_file in "${GLOBAL_OVERRIDES_DIR}"/*.yaml; do
    OVERRIDE_ARGS+=("-f" "$yaml_file")
  done
fi

if [[ -d "$SERVICE_CONFIG_DIR" ]] && compgen -G "${SERVICE_CONFIG_DIR}/*.yaml" >/dev/null; then
  for yaml_file in "${SERVICE_CONFIG_DIR}"/*.yaml; do
    OVERRIDE_ARGS+=("-f" "$yaml_file")
  done
fi

echo "Using override files (in order):"
if [[ ${#OVERRIDE_ARGS[@]} -eq 0 ]]; then
  echo "  (none)"
else
  for ((i=0; i<${#OVERRIDE_ARGS[@]}; i+=2)); do
    echo "  ${OVERRIDE_ARGS[i+1]}"
  done
fi

# Add post-renderer if available AND overlay exists.
if [[ -f "$KUSTOMIZE_RENDERER" && -d "$KUSTOMIZE_OVERLAY_DIR" ]]; then
  POST_RENDER_ARGS+=("--post-renderer" "$KUSTOMIZE_RENDERER")
  POST_RENDER_ARGS+=("--post-renderer-args" "$KUSTOMIZE_OVERLAY_ARG")
fi

if [[ "$ROTATE_SECRETS" == "true" ]]; then
  rotate_chart_secrets "$NAMESPACE" "$RELEASE_NAME"
fi

if [[ "$VALIDATE" == "true" ]]; then
  run_helm_validation "$CHART_REPO" "$POUNDCAKE_VERSION" "$NAMESPACE" "$RELEASE_NAME" \
    "${OVERRIDE_ARGS[@]}" \
    "${POST_RENDER_ARGS[@]}" \
    "${PASSTHROUGH_ARGS[@]}"
fi

if [[ "$HELM_WAIT" == "true" && "$ALLOW_WAIT_WITH_STAGED_STARTUP" != "true" ]]; then
  echo "Error: POUNDCAKE_HELM_WAIT=true can deadlock staged StackStorm bootstrap (post-install hooks)." >&2
  echo "Set POUNDCAKE_HELM_WAIT=false (recommended) or set POUNDCAKE_ALLOW_WAIT_WITH_STAGED_STARTUP=true to override." >&2
  exit 1
fi

HELM_CMD=(
  helm upgrade --install "$RELEASE_NAME" "$CHART_REPO"
  --version "$POUNDCAKE_VERSION"
  --namespace "$NAMESPACE"
  --create-namespace
  --timeout "${HELM_TIMEOUT:-$HELM_TIMEOUT_DEFAULT}"
  --set "image.repository=${APP_IMAGE_REPO}"
  --set "ui.image.repository=${UI_IMAGE_REPO}"
  --set "bakery.image.repository=${BAKERY_IMAGE_REPO}"
  --set "stackstorm-chart.imageTag=${STACKSTORM_VERSION}"
  --set "stackstorm-chart.st2.image.tag=${STACKSTORM_VERSION}"
  --set "stackstorm-chart.st2client.image.tag=${STACKSTORM_VERSION}"
  --set "stackstorm-chart.fullnameOverride=${STACKSTORM_FULLNAME_OVERRIDE}"
  --set "stackstorm.subchart.fullnameOverride=${STACKSTORM_FULLNAME_OVERRIDE}"
  --set "stackstorm.startupOrder.enabled=true"
  --set "stackstorm.startupOrder.coreReplicas.st2register=1"
  --set "stackstorm.startupOrder.coreReplicas.st2auth=1"
  --set "stackstorm.startupOrder.coreReplicas.st2api=1"
  --set "stackstorm.startupOrder.workerReplicas.st2actionrunner=1"
  --set "stackstorm.startupOrder.workerReplicas.st2rulesengine=1"
  --set "stackstorm.startupOrder.workerReplicas.st2workflowengine=1"
  --set "stackstorm.startupOrder.workerReplicas.st2scheduler=1"
  --set "stackstorm.startupOrder.workerReplicas.st2notifier=1"
  --set "stackstorm.startupOrder.workerReplicas.st2garbagecollector=1"
  --set "stackstorm.startupOrder.workerReplicas.st2timersengine=1"
  --set "stackstorm.startupOrder.workerReplicas.st2chatops=1"
  --set "stackstorm.startupOrder.edgeReplicas.st2sensorcontainer=1"
  --set "stackstorm.startupOrder.edgeReplicas.st2stream=1"
  --set "stackstorm.startupOrder.edgeReplicas.st2web=1"
  --set "stackstorm.startupOrder.replicas.st2actionrunner=1"
  --set "stackstorm.startupOrder.replicas.st2rulesengine=1"
  --set "stackstorm.startupOrder.replicas.st2workflowengine=1"
  --set "stackstorm.startupOrder.replicas.st2scheduler=1"
  --set "stackstorm.startupOrder.replicas.st2notifier=1"
  --set "stackstorm.startupOrder.replicas.st2garbagecollector=1"
  --set "stackstorm.startupOrder.replicas.st2timersengine=1"
  --set "stackstorm.startupOrder.replicas.st2chatops=1"
  --set "stackstorm.startupOrder.replicas.st2sensorcontainer=1"
  --set "stackstorm.startupOrder.replicas.st2stream=1"
  --set "stackstorm.startupOrder.replicas.st2web=1"
  --set "stackstorm-chart.st2api.replicaCount=0"
  --set "stackstorm-chart.st2api.replicas=0"
  --set "stackstorm-chart.st2auth.replicaCount=0"
  --set "stackstorm-chart.st2auth.replicas=0"
  --set "stackstorm-chart.st2register.replicaCount=0"
  --set "stackstorm-chart.st2register.replicas=0"
  --set "stackstorm-chart.st2actionrunner.replicaCount=0"
  --set "stackstorm-chart.st2actionrunner.replicas=0"
  --set "stackstorm-chart.st2rulesengine.replicaCount=0"
  --set "stackstorm-chart.st2rulesengine.replicas=0"
  --set "stackstorm-chart.st2workflowengine.replicaCount=0"
  --set "stackstorm-chart.st2workflowengine.replicas=0"
  --set "stackstorm-chart.st2scheduler.replicaCount=0"
  --set "stackstorm-chart.st2scheduler.replicas=0"
  --set "stackstorm-chart.st2notifier.replicaCount=0"
  --set "stackstorm-chart.st2notifier.replicas=0"
  --set "stackstorm-chart.st2garbagecollector.replicaCount=0"
  --set "stackstorm-chart.st2garbagecollector.replicas=0"
  --set "stackstorm-chart.st2timersengine.replicaCount=0"
  --set "stackstorm-chart.st2timersengine.replicas=0"
  --set "stackstorm-chart.st2chatops.replicaCount=0"
  --set "stackstorm-chart.st2chatops.replicas=0"
  --set "stackstorm-chart.st2sensorcontainer.replicaCount=0"
  --set "stackstorm-chart.st2sensorcontainer.replicas=0"
  --set "stackstorm-chart.st2stream.replicaCount=0"
  --set "stackstorm-chart.st2stream.replicas=0"
  --set "stackstorm-chart.st2web.replicaCount=0"
  --set "stackstorm-chart.st2web.replicas=0"
  "${OVERRIDE_ARGS[@]}"
  "${POST_RENDER_ARGS[@]}"
)

if [[ "$HELM_WAIT" == "true" ]]; then
  HELM_CMD+=(--wait)
fi

if [[ "$HELM_ATOMIC" == "true" ]]; then
  HELM_CMD+=(--atomic)
fi

if [[ "$HELM_CLEANUP_ON_FAIL" == "true" ]]; then
  HELM_CMD+=(--cleanup-on-fail)
fi

echo "Executing Helm command:"
printf '%q ' "${HELM_CMD[@]}" "${PASSTHROUGH_ARGS[@]}"
echo

"${HELM_CMD[@]}" "${PASSTHROUGH_ARGS[@]}"

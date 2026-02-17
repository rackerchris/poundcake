#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

RELEASE_NAME="${POUNDCAKE_RELEASE_NAME:-poundcake}"
NAMESPACE="${POUNDCAKE_NAMESPACE:-rackspace}"
HELM_TIMEOUT="${POUNDCAKE_HELM_TIMEOUT:-120m}"
HELM_WAIT="${POUNDCAKE_HELM_WAIT:-true}"
HELM_ATOMIC="${POUNDCAKE_HELM_ATOMIC:-false}"
HELM_CLEANUP_ON_FAIL="${POUNDCAKE_HELM_CLEANUP_ON_FAIL:-false}"

POUNDCAKE_IMAGE_REPO="${POUNDCAKE_IMAGE_REPO:-ghcr.io/rackerchris/poundcake}"
POUNDCAKE_IMAGE_TAG="${POUNDCAKE_IMAGE_TAG:-d5dbf49}"
STACKSTORM_IMAGE_REPO="${POUNDCAKE_STACKSTORM_IMAGE_REPO:-stackstorm/st2}"
STACKSTORM_IMAGE_TAG="${POUNDCAKE_STACKSTORM_IMAGE_TAG:-3.9.0}"

EXTRA_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage:
  install-poundcake-standalone.sh [helm upgrade/install args]

Environment overrides:
  POUNDCAKE_RELEASE_NAME             (default: poundcake)
  POUNDCAKE_NAMESPACE                (default: rackspace)
  POUNDCAKE_HELM_TIMEOUT             (default: 120m)
  POUNDCAKE_HELM_WAIT                (default: true)
  POUNDCAKE_HELM_ATOMIC              (default: false)
  POUNDCAKE_HELM_CLEANUP_ON_FAIL     (default: false)
  POUNDCAKE_IMAGE_REPO               (default: ghcr.io/rackerchris/poundcake)
  POUNDCAKE_IMAGE_TAG                (default: d5dbf49)
  POUNDCAKE_STACKSTORM_IMAGE_REPO    (default: stackstorm/st2)
  POUNDCAKE_STACKSTORM_IMAGE_TAG     (default: 3.9.0)

Examples:
  ./bin/install-poundcake-standalone.sh
  POUNDCAKE_HELM_WAIT=false ./bin/install-poundcake-standalone.sh
  ./bin/install-poundcake-standalone.sh -f /etc/genestack/helm-configs/poundcake/poundcake-helm-overrides.yaml
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if ! command -v helm >/dev/null 2>&1; then
  echo "Error: helm is not installed or not in PATH." >&2
  exit 1
fi

HELM_CMD=(
  helm upgrade --install "${RELEASE_NAME}" "${CHART_DIR}"
  --namespace "${NAMESPACE}"
  --create-namespace
  --timeout "${HELM_TIMEOUT}"
  --set "poundcakeImage.repository=${POUNDCAKE_IMAGE_REPO}"
  --set "poundcakeImage.tag=${POUNDCAKE_IMAGE_TAG}"
  --set "stackstormImage.repository=${STACKSTORM_IMAGE_REPO}"
  --set "stackstormImage.tag=${STACKSTORM_IMAGE_TAG}"
)

if [[ "${HELM_WAIT}" == "true" ]]; then
  HELM_CMD+=(--wait)
fi

if [[ "${HELM_ATOMIC}" == "true" ]]; then
  HELM_CMD+=(--atomic)
fi

if [[ "${HELM_CLEANUP_ON_FAIL}" == "true" ]]; then
  HELM_CMD+=(--cleanup-on-fail)
fi

echo "Installing chart from: ${CHART_DIR}"
echo "Release: ${RELEASE_NAME}"
echo "Namespace: ${NAMESPACE}"
echo "PoundCake image: ${POUNDCAKE_IMAGE_REPO}:${POUNDCAKE_IMAGE_TAG}"
echo "StackStorm image: ${STACKSTORM_IMAGE_REPO}:${STACKSTORM_IMAGE_TAG}"
echo "Executing Helm command:"
printf '%q ' "${HELM_CMD[@]}" "${EXTRA_ARGS[@]}"
echo

"${HELM_CMD[@]}" "${EXTRA_ARGS[@]}"

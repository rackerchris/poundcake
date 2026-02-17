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

GHCR_OWNER="${POUNDCAKE_GHCR_OWNER:-rackerlabs}"
CHART_REPO="${POUNDCAKE_CHART_REPO:-}"
POUNDCAKE_IMAGE_REPO="${POUNDCAKE_IMAGE_REPO:-ghcr.io/${GHCR_OWNER}/poundcake}"
POUNDCAKE_IMAGE_TAG="${POUNDCAKE_IMAGE_TAG:-d5dbf49}"
STACKSTORM_IMAGE_REPO="${POUNDCAKE_STACKSTORM_IMAGE_REPO:-stackstorm/st2}"
STACKSTORM_IMAGE_TAG="${POUNDCAKE_STACKSTORM_IMAGE_TAG:-3.9.0}"
UI_IMAGE_REPO="${POUNDCAKE_UI_IMAGE_REPO:-}"
BAKERY_IMAGE_REPO="${POUNDCAKE_BAKERY_IMAGE_REPO:-}"
CHART_VERSION="${POUNDCAKE_CHART_VERSION:-}"
VERSION_FILE="${POUNDCAKE_VERSION_FILE:-/etc/genestack/helm-chart-versions.yaml}"
HELM_REGISTRY_USERNAME="${HELM_REGISTRY_USERNAME:-}"
HELM_REGISTRY_PASSWORD="${HELM_REGISTRY_PASSWORD:-}"

EXTRA_ARGS=("$@")

usage() {
  cat <<'EOF'
Usage:
  install-poundcake.sh [helm upgrade/install args]

Environment overrides:
  POUNDCAKE_GHCR_OWNER             (default: rackerlabs)
  POUNDCAKE_CHART_REPO             (default: local chart at ./helm)
  POUNDCAKE_CHART_VERSION          (optional; for OCI repo installs)
  POUNDCAKE_IMAGE_REPO             (default: ghcr.io/${POUNDCAKE_GHCR_OWNER}/poundcake)
  POUNDCAKE_IMAGE_TAG              (default: d5dbf49)
  POUNDCAKE_UI_IMAGE_REPO          (optional; accepted for compatibility)
  POUNDCAKE_BAKERY_IMAGE_REPO      (optional; accepted for compatibility)
  HELM_REGISTRY_USERNAME           (optional; for OCI login)
  HELM_REGISTRY_PASSWORD           (optional; for OCI login)
  POUNDCAKE_RELEASE_NAME           (default: poundcake)
  POUNDCAKE_NAMESPACE              (default: rackspace)
  POUNDCAKE_HELM_TIMEOUT           (default: 120m)
  POUNDCAKE_HELM_WAIT              (default: true)
  POUNDCAKE_HELM_ATOMIC            (default: false)
  POUNDCAKE_HELM_CLEANUP_ON_FAIL   (default: false)

Examples:
  ./install/install-helm.sh
  POUNDCAKE_GHCR_OWNER=rackerchris ./install/install-helm.sh
  POUNDCAKE_CHART_REPO=oci://ghcr.io/rackerchris/charts/poundcake ./install/install-helm.sh
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

if [[ -z "${CHART_REPO}" ]]; then
  CHART_SOURCE="${CHART_DIR}"
else
  CHART_SOURCE="${CHART_REPO}"
fi

if [[ -z "${CHART_VERSION}" && -f "${VERSION_FILE}" ]]; then
  detected_version="$(awk -F: '/^[[:space:]]*poundcake[[:space:]]*:/ {gsub(/[[:space:]]*/, "", $2); print $2; exit}' "${VERSION_FILE}")"
  if [[ -n "${detected_version}" ]]; then
    CHART_VERSION="${detected_version}"
  fi
fi

if [[ "${CHART_SOURCE}" == oci://* && -n "${HELM_REGISTRY_USERNAME}" ]]; then
  registry_host="$(echo "${CHART_SOURCE}" | sed -E 's#^oci://([^/]+)/.*#\1#')"
  if [[ -n "${HELM_REGISTRY_PASSWORD}" ]]; then
    echo "Authenticating Helm OCI client to ${registry_host} as ${HELM_REGISTRY_USERNAME}..."
    helm registry login "${registry_host}" -u "${HELM_REGISTRY_USERNAME}" --password-stdin <<<"${HELM_REGISTRY_PASSWORD}"
  else
    echo "HELM_REGISTRY_USERNAME is set but HELM_REGISTRY_PASSWORD is empty; skipping registry login."
  fi
fi

HELM_CMD=(
  helm upgrade --install "${RELEASE_NAME}" "${CHART_SOURCE}"
  --namespace "${NAMESPACE}"
  --create-namespace
  --timeout "${HELM_TIMEOUT}"
  --set "poundcakeImage.repository=${POUNDCAKE_IMAGE_REPO}"
  --set "poundcakeImage.tag=${POUNDCAKE_IMAGE_TAG}"
  --set "stackstormImage.repository=${STACKSTORM_IMAGE_REPO}"
  --set "stackstormImage.tag=${STACKSTORM_IMAGE_TAG}"
)

# Keep compatibility with legacy caller env/overrides even if chart doesn't consume all keys yet.
if [[ -n "${UI_IMAGE_REPO}" ]]; then
  HELM_CMD+=(--set "ui.image.repository=${UI_IMAGE_REPO}")
fi
if [[ -n "${BAKERY_IMAGE_REPO}" ]]; then
  HELM_CMD+=(--set "bakery.image.repository=${BAKERY_IMAGE_REPO}")
fi

if [[ "${CHART_SOURCE}" == oci://* && -n "${CHART_VERSION}" ]]; then
  HELM_CMD+=(--version "${CHART_VERSION}")
fi

if [[ "${HELM_WAIT}" == "true" ]]; then
  HELM_CMD+=(--wait)
fi
if [[ "${HELM_ATOMIC}" == "true" ]]; then
  HELM_CMD+=(--atomic)
fi
if [[ "${HELM_CLEANUP_ON_FAIL}" == "true" ]]; then
  HELM_CMD+=(--cleanup-on-fail)
fi

echo "Installing PoundCake release: ${RELEASE_NAME}"
echo "Namespace: ${NAMESPACE}"
echo "Chart source: ${CHART_SOURCE}"
if [[ "${CHART_SOURCE}" == oci://* ]]; then
  echo "Chart version: ${CHART_VERSION:-"(not set)"}"
fi
echo "PoundCake image: ${POUNDCAKE_IMAGE_REPO}:${POUNDCAKE_IMAGE_TAG}"
if [[ -n "${UI_IMAGE_REPO}" ]]; then
  echo "UI image repo override: ${UI_IMAGE_REPO}"
fi
if [[ -n "${BAKERY_IMAGE_REPO}" ]]; then
  echo "Bakery image repo override: ${BAKERY_IMAGE_REPO}"
fi
echo "Executing Helm command:"
printf '%q ' "${HELM_CMD[@]}" "${EXTRA_ARGS[@]}"
echo

"${HELM_CMD[@]}" "${EXTRA_ARGS[@]}"

#!/usr/bin/env bash

# Source this file:
#   source /Users/chris.breu/code/poundcake/install/set-env-helper.sh
#
# This config targets fork-based development by default.
# Update FORK_OWNER / tags as needed per release.

# -----------------------------------------------------------------------------
# Ownership / registry targeting
# -----------------------------------------------------------------------------
export FORK_OWNER="${FORK_OWNER:-rackerchris}"

# Optional OCI auth for private chart/image repos
export HELM_REGISTRY_USERNAME="${HELM_REGISTRY_USERNAME:-$FORK_OWNER}"
export HELM_REGISTRY_PASSWORD="${HELM_REGISTRY_PASSWORD:-}"

# -----------------------------------------------------------------------------
# Chart source selection
# -----------------------------------------------------------------------------
# Use OCI chart repo (recommended for release testing)
export POUNDCAKE_CHART_REPO="${POUNDCAKE_CHART_REPO:-oci://ghcr.io/${FORK_OWNER}/charts/poundcake}"

# Optional explicit chart version override. If empty, installer may read
# /etc/genestack/helm-chart-versions.yaml (key: poundcake) when present.
export POUNDCAKE_CHART_VERSION="${POUNDCAKE_CHART_VERSION:-}"
export POUNDCAKE_VERSION_FILE="${POUNDCAKE_VERSION_FILE:-/etc/genestack/helm-chart-versions.yaml}"

# Optional owner hint used by installer defaults
export POUNDCAKE_GHCR_OWNER="${POUNDCAKE_GHCR_OWNER:-$FORK_OWNER}"

# -----------------------------------------------------------------------------
# Image repositories/tags
# -----------------------------------------------------------------------------
export POUNDCAKE_IMAGE_REPO="${POUNDCAKE_IMAGE_REPO:-ghcr.io/${FORK_OWNER}/poundcake}"
export POUNDCAKE_IMAGE_TAG="${POUNDCAKE_IMAGE_TAG:-latest}"

# Passed through for compatibility with existing workflows.
export POUNDCAKE_UI_IMAGE_REPO="${POUNDCAKE_UI_IMAGE_REPO:-ghcr.io/${FORK_OWNER}/poundcake-ui}"
export POUNDCAKE_BAKERY_IMAGE_REPO="${POUNDCAKE_BAKERY_IMAGE_REPO:-ghcr.io/${FORK_OWNER}/poundcake-bakery}"

# StackStorm image controls
export POUNDCAKE_STACKSTORM_IMAGE_REPO="${POUNDCAKE_STACKSTORM_IMAGE_REPO:-stackstorm/st2}"
export POUNDCAKE_STACKSTORM_IMAGE_TAG="${POUNDCAKE_STACKSTORM_IMAGE_TAG:-3.9.0}"

# -----------------------------------------------------------------------------
# Release behavior
# -----------------------------------------------------------------------------
export POUNDCAKE_RELEASE_NAME="${POUNDCAKE_RELEASE_NAME:-poundcake}"
export POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-rackspace}"
export POUNDCAKE_HELM_TIMEOUT="${POUNDCAKE_HELM_TIMEOUT:-120m}"

# For troubleshooting, these are often set false.
export POUNDCAKE_HELM_WAIT="${POUNDCAKE_HELM_WAIT:-false}"
export POUNDCAKE_HELM_ATOMIC="${POUNDCAKE_HELM_ATOMIC:-false}"
export POUNDCAKE_HELM_CLEANUP_ON_FAIL="${POUNDCAKE_HELM_CLEANUP_ON_FAIL:-false}"

# Wrapper toggle from install/install-helm.sh
export POUNDCAKE_STACKSTORM_CHART_ENABLED="${POUNDCAKE_STACKSTORM_CHART_ENABLED:-true}"

# -----------------------------------------------------------------------------
# Optional profile switch examples
# -----------------------------------------------------------------------------
# Production defaults example:
#   export FORK_OWNER="rackerlabs"
#   export POUNDCAKE_CHART_REPO="oci://ghcr.io/rackerlabs/charts/poundcake"
#   export POUNDCAKE_IMAGE_REPO="ghcr.io/rackerlabs/poundcake"
#   export POUNDCAKE_UI_IMAGE_REPO="ghcr.io/rackerlabs/poundcake-ui"
#   export POUNDCAKE_BAKERY_IMAGE_REPO="ghcr.io/rackerlabs/poundcake-bakery"

echo "PoundCake env helper loaded."
echo "  Chart repo:  ${POUNDCAKE_CHART_REPO}"
echo "  Image repo:  ${POUNDCAKE_IMAGE_REPO}:${POUNDCAKE_IMAGE_TAG}"
echo "  Namespace:   ${POUNDCAKE_NAMESPACE}"
echo "  Release:     ${POUNDCAKE_RELEASE_NAME}"

#!/usr/bin/env bash

set -euo pipefail

HELM_TIMEOUT_DEFAULT="${HELM_TIMEOUT_DEFAULT:-120m}"

check_dependencies() {
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "Error: required command '$cmd' not found in PATH." >&2
      exit 1
    fi
  done
}

check_cluster_connection() {
  if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "Error: cannot connect to Kubernetes cluster (kubectl cluster-info failed)." >&2
    exit 1
  fi
}

perform_preflight_checks() {
  check_dependencies helm kubectl grep sed
  check_cluster_connection
}

ensure_oci_registry_auth() {
  local chart_ref=$1

  if [[ "$chart_ref" != oci://* ]]; then
    return 0
  fi

  local registry
  registry="${chart_ref#oci://}"
  registry="${registry%%/*}"

  local username="${HELM_REGISTRY_USERNAME:-${GHCR_USERNAME:-${GITHUB_ACTOR:-}}}"
  local password="${HELM_REGISTRY_PASSWORD:-${GHCR_TOKEN:-${CR_PAT:-${GITHUB_TOKEN:-}}}}"

  if [[ -n "$username" && -n "$password" ]]; then
    echo "Authenticating Helm OCI client to ${registry} as ${username}..."
    printf '%s' "$password" | helm registry login "$registry" -u "$username" --password-stdin >/dev/null
  elif [[ "$registry" == "ghcr.io" ]]; then
    echo "Note: ${chart_ref} is an OCI chart in GHCR. If it is private, export HELM_REGISTRY_USERNAME and HELM_REGISTRY_PASSWORD (or GHCR_USERNAME/GHCR_TOKEN)." >&2
  fi
}

get_chart_version() {
  local service=$1
  local version_file=$2

  if [[ -f "$version_file" ]]; then
    grep "^[[:space:]]*${service}:" "$version_file" | sed "s/.*${service}: *//" | head -n1
  fi
}

run_helm_validation() {
  local chart_ref=$1
  local version=$2
  local namespace=$3
  local release_name=$4
  shift 4
  local render_args=("$@")
  local lint_args=()
  local template_args=()
  local skip_next=0

  # helm lint does not support post-renderer flags; keep them for template only.
  for arg in "${render_args[@]}"; do
    template_args+=("$arg")

    if [[ $skip_next -eq 1 ]]; then
      skip_next=0
      continue
    fi

    if [[ "$arg" == "--post-renderer" || "$arg" == "--post-renderer-args" ]]; then
      skip_next=1
      continue
    fi

    lint_args+=("$arg")
  done

  local tmpdir
  tmpdir="$(mktemp -d)"

  # Pull chart locally for linting.
  helm pull "$chart_ref" --version "$version" --untar --untardir "$tmpdir"
  local chart_dir
  chart_dir="$(find "$tmpdir" -mindepth 1 -maxdepth 1 -type d | head -n1)"

  echo "Running helm lint..."
  helm lint "$chart_dir" "${lint_args[@]}"

  echo "Running helm template --debug..."
  helm template "$release_name" "$chart_ref" \
    --version "$version" \
    --namespace "$namespace" \
    "${template_args[@]}" \
    --debug >/dev/null

  rm -rf "$tmpdir"
}

rotate_chart_secrets() {
  local namespace=$1
  local release_name=$2

  # These are chart-owned/default secret names that are safe to rotate.
  local secrets=(
    "${release_name}-poundcake-admin"
    "${release_name}-poundcake-stackstorm"
    "${release_name}-stackstorm-ha-st2-apikeys"
    "st2-st2-apikeys"
    "st2-mongodb-secret"
    "st2-rabbitmq"
    "${release_name}-poundcake-mariadb-root"
    "${release_name}-poundcake-mariadb-user"
  )

  echo "Rotating selected chart-managed secrets (if present)..."
  for s in "${secrets[@]}"; do
    kubectl -n "$namespace" delete secret "$s" --ignore-not-found >/dev/null || true
  done
}

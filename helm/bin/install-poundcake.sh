#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

RELEASE_NAME="${POUNDCAKE_RELEASE_NAME:-poundcake}"
NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
HELM_TIMEOUT="${POUNDCAKE_HELM_TIMEOUT:-120m}"
HELM_WAIT="${POUNDCAKE_HELM_WAIT:-false}"
HELM_ATOMIC="${POUNDCAKE_HELM_ATOMIC:-false}"
HELM_CLEANUP_ON_FAIL="${POUNDCAKE_HELM_CLEANUP_ON_FAIL:-false}"
ALLOW_HOOK_WAIT="${POUNDCAKE_ALLOW_HOOK_WAIT:-false}"

CHART_REPO="${POUNDCAKE_CHART_REPO:-}"
CHART_VERSION="${POUNDCAKE_CHART_VERSION:-}"
VERSION_FILE="${POUNDCAKE_VERSION_FILE:-}"
HELM_REGISTRY_USERNAME="${HELM_REGISTRY_USERNAME:-}"
HELM_REGISTRY_PASSWORD="${HELM_REGISTRY_PASSWORD:-}"
IMAGE_PULL_SECRET_NAME="${POUNDCAKE_IMAGE_PULL_SECRET_NAME:-registry-creds}"
CREATE_IMAGE_PULL_SECRET="${POUNDCAKE_CREATE_IMAGE_PULL_SECRET:-true}"
IMAGE_PULL_SECRET_EMAIL="${POUNDCAKE_IMAGE_PULL_SECRET_EMAIL:-noreply@local}"

BASE_OVERRIDES="${POUNDCAKE_BASE_OVERRIDES:-/opt/poundcake/base-helm-configs/poundcake/poundcake-helm-overrides.yaml}"
GLOBAL_OVERRIDES_DIR="${POUNDCAKE_GLOBAL_OVERRIDES_DIR:-/etc/poundcake/helm-configs/global_overrides}"
SERVICE_CONFIG_DIR="${POUNDCAKE_SERVICE_CONFIG_DIR:-/etc/poundcake/helm-configs/poundcake}"
POST_RENDERER="${POUNDCAKE_HELM_POST_RENDERER:-/etc/poundcake/kustomize/kustomize.sh}"
POST_RENDERER_ARGS="${POUNDCAKE_HELM_POST_RENDERER_ARGS:-poundcake/overlay}"
POST_RENDERER_OVERLAY_DIR="${POUNDCAKE_HELM_POST_RENDERER_OVERLAY_DIR:-/etc/poundcake/kustomize/poundcake/overlay}"

VALIDATE="${POUNDCAKE_HELM_VALIDATE:-false}"
INSTALL_DEBUG="${POUNDCAKE_INSTALL_DEBUG:-false}"
OPERATOR_MODE="${POUNDCAKE_OPERATORS_MODE:-install-missing}"
MARIADB_OPERATOR_RELEASE_NAME="${POUNDCAKE_MARIADB_OPERATOR_RELEASE_NAME:-mariadb-operator}"
MARIADB_OPERATOR_CRDS_RELEASE_NAME="${POUNDCAKE_MARIADB_OPERATOR_CRDS_RELEASE_NAME:-mariadb-operator-crds}"
MARIADB_OPERATOR_NAMESPACE="${POUNDCAKE_MARIADB_OPERATOR_NAMESPACE:-mariadb-system}"
MARIADB_OPERATOR_CRDS_CHART_NAME="${POUNDCAKE_MARIADB_OPERATOR_CRDS_CHART_NAME:-mariadb-operator-crds}"
MARIADB_OPERATOR_CHART_NAME="${POUNDCAKE_MARIADB_OPERATOR_CHART_NAME:-mariadb-operator}"
MARIADB_OPERATOR_CHART_REPO_URL="${POUNDCAKE_MARIADB_OPERATOR_CHART_REPO_URL:-https://helm.mariadb.com/mariadb-operator}"
MARIADB_OPERATOR_VERSION="${POUNDCAKE_MARIADB_OPERATOR_CHART_VERSION:-0.38.1}"
RABBITMQ_OPERATOR_NAMESPACE="${POUNDCAKE_RABBITMQ_OPERATOR_NAMESPACE:-rabbitmq-system}"
RABBITMQ_CLUSTER_OPERATOR_MANIFEST_URL="${POUNDCAKE_RABBITMQ_CLUSTER_OPERATOR_MANIFEST_URL:-https://github.com/rabbitmq/cluster-operator/releases/download/v2.12.0/cluster-operator.yml}"
RABBITMQ_TOPOLOGY_OPERATOR_MANIFEST_URL="${POUNDCAKE_RABBITMQ_TOPOLOGY_OPERATOR_MANIFEST_URL:-https://github.com/rabbitmq/messaging-topology-operator/releases/download/v1.15.0/messaging-topology-operator-with-certmanager.yaml}"
MONGODB_OPERATOR_RELEASE_NAME="${POUNDCAKE_MONGODB_OPERATOR_RELEASE_NAME:-mongodb-community-operator}"
MONGODB_OPERATOR_NAMESPACE="${POUNDCAKE_MONGODB_OPERATOR_NAMESPACE:-mongodb-system}"
MONGODB_OPERATOR_CHART_NAME="${POUNDCAKE_MONGODB_OPERATOR_CHART_NAME:-community-operator}"
MONGODB_OPERATOR_CHART_REPO_URL="${POUNDCAKE_MONGODB_OPERATOR_CHART_REPO_URL:-https://mongodb.github.io/helm-charts}"
MONGODB_OPERATOR_VERSION="${POUNDCAKE_MONGODB_OPERATOR_CHART_VERSION:-0.13.0}"
SKIP_PREFLIGHT="false"
ROTATE_SECRETS="false"
CURRENT_PHASE="initialization"
EXTRA_ARGS=()
OVERRIDE_ARGS=()

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log_info() {
  printf '[%s] [INFO] %s\n' "$(timestamp_utc)" "$*"
}

log_warn() {
  printf '[%s] [WARN] %s\n' "$(timestamp_utc)" "$*" >&2
}

log_error() {
  printf '[%s] [ERROR] %s\n' "$(timestamp_utc)" "$*" >&2
}

log_phase() {
  CURRENT_PHASE="$*"
  log_info "Phase: ${CURRENT_PHASE}"
}

on_error() {
  local line_no="$1"
  local failed_command="$2"
  local exit_code="$3"
  trap - ERR
  log_error "Fatal: phase='${CURRENT_PHASE:-unknown}' line=${line_no} exit=${exit_code} cmd=${failed_command}"
  exit "${exit_code}"
}

trap 'on_error "$LINENO" "$BASH_COMMAND" "$?"' ERR

usage() {
  cat <<'USAGE_EOF'
Usage:
  install-poundcake.sh [installer options] [helm upgrade/install args]

Installer options:
  --debug           Enable shell tracing for installer execution
  --validate        Run helm lint + helm template --debug before install
  --operators-mode <install-missing|verify|skip>  Operator handling policy
  --verify-operators  Alias for --operators-mode verify
  --skip-operators    Alias for --operators-mode skip
  --skip-preflight  Skip dependency/cluster preflight checks
  --rotate-secrets  Delete known chart-managed secrets before install

Environment overrides:
  POUNDCAKE_CHART_REPO             (default: local chart at ./helm)
  POUNDCAKE_CHART_VERSION          (optional; for OCI repo installs)
  POUNDCAKE_VERSION_FILE           (optional; explicit chart versions file)
  POUNDCAKE_BASE_OVERRIDES         (optional; base values file)
  POUNDCAKE_GLOBAL_OVERRIDES_DIR   (optional; global values dir)
  POUNDCAKE_SERVICE_CONFIG_DIR     (optional; service values dir)
  POUNDCAKE_HELM_POST_RENDERER     (optional; post-renderer script path)
  POUNDCAKE_HELM_POST_RENDERER_ARGS (optional; post-renderer args)
  POUNDCAKE_HELM_POST_RENDERER_OVERLAY_DIR (optional; overlay path guard)
  POUNDCAKE_INSTALL_DEBUG         (default: false; same as --debug)
  POUNDCAKE_HELM_VALIDATE          (default: false; same as --validate)
  POUNDCAKE_OPERATORS_MODE         (default: install-missing; valid: install-missing, verify, skip)
  POUNDCAKE_MARIADB_OPERATOR_RELEASE_NAME
  POUNDCAKE_MARIADB_OPERATOR_CRDS_RELEASE_NAME
  POUNDCAKE_MARIADB_OPERATOR_NAMESPACE
  POUNDCAKE_MARIADB_OPERATOR_CRDS_CHART_NAME
  POUNDCAKE_MARIADB_OPERATOR_CHART_NAME
  POUNDCAKE_MARIADB_OPERATOR_CHART_REPO_URL
  POUNDCAKE_MARIADB_OPERATOR_CHART_VERSION
  POUNDCAKE_RABBITMQ_OPERATOR_NAMESPACE
  POUNDCAKE_RABBITMQ_CLUSTER_OPERATOR_MANIFEST_URL
  POUNDCAKE_RABBITMQ_TOPOLOGY_OPERATOR_MANIFEST_URL
  POUNDCAKE_MONGODB_OPERATOR_RELEASE_NAME
  POUNDCAKE_MONGODB_OPERATOR_NAMESPACE
  POUNDCAKE_MONGODB_OPERATOR_CHART_NAME
  POUNDCAKE_MONGODB_OPERATOR_CHART_REPO_URL
  POUNDCAKE_MONGODB_OPERATOR_CHART_VERSION
  HELM_REGISTRY_USERNAME           (optional; for OCI login)
  HELM_REGISTRY_PASSWORD           (optional; for OCI login)
  POUNDCAKE_IMAGE_PULL_SECRET_NAME     (default: registry-creds)
  POUNDCAKE_CREATE_IMAGE_PULL_SECRET   (default: true)
  POUNDCAKE_IMAGE_PULL_SECRET_EMAIL    (default: noreply@local)
  POUNDCAKE_RELEASE_NAME           (default: poundcake)
  POUNDCAKE_NAMESPACE              (default: poundcake)
  POUNDCAKE_HELM_TIMEOUT           (default: 120m)
  POUNDCAKE_HELM_WAIT              (default: false)
  POUNDCAKE_ALLOW_HOOK_WAIT        (default: false; required when forcing --wait/--atomic)
  POUNDCAKE_HELM_ATOMIC            (default: false)
  POUNDCAKE_HELM_CLEANUP_ON_FAIL   (default: false)

Image repositories/tags/digests:
  - Configure these in Helm values files or override files only.
  - Default active override dir: /etc/poundcake/helm-configs/poundcake/
  - Image env vars and image --set overrides are intentionally not supported.

Runtime deployment settings:
  - Configure shared DB mode and imagePullSecrets
    in Helm values files or override files only.

Examples:
  ./install/install-poundcake-helm.sh
  ./install/install-poundcake-helm.sh --validate
  ./install/install-poundcake-helm.sh --skip-preflight -f /path/to/values.yaml
USAGE_EOF
}

reject_image_override_args() {
  local arg=""
  local next_is_set_payload="false"

  for arg in "$@"; do
    if [[ "${next_is_set_payload}" == "true" ]]; then
      next_is_set_payload="false"
      case "${arg}" in
        poundcakeImage.*=*|uiImage.*=*)
          log_error "Image --set overrides are not supported by the Helm installers."
          log_error "Configure image repositories/tags/digests in values files or override files instead."
          exit 1
          ;;
      esac
      continue
    fi

    case "${arg}" in
      --set|--set-string|--set-json|--set-literal|--set-file)
        next_is_set_payload="true"
        ;;
      --set=*|--set-string=*|--set-json=*|--set-literal=*|--set-file=*)
        case "${arg}" in
          *poundcakeImage.*=*|*uiImage.*=*)
            log_error "Image --set overrides are not supported by the Helm installers."
            log_error "Configure image repositories/tags/digests in values files or override files instead."
            exit 1
            ;;
        esac
        ;;
    esac
  done
}

check_dependencies() {
  log_info "Checking required command dependencies..."
  local missing=0
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      log_error "Required command '$cmd' not found in PATH."
      missing=1
    fi
  done
  if [[ "$missing" -eq 1 ]]; then
    exit 1
  fi
}

check_cluster_connection() {
  log_info "Checking Kubernetes cluster connectivity..."
  if ! kubectl cluster-info >/dev/null 2>&1; then
    log_error "Cannot connect to Kubernetes cluster (kubectl cluster-info failed)."
    exit 1
  fi
}

perform_preflight_checks() {
  check_dependencies helm kubectl grep sed awk find sort
  check_cluster_connection
}

crd_exists() {
  local crd_name="$1"
  kubectl get crd "${crd_name}" >/dev/null 2>&1
}

crd_exists_any() {
  local crd_name=""
  for crd_name in "$@"; do
    if crd_exists "${crd_name}"; then
      return 0
    fi
  done
  return 1
}

resolve_operator_version_from_config() {
  local env_value="$1"
  local chart_key="$2"
  local fallback="$3"
  local resolved=""
  local candidate_files=()

  if [[ -n "${env_value}" ]]; then
    echo "${env_value}"
    return
  fi

  if [[ -n "${VERSION_FILE}" ]]; then
    candidate_files+=("${VERSION_FILE}")
  fi
  candidate_files+=(
    "/etc/poundcake/helm-chart-version.yaml"
    "/etc/poundcake/helm-chart-versions.yaml"
  )

  local candidate=""
  for candidate in "${candidate_files[@]}"; do
    [[ -f "${candidate}" ]] || continue
    resolved="$(get_chart_version_from_file "${candidate}" "${chart_key}")"
    if [[ -n "${resolved}" ]]; then
      echo "${resolved}"
      return
    fi
  done

  echo "${fallback}"
}

resolve_operator_versions() {
  MARIADB_OPERATOR_VERSION="$(resolve_operator_version_from_config "${POUNDCAKE_MARIADB_OPERATOR_CHART_VERSION:-}" "mariadb-operator" "${MARIADB_OPERATOR_VERSION}")"
  MONGODB_OPERATOR_VERSION="$(resolve_operator_version_from_config "${POUNDCAKE_MONGODB_OPERATOR_CHART_VERSION:-}" "mongodb-operator" "${MONGODB_OPERATOR_VERSION}")"
}

install_or_verify_helm_operator() {
  local operator_key="$1"
  local crd_names_raw="$2"
  local release_name="$3"
  local chart_name="$4"
  local chart_repo_url="$5"
  local chart_version="$6"
  local chart_namespace="$7"
  local crd_names=()
  local crd_name_display=""

  IFS=',' read -r -a crd_names <<< "${crd_names_raw}"
  crd_name_display="${crd_names[0]}"

  if crd_exists_any "${crd_names[@]}"; then
    log_info "Operator '${operator_key}' already present (CRD ${crd_name_display}); skipping install."
    return 0
  fi

  case "${OPERATOR_MODE}" in
    skip)
      log_info "Operator mode is skip. Not installing '${operator_key}' (missing CRD ${crd_name_display})."
      return 0
      ;;
    verify)
      log_error "Operator '${operator_key}' is missing (CRD ${crd_name_display})."
      log_error "Set --operators-mode install-missing to auto-install missing operators."
      exit 1
      ;;
    install-missing)
      log_info "Installing missing operator '${operator_key}' (chart ${chart_name}:${chart_version})..."
      helm upgrade --install "${release_name}" "${chart_name}" \
        --repo "${chart_repo_url}" \
        --version "${chart_version}" \
        --namespace "${chart_namespace}" \
        --create-namespace \
        --wait \
        --atomic \
        --cleanup-on-fail \
        --timeout "${HELM_TIMEOUT}"
      ;;
    *)
      log_error "Unsupported operators mode '${OPERATOR_MODE}'."
      exit 1
      ;;
  esac

  if ! crd_exists_any "${crd_names[@]}"; then
    log_error "Operator '${operator_key}' install completed but CRD ${crd_name_display} is still missing."
    exit 1
  fi
}

install_or_verify_manifest_operator() {
  local operator_key="$1"
  local crd_names_raw="$2"
  local manifest_url="$3"
  local check_namespace="$4"
  local check_deployment="$5"
  local crd_names=()
  local crd_name_display=""

  IFS=',' read -r -a crd_names <<< "${crd_names_raw}"
  crd_name_display="${crd_names[0]}"

  if crd_exists_any "${crd_names[@]}"; then
    log_info "Operator '${operator_key}' already present (CRD ${crd_name_display}); skipping install."
    return 0
  fi

  case "${OPERATOR_MODE}" in
    skip)
      log_info "Operator mode is skip. Not installing '${operator_key}' (missing CRD ${crd_name_display})."
      return 0
      ;;
    verify)
      log_error "Operator '${operator_key}' is missing (CRD ${crd_name_display})."
      log_error "Set --operators-mode install-missing to auto-install missing operators."
      exit 1
      ;;
    install-missing)
      log_info "Installing missing operator '${operator_key}' from ${manifest_url}"
      kubectl apply -f "${manifest_url}" >/dev/null
      if [[ -n "${check_deployment}" ]]; then
        kubectl -n "${check_namespace}" wait --timeout=5m "deployments.apps/${check_deployment}" --for=condition=available >/dev/null
      fi
      ;;
    *)
      log_error "Unsupported operators mode '${OPERATOR_MODE}'."
      exit 1
      ;;
  esac

  if ! crd_exists_any "${crd_names[@]}"; then
    log_error "Operator '${operator_key}' install completed but CRD ${crd_name_display} is still missing."
    exit 1
  fi
}

install_or_verify_mariadb_operator() {
  local crd_name="mariadbs.k8s.mariadb.com"
  if crd_exists "${crd_name}"; then
    log_info "Operator 'mariadb-operator' already present (CRD ${crd_name}); skipping install."
    return 0
  fi

  case "${OPERATOR_MODE}" in
    skip)
      log_info "Operator mode is skip. Not installing 'mariadb-operator' (missing CRD ${crd_name})."
      return 0
      ;;
    verify)
      log_error "Operator 'mariadb-operator' is missing (CRD ${crd_name})."
      log_error "Set --operators-mode install-missing to auto-install missing operators."
      exit 1
      ;;
    install-missing)
      log_info "Installing missing operator 'mariadb-operator' CRDs and controller (version ${MARIADB_OPERATOR_VERSION})..."
      helm upgrade --install "${MARIADB_OPERATOR_CRDS_RELEASE_NAME}" "${MARIADB_OPERATOR_CRDS_CHART_NAME}" \
        --repo "${MARIADB_OPERATOR_CHART_REPO_URL}" \
        --version "${MARIADB_OPERATOR_VERSION}" \
        --namespace "${MARIADB_OPERATOR_NAMESPACE}" \
        --create-namespace \
        --wait \
        --atomic \
        --cleanup-on-fail \
        --timeout "${HELM_TIMEOUT}"
      helm upgrade --install "${MARIADB_OPERATOR_RELEASE_NAME}" "${MARIADB_OPERATOR_CHART_NAME}" \
        --repo "${MARIADB_OPERATOR_CHART_REPO_URL}" \
        --version "${MARIADB_OPERATOR_VERSION}" \
        --namespace "${MARIADB_OPERATOR_NAMESPACE}" \
        --create-namespace \
        --wait \
        --atomic \
        --cleanup-on-fail \
        --timeout "${HELM_TIMEOUT}"
      ;;
    *)
      log_error "Unsupported operators mode '${OPERATOR_MODE}'."
      exit 1
      ;;
  esac

  if ! crd_exists "${crd_name}"; then
    log_error "Operator 'mariadb-operator' install completed but CRD ${crd_name} is still missing."
    exit 1
  fi
}

ensure_required_operators() {
  log_info "Operator mode: ${OPERATOR_MODE}"

  if [[ "${OPERATOR_MODE}" == "skip" ]]; then
    log_info "Skipping operator checks/installs."
    return 0
  fi

  resolve_operator_versions

  install_or_verify_mariadb_operator

  install_or_verify_manifest_operator \
    "rabbitmq-cluster-operator" \
    "rabbitmqclusters.rabbitmq.com" \
    "${RABBITMQ_CLUSTER_OPERATOR_MANIFEST_URL}" \
    "${RABBITMQ_OPERATOR_NAMESPACE}" \
    "rabbitmq-cluster-operator"

  install_or_verify_manifest_operator \
    "rabbitmq-topology-operator" \
    "queues.rabbitmq.com" \
    "${RABBITMQ_TOPOLOGY_OPERATOR_MANIFEST_URL}" \
    "${RABBITMQ_OPERATOR_NAMESPACE}" \
    "messaging-topology-operator"

  install_or_verify_helm_operator \
    "mongodb-community-operator" \
    "mongodbcommunity.mongodbcommunity.mongodb.com" \
    "${MONGODB_OPERATOR_RELEASE_NAME}" \
    "${MONGODB_OPERATOR_CHART_NAME}" \
    "${MONGODB_OPERATOR_CHART_REPO_URL}" \
    "${MONGODB_OPERATOR_VERSION}" \
    "${MONGODB_OPERATOR_NAMESPACE}"
}

ensure_oci_registry_auth() {
  local chart_source="$1"
  if [[ "${chart_source}" != oci://* ]]; then
    return
  fi

  local registry_host
  registry_host="$(echo "${chart_source}" | sed -E 's#^oci://([^/]+)/.*#\1#')"

  local username password
  username="${HELM_REGISTRY_USERNAME:-${GHCR_USERNAME:-${GITHUB_ACTOR:-}}}"
  password="${HELM_REGISTRY_PASSWORD:-${GHCR_TOKEN:-${CR_PAT:-${GITHUB_TOKEN:-}}}}"

  if [[ -n "${username}" && -n "${password}" ]]; then
    log_info "Authenticating Helm OCI client to ${registry_host} as ${username}..."
    printf '%s' "${password}" | helm registry login "${registry_host}" -u "${username}" --password-stdin >/dev/null
  else
    log_warn "${chart_source} is OCI-based. Set HELM_REGISTRY_USERNAME and HELM_REGISTRY_PASSWORD for private charts."
  fi
}

collect_yaml_files() {
  local directory="$1"
  if [[ -d "${directory}" ]]; then
    find "${directory}" -maxdepth 1 -type f -name '*.yaml' | LC_ALL=C sort
  fi
  return 0
}

discover_override_args() {
  OVERRIDE_ARGS=()
  if [[ -n "${BASE_OVERRIDES}" && -f "${BASE_OVERRIDES}" ]]; then
    OVERRIDE_ARGS+=("-f" "${BASE_OVERRIDES}")
  fi

  while IFS= read -r yaml_file; do
    [[ -n "${yaml_file}" ]] || continue
    OVERRIDE_ARGS+=("-f" "${yaml_file}")
  done < <(collect_yaml_files "${GLOBAL_OVERRIDES_DIR}")

  while IFS= read -r yaml_file; do
    [[ -n "${yaml_file}" ]] || continue
    OVERRIDE_ARGS+=("-f" "${yaml_file}")
  done < <(collect_yaml_files "${SERVICE_CONFIG_DIR}")
}

ensure_namespace_exists() {
  local reason="$1"
  if ! kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    log_info "Namespace '${NAMESPACE}' does not exist; creating it for ${reason}..."
    kubectl create namespace "${NAMESPACE}" >/dev/null
  fi
}

get_chart_version_from_file() {
  local version_file="$1"
  local chart_name="$2"

  awk -v chart="${chart_name}" '
    BEGIN { in_charts = 0 }
    /^[[:space:]]*charts:[[:space:]]*$/ { in_charts = 1; next }
    in_charts == 1 {
      if ($0 ~ /^[^[:space:]]/) { in_charts = 0; next }
      line = $0
      sub(/^[[:space:]]+/, "", line)
      if (line ~ ("^" chart ":[[:space:]]*")) {
        sub("^" chart ":[[:space:]]*", "", line)
        gsub(/[[:space:]]*$/, "", line)
        print line
        exit
      }
    }
  ' "${version_file}" | head -n1
}

resolve_chart_version() {
  if [[ -n "${CHART_VERSION}" ]]; then
    log_info "Using chart version from POUNDCAKE_CHART_VERSION: ${CHART_VERSION}"
    return
  fi

  local candidate_files=()
  if [[ -n "${VERSION_FILE}" ]]; then
    candidate_files+=("${VERSION_FILE}")
  fi
  candidate_files+=(
    "/etc/poundcake/helm-chart-version.yaml"
    "/etc/poundcake/helm-chart-versions.yaml"
  )

  local candidate
  for candidate in "${candidate_files[@]}"; do
    [[ -f "${candidate}" ]] || continue

    log_info "Inspecting chart version file: ${candidate}"
    CHART_VERSION="$(get_chart_version_from_file "${candidate}" "poundcake")"
    if [[ -z "${CHART_VERSION}" ]]; then
      local matched_line=""
      matched_line="$(grep -E '^[[:space:]]*poundcake:[[:space:]]*' "${candidate}" | head -n1 || true)"
      if [[ -n "${matched_line}" ]]; then
        CHART_VERSION="${matched_line#*:}"
        CHART_VERSION="$(echo "${CHART_VERSION}" | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//')"
      else
        log_warn "No poundcake version entry in ${candidate}; continuing without explicit --version."
      fi
    fi

    if [[ -n "${CHART_VERSION}" ]]; then
      log_info "Resolved chart version '${CHART_VERSION}' from ${candidate}"
      return
    fi
  done

  log_info "No chart version override resolved from configured version files."
}

run_helm_validation() {
  local chart_source="$1"
  local namespace="$2"
  local release_name="$3"
  shift 3
  local template_args=("$@")
  local lint_args=()

  local i=0
  while [[ $i -lt ${#template_args[@]} ]]; do
    local arg="${template_args[$i]}"
    if [[ "${arg}" == "--post-renderer" || "${arg}" == "--post-renderer-args" || "${arg}" == "--namespace" || "${arg}" == "--timeout" || "${arg}" == "--version" ]]; then
      ((i+=2))
      continue
    fi
    if [[ "${arg}" == "--create-namespace" || "${arg}" == "--wait" || "${arg}" == "--atomic" || "${arg}" == "--cleanup-on-fail" ]]; then
      ((i+=1))
      continue
    fi
    lint_args+=("${arg}")
    ((i+=1))
  done

  local lint_chart_source="${chart_source}"
  local tmpdir=""
  if [[ "${chart_source}" == oci://* ]]; then
    tmpdir="$(mktemp -d)"
    local pull_cmd=(helm pull "${chart_source}" --untar --untardir "${tmpdir}")
    if [[ -n "${CHART_VERSION}" ]]; then
      pull_cmd+=(--version "${CHART_VERSION}")
    fi
    "${pull_cmd[@]}"
    lint_chart_source="$(find "${tmpdir}" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  fi

  log_info "Running helm lint..."
  helm lint "${lint_chart_source}" "${lint_args[@]}"

  log_info "Running helm template --debug..."
  helm template "${release_name}" "${chart_source}" \
    --namespace "${namespace}" \
    "${template_args[@]}" \
    --debug >/dev/null

  if [[ -n "${tmpdir}" ]]; then
    rm -rf "${tmpdir}"
  fi
}

rotate_chart_secrets() {
  local namespace="$1"
  local release_name="$2"

  local secrets=(
    "${release_name}-poundcake-admin"
    "${release_name}-poundcake-mariadb-root"
    "${release_name}-poundcake-mariadb-user"
  )

  log_info "Rotating selected chart-managed secrets (if present)..."
  local s
  for s in "${secrets[@]}"; do
    kubectl -n "${namespace}" delete secret "${s}" --ignore-not-found >/dev/null || true
  done
}

verify_rendered_endpoint_contract() {
  local rendered_manifest="$1"

  if ! awk '
    BEGIN { kind=""; name=""; inmeta=0; want=0; probe=""; ready=0; live=0 }
    /^kind:[[:space:]]+/ { kind=$2; next }
    /^metadata:[[:space:]]*$/ { inmeta=1; next }
    inmeta && /^  name:[[:space:]]+/ {
      name=$2
      inmeta=0
      if (kind=="Deployment" && name=="poundcake-api") {
        want=1
      }
      next
    }
    want && /^[[:space:]]+readinessProbe:[[:space:]]*$/ { probe="ready"; next }
    want && /^[[:space:]]+livenessProbe:[[:space:]]*$/ { probe="live"; next }
    want && probe=="ready" && /^[[:space:]]+path:[[:space:]]*\/readyz[[:space:]]*$/ { ready=1; next }
    want && probe=="live" && /^[[:space:]]+path:[[:space:]]*\/livez[[:space:]]*$/ { live=1; next }
    /^---[[:space:]]*$/ { kind=""; name=""; inmeta=0; want=0; probe="" }
    END { exit((ready && live) ? 0 : 1) }
  ' "${rendered_manifest}"; then
    log_error "Rendered manifest contract failed: poundcake-api probes must target /readyz and /livez."
    exit 1
  fi
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

log_phase "argument parsing"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug)
      INSTALL_DEBUG="true"
      shift
      ;;
    --validate)
      VALIDATE="true"
      shift
      ;;
    --operators-mode)
      OPERATOR_MODE="$2"
      shift 2
      ;;
    --skip-operators)
      OPERATOR_MODE="skip"
      shift
      ;;
    --verify-operators)
      OPERATOR_MODE="verify"
      shift
      ;;
    --skip-preflight)
      SKIP_PREFLIGHT="true"
      shift
      ;;
    --rotate-secrets)
      ROTATE_SECRETS="true"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

if (( ${#EXTRA_ARGS[@]} > 0 )); then
  reject_image_override_args "${EXTRA_ARGS[@]}"
fi

if [[ "${OPERATOR_MODE}" != "install-missing" && "${OPERATOR_MODE}" != "verify" && "${OPERATOR_MODE}" != "skip" ]]; then
  log_error "Invalid operators mode '${OPERATOR_MODE}'. Valid values: install-missing, verify, skip."
  exit 1
fi

if [[ -n "${POUNDCAKE_INSTALL_PROFILE:-}" && "${POUNDCAKE_INSTALL_PROFILE}" != "poundcake" ]]; then
  log_error "Invalid installer profile '${POUNDCAKE_INSTALL_PROFILE}'."
  log_error "This repo supports only the 'poundcake' installer profile."
  exit 1
fi

if [[ "${INSTALL_DEBUG}" == "true" ]]; then
  log_info "Debug tracing enabled."
  PS4='+ [${BASH_SOURCE##*/}:${LINENO}${FUNCNAME[0]:+:${FUNCNAME[0]}}] '
  set -x
fi

log_info "Installer options: operators_mode=${OPERATOR_MODE}, validate=${VALIDATE}, skip_preflight=${SKIP_PREFLIGHT}, rotate_secrets=${ROTATE_SECRETS}, debug=${INSTALL_DEBUG}"

log_phase "preflight checks"
if [[ "${SKIP_PREFLIGHT}" != "true" ]]; then
  perform_preflight_checks
else
  log_info "Skipping preflight checks (--skip-preflight)."
fi

if [[ "${CREATE_IMAGE_PULL_SECRET}" == "true" ]] && ! command -v kubectl >/dev/null 2>&1; then
  log_error "kubectl is required when POUNDCAKE_CREATE_IMAGE_PULL_SECRET=true."
  exit 1
fi

log_phase "chart source and version resolution"
if [[ -z "${CHART_REPO}" ]]; then
  CHART_SOURCE="${CHART_DIR}"
else
  CHART_SOURCE="${CHART_REPO}"
fi

resolve_chart_version

log_phase "operator verification/install"
ensure_required_operators

log_phase "oci authentication"
ensure_oci_registry_auth "${CHART_SOURCE}"

wait_requested="false"
if [[ "${HELM_WAIT}" == "true" || "${HELM_ATOMIC}" == "true" ]]; then
  wait_requested="true"
fi
if (( ${#EXTRA_ARGS[@]} )); then
  for arg in "${EXTRA_ARGS[@]}"; do
    if [[ "${arg}" == "--wait" || "${arg}" == "--atomic" ]]; then
      wait_requested="true"
      break
    fi
  done
fi

if [[ "${wait_requested}" == "true" && "${ALLOW_HOOK_WAIT}" != "true" ]]; then
  log_error "Startup jobs are Helm post-install/post-upgrade hooks and workload init containers are marker-gated."
  log_error "Using --wait/--atomic can deadlock before hook jobs run, so jobs may never be created."
  log_error "Re-run with POUNDCAKE_HELM_WAIT=false (recommended)."
  log_error "If you intentionally want wait semantics, set POUNDCAKE_ALLOW_HOOK_WAIT=true."
  exit 1
fi

if [[ "${CREATE_IMAGE_PULL_SECRET}" == "true" && -z "${IMAGE_PULL_SECRET_NAME}" ]]; then
  log_error "POUNDCAKE_CREATE_IMAGE_PULL_SECRET=true requires POUNDCAKE_IMAGE_PULL_SECRET_NAME."
  exit 1
fi

log_phase "image pull secret configuration"
if [[ "${CREATE_IMAGE_PULL_SECRET}" == "true" ]]; then
  if [[ -z "${HELM_REGISTRY_USERNAME}" || -z "${HELM_REGISTRY_PASSWORD}" ]]; then
    log_error "POUNDCAKE_CREATE_IMAGE_PULL_SECRET=true requires HELM_REGISTRY_USERNAME and HELM_REGISTRY_PASSWORD."
    exit 1
  fi
  if ! kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    log_info "Namespace '${NAMESPACE}' does not exist; creating it for image pull secret setup..."
    kubectl create namespace "${NAMESPACE}"
  fi
  log_info "Creating/updating image pull secret '${IMAGE_PULL_SECRET_NAME}' in namespace '${NAMESPACE}'..."
  kubectl -n "${NAMESPACE}" create secret docker-registry "${IMAGE_PULL_SECRET_NAME}" \
    --docker-server=ghcr.io \
    --docker-username="${HELM_REGISTRY_USERNAME}" \
    --docker-password="${HELM_REGISTRY_PASSWORD}" \
    --docker-email="${IMAGE_PULL_SECRET_EMAIL}" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  log_info "Skipping image pull secret creation (POUNDCAKE_CREATE_IMAGE_PULL_SECRET=${CREATE_IMAGE_PULL_SECRET})."
fi

log_phase "values file discovery"
discover_override_args
log_info "Resolved override file argument count: ${#OVERRIDE_ARGS[@]}"

if [[ "${ROTATE_SECRETS}" == "true" ]]; then
  log_phase "secret rotation"
  rotate_chart_secrets "${NAMESPACE}" "${RELEASE_NAME}"
fi

POST_RENDER_ARGS=()
if [[ -f "${POST_RENDERER}" && -d "${POST_RENDERER_OVERLAY_DIR}" ]]; then
  POST_RENDER_ARGS+=("--post-renderer" "${POST_RENDERER}")
  if [[ -n "${POST_RENDERER_ARGS}" ]]; then
    POST_RENDER_ARGS+=("--post-renderer-args" "${POST_RENDERER_ARGS}")
  fi
fi

INSTALLER_SET_ARGS=(--set "poundcake.enabled=true")

COMMON_HELM_ARGS=(
  --namespace "${NAMESPACE}"
  --timeout "${HELM_TIMEOUT}"
)

if (( ${#OVERRIDE_ARGS[@]} )); then
  COMMON_HELM_ARGS+=("${OVERRIDE_ARGS[@]}")
fi
if (( ${#POST_RENDER_ARGS[@]} )); then
  COMMON_HELM_ARGS+=("${POST_RENDER_ARGS[@]}")
fi
if (( ${#INSTALLER_SET_ARGS[@]} )); then
  COMMON_HELM_ARGS+=("${INSTALLER_SET_ARGS[@]}")
fi

if [[ -n "${CHART_VERSION}" ]]; then
  COMMON_HELM_ARGS+=(--version "${CHART_VERSION}")
fi

if [[ "${VALIDATE}" == "true" ]]; then
  log_phase "helm validation"
  if (( ${#EXTRA_ARGS[@]} )); then
    run_helm_validation "${CHART_SOURCE}" "${NAMESPACE}" "${RELEASE_NAME}" \
      "${COMMON_HELM_ARGS[@]}" \
      "${EXTRA_ARGS[@]}"
  else
    run_helm_validation "${CHART_SOURCE}" "${NAMESPACE}" "${RELEASE_NAME}" \
      "${COMMON_HELM_ARGS[@]}"
  fi
fi

log_phase "helm command assembly"
HELM_CMD=(
  helm upgrade --install "${RELEASE_NAME}" "${CHART_SOURCE}"
  --create-namespace
  "${COMMON_HELM_ARGS[@]}"
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

log_phase "rendered manifest contract verification"
TEMPLATE_ARGS=()
if (( ${#EXTRA_ARGS[@]} )); then
  for arg in "${EXTRA_ARGS[@]}"; do
    case "${arg}" in
      --wait|--atomic|--cleanup-on-fail|--create-namespace)
        continue
        ;;
    esac
    TEMPLATE_ARGS+=("${arg}")
  done
fi

RENDERED_MANIFEST="$(mktemp)"
HELM_TEMPLATE_CMD=(
  helm template "${RELEASE_NAME}" "${CHART_SOURCE}"
  "${COMMON_HELM_ARGS[@]}"
)

if (( ${#TEMPLATE_ARGS[@]} )); then
  "${HELM_TEMPLATE_CMD[@]}" "${TEMPLATE_ARGS[@]}" > "${RENDERED_MANIFEST}"
else
  "${HELM_TEMPLATE_CMD[@]}" > "${RENDERED_MANIFEST}"
fi

verify_rendered_endpoint_contract "${RENDERED_MANIFEST}"
rm -f "${RENDERED_MANIFEST}"

log_phase "helm install execution"
log_info "Installing PoundCake release: ${RELEASE_NAME}"
log_info "Namespace: ${NAMESPACE}"
log_info "PoundCake resources: enabled"
log_info "Chart source: ${CHART_SOURCE}"
if [[ "${CHART_SOURCE}" == oci://* ]]; then
  log_info "Chart version: ${CHART_VERSION:-"(not set)"}"
fi
log_info "Image refs: values files / override files"
log_info "Runtime config source: values files / override files"
if [[ "${CREATE_IMAGE_PULL_SECRET}" == "true" ]]; then
  log_info "Image pull secret management: create/update '${IMAGE_PULL_SECRET_NAME}'"
else
  log_info "Image pull secret management: skipped"
fi

log_info "Executing Helm command:"
printf '%q ' "${HELM_CMD[@]}"
if (( ${#EXTRA_ARGS[@]} )); then
  printf '%q ' "${EXTRA_ARGS[@]}"
fi
echo

if (( ${#EXTRA_ARGS[@]} )); then
  "${HELM_CMD[@]}" "${EXTRA_ARGS[@]}"
else
  "${HELM_CMD[@]}"
fi

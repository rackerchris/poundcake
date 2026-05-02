#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEVSTACK_VALUES="${PROJECT_ROOT}/helm/devstack/values/prometheus-kind.yaml"
DEVSTACK_INSTALLER="${PROJECT_ROOT}/helm/devstack/install-prometheus.sh"
CHART="${PROMETHEUS_CHART:-prometheus-community/kube-prometheus-stack}"
CHART_VERSION="${PROMETHEUS_CHART_VERSION:-84.5.0}"

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! rg -Fq -- "${needle}" "${file}"; then
    echo "Expected to find: ${needle}" >&2
    echo "In file: ${file}" >&2
    fail "missing expected content"
  fi
}

assert_not_contains_regex() {
  local pattern="$1"
  local file="$2"
  if rg -q -- "${pattern}" "${file}"; then
    echo "Did not expect regex: ${pattern}" >&2
    echo "In file: ${file}" >&2
    fail "unexpected content present"
  fi
}

decode_alertmanager_config() {
  local rendered="$1"
  local output="$2"
  local encoded

  encoded="$(sed -n 's/^  alertmanager.yaml: "\(.*\)"$/\1/p' "${rendered}" | head -n 1)"
  [ -n "${encoded}" ] || fail "rendered Alertmanager secret did not include alertmanager.yaml"

  if printf '%s' "${encoded}" | base64 --decode >"${output}" 2>/dev/null; then
    return 0
  fi

  printf '%s' "${encoded}" | base64 -D >"${output}"
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
RENDERED="${TMP_DIR}/kube-prometheus-stack.yaml"
ALERTMANAGER_CONFIG="${TMP_DIR}/alertmanager.yaml"
RENDERED_CUSTOM_NAMESPACE="${TMP_DIR}/kube-prometheus-stack-custom-namespace.yaml"
ALERTMANAGER_CONFIG_CUSTOM_NAMESPACE="${TMP_DIR}/alertmanager-custom-namespace.yaml"

echo "Checking devstack Prometheus chart source..."
assert_contains 'PROMETHEUS_CHART="${PROMETHEUS_CHART:-prometheus-community/kube-prometheus-stack}"' "${DEVSTACK_INSTALLER}"
assert_contains 'PROMETHEUS_CRDS_CHART="${PROMETHEUS_CRDS_CHART:-prometheus-community/prometheus-operator-crds}"' "${DEVSTACK_INSTALLER}"
assert_contains '--set-string "poundcakeWebhook.namespace=$POUNDCAKE_NAMESPACE"' "${DEVSTACK_INSTALLER}"

echo "Checking devstack Prometheus values..."
assert_contains "defaultRules:" "${DEVSTACK_VALUES}"
assert_contains "  create: false" "${DEVSTACK_VALUES}"
assert_contains "additionalPrometheusRulesMap: {}" "${DEVSTACK_VALUES}"
assert_contains "ruleSelectorNilUsesHelmValues: false" "${DEVSTACK_VALUES}"
assert_contains "        managed-by: poundcake" "${DEVSTACK_VALUES}"
assert_contains "poundcakeWebhook:" "${DEVSTACK_VALUES}"
assert_contains "  namespace: poundcake" "${DEVSTACK_VALUES}"
assert_contains "  secretName: poundcake-alertmanager-webhook" "${DEVSTACK_VALUES}"

echo "Rendering kube-prometheus-stack devstack profile..."
helm template poundcake-prometheus "${CHART}" \
  --version "${CHART_VERSION}" \
  --namespace monitoring \
  -f "${DEVSTACK_VALUES}" >"${RENDERED}"
decode_alertmanager_config "${RENDERED}" "${ALERTMANAGER_CONFIG}"

echo "Checking rendered Prometheus rule loading contract..."
assert_contains "kind: Prometheus" "${RENDERED}"
assert_contains "  ruleSelector:" "${RENDERED}"
assert_contains "      managed-by: poundcake" "${RENDERED}"
assert_not_contains_regex '^kind: PrometheusRule$' "${RENDERED}"

echo "Checking rendered Alertmanager PoundCake receiver contract..."
assert_contains "  receiver: poundcake-webhook" "${ALERTMANAGER_CONFIG}"
assert_contains "  - name: poundcake-webhook" "${ALERTMANAGER_CONFIG}"
assert_contains "    webhook_configs:" "${ALERTMANAGER_CONFIG}"
assert_contains "      - url: http://poundcake-api.poundcake.svc.cluster.local:8000/api/v1/webhook" "${ALERTMANAGER_CONFIG}"
assert_contains "          credentials_file: /etc/alertmanager/secrets/poundcake-alertmanager-webhook/webhook-bearer-token" "${ALERTMANAGER_CONFIG}"
assert_not_contains_regex 'default-receiver' "${ALERTMANAGER_CONFIG}"
assert_contains "  secrets:" "${RENDERED}"
assert_contains "    - poundcake-alertmanager-webhook" "${RENDERED}"

echo "Checking rendered Alertmanager Genestack inhibition contract..."
assert_contains "inhibit_rules:" "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name = "node-host-down"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name = "kube-node-not-ready"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name = "kube-pod-container-restarts"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^etcd-.+$"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^(kube-.+|kubelet-.+|cpu-throttling-high)$"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^node-.+$"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^openstack-.+$"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^(alertmanager|config-reloaders|prometheus|prometheus-operator).+$|^target-down$"' "${ALERTMANAGER_CONFIG}"
assert_contains 'group_name =~ "^rabbitmq-.+$"' "${ALERTMANAGER_CONFIG}"

echo "Checking PoundCake receiver namespace override at Alertmanager creation..."
helm template poundcake-prometheus "${CHART}" \
  --version "${CHART_VERSION}" \
  --namespace monitoring \
  -f "${DEVSTACK_VALUES}" \
  --set-string poundcakeWebhook.namespace=custom-poundcake >"${RENDERED_CUSTOM_NAMESPACE}"
decode_alertmanager_config "${RENDERED_CUSTOM_NAMESPACE}" "${ALERTMANAGER_CONFIG_CUSTOM_NAMESPACE}"
assert_contains "      - url: http://poundcake-api.custom-poundcake.svc.cluster.local:8000/api/v1/webhook" "${ALERTMANAGER_CONFIG_CUSTOM_NAMESPACE}"

echo "[PASS] Devstack Prometheus wires PoundCake Alertmanager receiver without auto-loaded alerts"

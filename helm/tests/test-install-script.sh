#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="${SCRIPT_DIR}/../../install/install-poundcake-helm.sh"
INSTALLER="${SCRIPT_DIR}/../bin/install-poundcake.sh"

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
    echo "--- file contents ---" >&2
    cat "${file}" >&2 || true
    echo "---------------------" >&2
    fail "missing expected content"
  fi
}

assert_not_contains() {
  local needle="$1"
  local file="$2"
  if rg -Fq -- "${needle}" "${file}"; then
    echo "Did not expect to find: ${needle}" >&2
    echo "In file: ${file}" >&2
    echo "--- file contents ---" >&2
    cat "${file}" >&2 || true
    echo "---------------------" >&2
    fail "unexpected content present"
  fi
}

echo "Checking PoundCake installer wrapper..."
[[ -x "${WRAPPER}" ]] || fail "missing ${WRAPPER}"
assert_contains 'exec "$PROJECT_ROOT/helm/bin/install-poundcake.sh" "$@"' "${WRAPPER}"

echo "Checking rendered manifest probe contract..."
[[ -x "${INSTALLER}" ]] || fail "missing ${INSTALLER}"
assert_contains '/readyz' "${INSTALLER}"
assert_contains '/livez' "${INSTALLER}"
assert_not_contains '/api/v1/health.' "${INSTALLER}"

echo "[PASS] PoundCake install wrapper checks passed"

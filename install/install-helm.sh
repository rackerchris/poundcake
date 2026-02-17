#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

STACKSTORM_CHART_ENABLED="${POUNDCAKE_STACKSTORM_CHART_ENABLED:-true}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec "$PROJECT_ROOT/helm/bin/install-poundcake.sh" "$@"
fi

exec "$PROJECT_ROOT/helm/bin/install-poundcake.sh" \
  --set "stackstorm.chart.enabled=${STACKSTORM_CHART_ENABLED}" \
  "$@"

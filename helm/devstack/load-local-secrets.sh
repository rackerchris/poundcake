#!/usr/bin/env bash
# Load local-only Helm devstack secrets when present.

set -euo pipefail

DEVSTACK_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVSTACK_SECRETS_FILE_DEFAULT="${DEVSTACK_HELPER_DIR}/.devstack-secrets.sh"
DEVSTACK_SECRETS_FILE="${DEVSTACK_SECRETS_FILE:-$DEVSTACK_SECRETS_FILE_DEFAULT}"

if [ -f "$DEVSTACK_SECRETS_FILE" ]; then
    # shellcheck source=/dev/null
    . "$DEVSTACK_SECRETS_FILE"
fi

export DEVSTACK_SECRETS_FILE

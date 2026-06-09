#!/usr/bin/env bash
# Refresh the local-only Helm devstack secrets file from current devstack state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# shellcheck source=/dev/null
. "$SCRIPT_DIR/load-local-secrets.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
STACKSTORM_NAMESPACE="${STACKSTORM_NAMESPACE:-stackstorm}"
POUNDCAKE_ADMIN_SECRET="${POUNDCAKE_ADMIN_SECRET:-poundcake-admin}"
POUNDCAKE_WEBHOOK_SECRET="${POUNDCAKE_WEBHOOK_SECRET:-poundcake-secrets}"
POUNDCAKE_WEBHOOK_FIELD="${POUNDCAKE_WEBHOOK_FIELD:-WEBHOOK_BEARER_TOKEN}"
POUNDCAKE_DB_ROOT_FIELD="${POUNDCAKE_DB_ROOT_FIELD:-DB_ROOT_PASSWORD}"
STACKSTORM_API_KEY_SECRET="${STACKSTORM_API_KEY_SECRET:-stackstorm-apikeys}"
STACKSTORM_API_KEY_FIELD="${STACKSTORM_API_KEY_FIELD:-st2_api_key}"

log() {
    printf '[helm-devstack-refresh-secrets] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-refresh-secrets] ERROR: %s\n' "$*" >&2
    exit 1
}

detect_executable() {
    local env_var="$1"
    local command_name="$2"
    shift 2
    local configured="${!env_var:-}"
    local candidate

    if [ -n "$configured" ]; then
        [ -x "$configured" ] || fail "$env_var is set but not executable: $configured"
        printf '%s\n' "$configured"
        return 0
    fi

    if command -v "$command_name" >/dev/null 2>&1; then
        command -v "$command_name"
        return 0
    fi

    for candidate in "$@"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    fail "$command_name is not installed or not in PATH"
}

decode_b64() {
    if base64 --help 2>&1 | grep -q -- '--decode'; then
        base64 --decode
    else
        base64 -D
    fi
}

read_secret_value() {
    local namespace="$1"
    local secret_name="$2"
    local key="$3"
    "$KUBECTL_BIN" -n "$namespace" get secret "$secret_name" \
        -o "jsonpath={.data.${key}}" 2>/dev/null | decode_b64
}

shell_quote() {
    "$PYTHON_BIN" -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$1"
}

AUTH_USERNAME_CURRENT="${AUTH_USERNAME:-}"
AUTH_PROVIDER_CURRENT="${AUTH_PROVIDER:-}"
AUTH_PASSWORD_CURRENT="${AUTH_PASSWORD:-}"
WEBHOOK_BEARER_TOKEN_CURRENT="${WEBHOOK_BEARER_TOKEN:-}"
STACKSTORM_API_KEY_CURRENT="${STACKSTORM_API_KEY:-}"
GITHUB_TOKEN_CURRENT="${GITHUB_TOKEN:-}"
DEVSTACK_DB_ROOT_PASSWORD_CURRENT="${DEVSTACK_DB_ROOT_PASSWORD:-}"
BAKERY_URL_CURRENT="${BAKERY_URL:-}"
BAKERY_BOOTSTRAP_HMAC_KEY_ID_CURRENT="${BAKERY_BOOTSTRAP_HMAC_KEY_ID:-}"
BAKERY_BOOTSTRAP_HMAC_SECRET_CURRENT="${BAKERY_BOOTSTRAP_HMAC_SECRET:-}"

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"
PYTHON_BIN="$(detect_executable PYTHON_BIN python3 "$PROJECT_ROOT/.venv/bin/python" /opt/homebrew/bin/python3 /usr/local/bin/python3)"

auth_username_from_cluster="$(read_secret_value "$POUNDCAKE_NAMESPACE" "$POUNDCAKE_ADMIN_SECRET" "username" || true)"
auth_password_from_cluster="$(read_secret_value "$POUNDCAKE_NAMESPACE" "$POUNDCAKE_ADMIN_SECRET" "password" || true)"
webhook_token_from_cluster="$(read_secret_value "$POUNDCAKE_NAMESPACE" "$POUNDCAKE_WEBHOOK_SECRET" "$POUNDCAKE_WEBHOOK_FIELD" || true)"
db_root_password_from_cluster="$(read_secret_value "$POUNDCAKE_NAMESPACE" "$POUNDCAKE_WEBHOOK_SECRET" "$POUNDCAKE_DB_ROOT_FIELD" || true)"
stackstorm_api_key_from_cluster="$(read_secret_value "$STACKSTORM_NAMESPACE" "$STACKSTORM_API_KEY_SECRET" "$STACKSTORM_API_KEY_FIELD" || true)"

[ -n "$auth_username_from_cluster" ] && AUTH_USERNAME_CURRENT="$auth_username_from_cluster"
[ -z "$AUTH_PROVIDER_CURRENT" ] && [ -n "$AUTH_USERNAME_CURRENT" ] && AUTH_PROVIDER_CURRENT="local"
[ -n "$auth_password_from_cluster" ] && AUTH_PASSWORD_CURRENT="$auth_password_from_cluster"
[ -n "$webhook_token_from_cluster" ] && WEBHOOK_BEARER_TOKEN_CURRENT="$webhook_token_from_cluster"
[ -n "$db_root_password_from_cluster" ] && DEVSTACK_DB_ROOT_PASSWORD_CURRENT="$db_root_password_from_cluster"
[ -n "$stackstorm_api_key_from_cluster" ] && STACKSTORM_API_KEY_CURRENT="$stackstorm_api_key_from_cluster"

mkdir -p "$(dirname "$DEVSTACK_SECRETS_FILE")"
umask 077
cat >"$DEVSTACK_SECRETS_FILE" <<EOF
#!/usr/bin/env bash
# Local-only Helm devstack secrets. This file is gitignored on purpose.

export AUTH_USERNAME=$(shell_quote "$AUTH_USERNAME_CURRENT")
export AUTH_PROVIDER=$(shell_quote "$AUTH_PROVIDER_CURRENT")
export AUTH_PASSWORD=$(shell_quote "$AUTH_PASSWORD_CURRENT")
export WEBHOOK_BEARER_TOKEN=$(shell_quote "$WEBHOOK_BEARER_TOKEN_CURRENT")
export DEVSTACK_DB_ROOT_PASSWORD=$(shell_quote "$DEVSTACK_DB_ROOT_PASSWORD_CURRENT")

# GitHub adapter / Genestack PR testing
export GITHUB_TOKEN=$(shell_quote "$GITHUB_TOKEN_CURRENT")

# StackStorm e2e helpers
export STACKSTORM_API_KEY=$(shell_quote "$STACKSTORM_API_KEY_CURRENT")

# Optional remote Bakery bootstrap
export BAKERY_URL=$(shell_quote "$BAKERY_URL_CURRENT")
export BAKERY_BOOTSTRAP_HMAC_KEY_ID=$(shell_quote "$BAKERY_BOOTSTRAP_HMAC_KEY_ID_CURRENT")
export BAKERY_BOOTSTRAP_HMAC_SECRET=$(shell_quote "$BAKERY_BOOTSTRAP_HMAC_SECRET_CURRENT")
EOF
chmod 600 "$DEVSTACK_SECRETS_FILE"

log "wrote local-only secrets to $DEVSTACK_SECRETS_FILE"

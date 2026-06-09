#!/usr/bin/env bash
# Configure PoundCake's GitHub adapter for devstack usage.

set -euo pipefail

# shellcheck source=/dev/null
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/load-local-secrets.sh"

POUNDCAKE_NAMESPACE="${POUNDCAKE_NAMESPACE:-poundcake}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
GITHUB_ALLOW_PUBLIC_READ="${GITHUB_ALLOW_PUBLIC_READ:-true}"
REQUIRE_GITHUB_WRITE="${REQUIRE_GITHUB_WRITE:-false}"

log() {
    printf '[helm-devstack-github-adapter] %s\n' "$*"
}

fail() {
    printf '[helm-devstack-github-adapter] ERROR: %s\n' "$*" >&2
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

KUBECTL_BIN="$(detect_executable KUBECTL_BIN kubectl /opt/homebrew/bin/kubectl /usr/local/bin/kubectl)"

normalize_bool() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on)
            printf 'true\n'
            ;;
        0|false|no|off|'')
            printf 'false\n'
            ;;
        *)
            fail "Invalid boolean value: $1"
            ;;
    esac
}

GITHUB_ALLOW_PUBLIC_READ="$(normalize_bool "$GITHUB_ALLOW_PUBLIC_READ")"
REQUIRE_GITHUB_WRITE="$(normalize_bool "$REQUIRE_GITHUB_WRITE")"

if [ "$REQUIRE_GITHUB_WRITE" = "true" ] && [ -z "$GITHUB_TOKEN" ]; then
    fail "REQUIRE_GITHUB_WRITE=true but GITHUB_TOKEN was not provided"
fi

log "waiting for PoundCake API"
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" wait --for=condition=Available deployment/poundcake-api --timeout="$WAIT_TIMEOUT"

if [ -n "$GITHUB_TOKEN" ]; then
    log "writing GitHub write-capable credential through PoundCake runtime"
else
    log "writing GitHub public-read credential policy through PoundCake runtime"
fi
"$KUBECTL_BIN" -n "$POUNDCAKE_NAMESPACE" exec -i deploy/poundcake-api -- \
    env \
    GITHUB_TOKEN="$GITHUB_TOKEN" \
    GITHUB_ALLOW_PUBLIC_READ="$GITHUB_ALLOW_PUBLIC_READ" \
    python3 - <<'PY'
import asyncio
import os

from api.services.adapter_runtime import dispose_adapter_runtime_resources
from api.services.credential_manager import write_adapter_credential


async def main() -> None:
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    allow_public_read = (os.getenv("GITHUB_ALLOW_PUBLIC_READ") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    try:
        await write_adapter_credential(
            service_type="github",
            credential_type="github_token",
            credential_key_id="default",
            payload={"token": token},
            allow_public_read=allow_public_read,
        )
        if token:
            print(
                "configured github adapter token_present=true "
                f"allow_public_read={str(allow_public_read).lower()}"
            )
        else:
            print(
                "configured github adapter token_present=false "
                f"allow_public_read={str(allow_public_read).lower()}"
            )
    finally:
        await dispose_adapter_runtime_resources()


asyncio.run(main())
PY

if [ -n "$GITHUB_TOKEN" ]; then
    log "GitHub adapter configured for write-capable devstack usage"
else
    log "GitHub adapter configured for public-read devstack usage"
fi

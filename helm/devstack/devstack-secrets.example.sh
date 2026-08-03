#!/usr/bin/env bash
# Copy to helm/devstack/.devstack-secrets.sh and keep that file local-only.

export AUTH_USERNAME=""
export AUTH_PROVIDER=""
export AUTH_PASSWORD=""
export WEBHOOK_BEARER_TOKEN=""
export DEVSTACK_DB_ROOT_PASSWORD=""

# GitHub adapter / Genestack PR testing
export GITHUB_TOKEN=""

# StackStorm e2e helpers
export STACKSTORM_API_KEY=""

# Optional remote Bakery monitor credential
export BAKERY_URL=""
export BAKERY_MONITOR_ID=""
export BAKERY_MONITOR_UUID=""
export BAKERY_MONITOR_HMAC_KEY_ID=""
export BAKERY_MONITOR_HMAC_SECRET=""

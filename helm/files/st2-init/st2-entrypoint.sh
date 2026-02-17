#!/bin/bash
set -euo pipefail

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

export PYTHONPATH=${PYTHONPATH:-}:/opt/stackstorm/st2/lib/python3.10/site-packages
export PATH=${PATH}:/opt/stackstorm/st2/bin

if [ -f "/app/config/st2_api_key" ] && [ -w /root/.bashrc ]; then
  if ! grep -q "export ST2_API_KEY=" /root/.bashrc 2>/dev/null; then
    echo "export ST2_API_KEY=\$(cat /app/config/st2_api_key)" >> /root/.bashrc
  fi
fi

if ! command -v envsubst >/dev/null 2>&1; then
  log "Installing envsubst"
  apt-get update -qq && apt-get install -y -qq gettext-base >/dev/null 2>&1
fi

if [ ! -f "/etc/st2/st2.conf.template" ]; then
  log "ERROR: /etc/st2/st2.conf.template not found"
  exit 1
fi

: "${MONGO_USERNAME:?MONGO_USERNAME not set}"
: "${MONGO_PASSWORD:?MONGO_PASSWORD not set}"
: "${RABBITMQ_USER:?RABBITMQ_USER not set}"
: "${RABBITMQ_PASSWORD:?RABBITMQ_PASSWORD not set}"

envsubst '${MONGO_USERNAME} ${MONGO_PASSWORD} ${RABBITMQ_USER} ${RABBITMQ_PASSWORD}' \
  < /etc/st2/st2.conf.template > /etc/st2/st2.conf

if [ ! -f "/etc/st2/st2.conf" ]; then
  log "ERROR: Failed to create /etc/st2/st2.conf"
  exit 1
fi

exec "$@"

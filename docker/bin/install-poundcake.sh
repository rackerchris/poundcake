#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

cd "$PROJECT_ROOT"

COMPOSE="${COMPOSE:-docker compose}"

echo "Installing local dummy-only PoundCake with ${COMPOSE} from: $PROJECT_ROOT"

if ! command -v "${COMPOSE%% *}" >/dev/null 2>&1; then
  echo "ERROR: ${COMPOSE%% *} is not installed or not in PATH"
  exit 1
fi

if ! $COMPOSE -f docker/docker-compose.yml version >/dev/null 2>&1; then
  echo "ERROR: ${COMPOSE} -f docker/docker-compose.yml is not available"
  exit 1
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

$COMPOSE -f docker/docker-compose.yml up --build -d "$@"

echo

echo "Current service status:"
$COMPOSE -f docker/docker-compose.yml ps

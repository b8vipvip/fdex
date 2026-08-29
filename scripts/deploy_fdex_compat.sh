#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/fdex}"
CANONICAL_UPDATER="${APP_DIR}/scripts/update_server.sh"

if [[ ! -f "${CANONICAL_UPDATER}" ]]; then
  echo "FDEX canonical updater not found: ${CANONICAL_UPDATER}" >&2
  echo "Expected repository layout: ${APP_DIR}/server + ${APP_DIR}/scripts/update_server.sh" >&2
  exit 1
fi

exec /bin/bash "${CANONICAL_UPDATER}" "$@"

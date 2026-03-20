#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CREATE_SCHEMA="${CREATE_SCHEMA:-1}"
TRUNCATE_DB="${TRUNCATE_DB:-1}"
SMOKE="${SMOKE:-0}"

ARGS=()

if [[ "$CREATE_SCHEMA" == "1" ]]; then
  ARGS+=("--create-schema")
fi

if [[ "$TRUNCATE_DB" == "1" ]]; then
  export SKIP_CONFIRM=1
  ARGS+=("--truncate-with-script")
fi

if [[ "$SMOKE" == "1" ]]; then
  ARGS+=("--smoke")
fi

python3 scripts/seed_db_test/seed_showcase_open_repo.py "${ARGS[@]}" "$@"

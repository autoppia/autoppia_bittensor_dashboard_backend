#!/usr/bin/env bash
# flush_db.sh — Truncate all user tables in a Postgres DB (keep schemas intact)

set -euo pipefail

# --- Load .env file automatically if present ---
if [[ -f ".env" ]]; then
  echo "📄 Loading environment variables from .env"
  # export all lines that aren't comments
  export $(grep -v '^#' .env | xargs)
else
  echo "⚠️  No .env file found in current directory. Make sure env vars are set manually."
fi

# --- Determine environment and set variable names ---
ENVIRONMENT="${ENVIRONMENT:-local}"
ENVIRONMENT=$(echo "$ENVIRONMENT" | tr '[:upper:]' '[:lower:]')  # Normalize to lowercase
ENV_SUFFIX=$(echo "$ENVIRONMENT" | tr '[:lower:]' '[:upper:]')  # Convert to uppercase for suffix

echo "🔧 Using environment: $ENVIRONMENT"

# --- Get environment-specific variables with fallback to generic ---
_get_var() {
  local base_name=$1
  local specific_var="${base_name}_${ENV_SUFFIX}"

  # Try specific var first (e.g., POSTGRES_USER_LOCAL)
  if [[ -n "${!specific_var:-}" ]]; then
    echo "${!specific_var}"
    return 0
  fi

  # Fallback to generic var (e.g., POSTGRES_USER)
  if [[ -n "${!base_name:-}" ]]; then
    echo "${!base_name}"
    return 0
  fi

  return 1
}

# --- Resolve database variables ---
POSTGRES_USER=$(_get_var "POSTGRES_USER") || POSTGRES_USER=""
POSTGRES_PASSWORD=$(_get_var "POSTGRES_PASSWORD") || POSTGRES_PASSWORD=""
POSTGRES_HOST=$(_get_var "POSTGRES_HOST") || POSTGRES_HOST="127.0.0.1"
POSTGRES_PORT=$(_get_var "POSTGRES_PORT") || POSTGRES_PORT="5432"
POSTGRES_DB=$(_get_var "POSTGRES_DB") || POSTGRES_DB=""

# --- Validate required vars ---
: "${POSTGRES_USER:?POSTGRES_USER or POSTGRES_USER_${ENV_SUFFIX} is missing}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or POSTGRES_PASSWORD_${ENV_SUFFIX} is missing}"
: "${POSTGRES_HOST:?POSTGRES_HOST or POSTGRES_HOST_${ENV_SUFFIX} is missing}"
: "${POSTGRES_PORT:?POSTGRES_PORT or POSTGRES_PORT_${ENV_SUFFIX} is missing}"
: "${POSTGRES_DB:?POSTGRES_DB or POSTGRES_DB_${ENV_SUFFIX} is missing}"

export PGPASSWORD="${POSTGRES_PASSWORD}"

# --- Safety confirmation ---
if [[ "${SKIP_CONFIRM:-0}" != "1" ]]; then
  echo "⚠️  This will TRUNCATE ALL USER TABLES in '${POSTGRES_DB}' on ${POSTGRES_HOST}:${POSTGRES_PORT}"
  read -r -p "Type 'YES' to continue: " CONFIRM
  [[ "$CONFIRM" == "YES" ]] || { echo "❌ Aborted."; exit 1; }
fi

# --- Execute truncation ---
psql \
  --host="${POSTGRES_HOST}" \
  --port="${POSTGRES_PORT}" \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --set=ON_ERROR_STOP=1 \
  --no-align --tuples-only \
<<'SQL'
DO $$
DECLARE
  stmt text;
BEGIN
  SELECT 'TRUNCATE TABLE ' ||
         string_agg(format('%I.%I', n.nspname, c.relname), ', ')
         || ' RESTART IDENTITY CASCADE'
  INTO stmt
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r','p')
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg_toast%'
    AND n.nspname NOT LIKE 'pg_temp_%';

  IF stmt IS NULL THEN
    RAISE NOTICE 'No user tables found.';
    RETURN;
  END IF;

  EXECUTE stmt;
END $$;
SQL

echo "✅ All user tables truncated successfully in '${POSTGRES_DB}'."

# --- Ask whether to insert initial round_config ---
echo ""
echo "Insert initial round_config with these values?"
echo "  round_size_epochs:    0.4166667"
echo "  season_size_epochs:   280.0"
echo "  minimum_start_block:  7672644"
echo "  blocks_per_epoch:      360"
echo "  updated_by_validator_uid: 83"
echo ""
read -r -p "Insert initial round_config? [y/N]: " INSERT_ROUND_CONFIG
if [[ "${INSERT_ROUND_CONFIG,,}" == "y" || "${INSERT_ROUND_CONFIG,,}" == "yes" ]]; then
  # Trigger requires app_runtime_config.main_validator_uid to be set (truncate leaves it empty)
  psql \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --set=ON_ERROR_STOP=1 \
    -c "INSERT INTO app_runtime_config (id, main_validator_uid, main_validator_hotkey, updated_at) VALUES (1, 83, NULL, NOW()) ON CONFLICT (id) DO UPDATE SET main_validator_uid = EXCLUDED.main_validator_uid, main_validator_hotkey = COALESCE(EXCLUDED.main_validator_hotkey, app_runtime_config.main_validator_hotkey), updated_at = NOW();"
  psql \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --set=ON_ERROR_STOP=1 \
    -c "INSERT INTO round_config (id, round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch, updated_by_validator_uid) VALUES (1, 0.4166667, 280.0, 7672644, 360, 83) ON CONFLICT (id) DO UPDATE SET round_size_epochs = EXCLUDED.round_size_epochs, season_size_epochs = EXCLUDED.season_size_epochs, minimum_start_block = EXCLUDED.minimum_start_block, blocks_per_epoch = EXCLUDED.blocks_per_epoch, updated_by_validator_uid = EXCLUDED.updated_by_validator_uid, updated_at = NOW();"
  echo "✅ Initial round_config inserted/updated."
else
  echo "⏭️  Skipped round_config insert."
fi

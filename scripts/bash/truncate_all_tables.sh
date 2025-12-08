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

# --- Validate required vars ---
: "${POSTGRES_USER:?POSTGRES_USER is missing}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is missing}"
: "${POSTGRES_HOST:?POSTGRES_HOST is missing}"
: "${POSTGRES_PORT:?POSTGRES_PORT is missing}"
: "${POSTGRES_DB:?POSTGRES_DB is missing}"

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

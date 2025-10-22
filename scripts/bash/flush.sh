#!/usr/bin/env bash
# flush_db.sh — Truncate all user tables in a Postgres database without dropping schemas.

set -euo pipefail

# Required environment variables (use your provided values or export before running):
# POSTGRES_USER=autoppia_user
# POSTGRES_PASSWORD=Autoppia#2025.Leaderboard
# POSTGRES_HOST=127.0.0.1
# POSTGRES_PORT=5432
# POSTGRES_DB=autoppia_prod

# Ensure password is available to psql
export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

# Optional safety prompt; set SKIP_CONFIRM=1 to skip
if [[ "${SKIP_CONFIRM:-0}" != "1" ]]; then
  echo "About to TRUNCATE ALL USER TABLES in database '${POSTGRES_DB}' on ${POSTGRES_HOST}:${POSTGRES_PORT} as ${POSTGRES_USER}."
  read -r -p "Type 'YES' to proceed: " CONFIRM
  [[ "$CONFIRM" == "YES" ]] || { echo "Aborted."; exit 1; }
fi

psql \
  --host="${POSTGRES_HOST:?POSTGRES_HOST is required}" \
  --port="${POSTGRES_PORT:?POSTGRES_PORT is required}" \
  --username="${POSTGRES_USER:?POSTGRES_USER is required}" \
  --dbname="${POSTGRES_DB:?POSTGRES_DB is required}" \
  --set=ON_ERROR_STOP=1 \
  --no-align --tuples-only \
<<'SQL'
DO $$
DECLARE
  stmt text;
BEGIN
  /*
    Build and execute a single TRUNCATE statement that targets every base/partitioned table
    in non-system schemas, restarting identities and cascading over FKs.
    - Keeps schemas and objects intact.
    - Skips system/internal schemas.
  */
  SELECT 'TRUNCATE TABLE ' ||
         string_agg(format('%I.%I', n.nspname, c.relname), ', ')
         || ' RESTART IDENTITY CASCADE'
  INTO stmt
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r','p')                 -- ordinary & partitioned tables
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg_toast%'         -- internal storage
    AND n.nspname NOT LIKE 'pg_temp_%'         -- temp schemas
    AND pg_table_is_visible(c.oid);            -- respect search_path visibility

  IF stmt IS NULL THEN
    RAISE NOTICE 'No user tables found to truncate.';
    RETURN;
  END IF;

  -- Execute the truncate in a single statement for efficiency
  EXECUTE stmt;
END $$;
SQL

echo "✅ Database '${POSTGRES_DB}' user tables truncated (identities reset, schemas preserved)."

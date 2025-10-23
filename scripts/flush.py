#!/usr/bin/env python3
"""
Flush (reset) the database using psql with .env POSTGRES_* variables.

This script reads the DB connection from project .env via
scripts.db_utils.get_database_url(), prints a password-masked DSN,
asks for confirmation, and then executes a TRUNCATE of all user tables
with RESTART IDENTITY CASCADE using the PostgreSQL client `psql`.

No Python DB drivers are used — only the `psql` CLI.
"""

import os
import subprocess
from typing import Optional
from scripts.db_utils import get_database_url
from sqlalchemy.engine import make_url

PSQL_TRUNCATE_BLOCK = r"""
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
"""


def _mask_dsn(dsn: str) -> str:
    try:
        url = make_url(dsn)
        if url.password:
            url = url.set(password="***")
        return str(url)
    except Exception:
        return dsn


def _psql_flush(database_url: str) -> None:
    url = make_url(database_url)
    host: Optional[str] = url.host or "127.0.0.1"
    port: int = int(url.port or 5432)
    user: Optional[str] = url.username or "postgres"
    password: Optional[str] = url.password
    dbname: str = url.database or "postgres"

    env = dict(os.environ)
    if password is not None:
        env["PGPASSWORD"] = password

    cmd = [
        "psql",
        f"--host={host}",
        f"--port={port}",
        f"--username={user}",
        f"--dbname={dbname}",
        "--set=ON_ERROR_STOP=1",
        "--no-align",
        "--tuples-only",
    ]

    print("🔁 Executing psql-based flush (TRUNCATE ALL USER TABLES)...")
    subprocess.run(cmd, input=PSQL_TRUNCATE_BLOCK.encode("utf-8"), check=True, env=env)

def main() -> int:
    database_url = get_database_url()
    display_dsn = _mask_dsn(database_url)
    print("=" * 60)
    print("DATABASE FLUSH (psql)")
    print("=" * 60)
    print(f"🔄 Using database: {display_dsn}")

    confirm = input("⚠️  This will TRUNCATE ALL USER TABLES and RESET IDENTITIES. Continue? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Aborted.")
        return 1

    try:
        _psql_flush(database_url)
        print("✅ All user tables truncated successfully via psql.")
        return 0
    except FileNotFoundError:
        print("❌ psql not found on PATH. Please install PostgreSQL client tools.")
        return 2
    except subprocess.CalledProcessError as exc:
        print("❌ psql-based flush failed.")
        print(str(exc))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

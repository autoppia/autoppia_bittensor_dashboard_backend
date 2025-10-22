#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import load_dotenv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Flush the configured Postgres database by resetting its schema (no drop/create DB)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    default_env = str(Path(__file__).resolve().parent.parent / ".env")
    parser.add_argument("-e", "--env-file", default=default_env, help="Path to .env file")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--force", action="store_true", help="Allow non-local host")
    return parser.parse_args()


def parse_from_database_url(url: str) -> dict:
    u = urlparse(url)
    # urlparse handles schemes like postgresql+asyncpg
    return {
        "user": u.username or "",
        "password": u.password or "",
        "host": u.hostname or "",
        "port": u.port or 5432,
        "db": (u.path or "/").lstrip("/") or "",
    }


def load_config(env_path: str) -> dict:
    load_dotenv(dotenv_path=env_path, override=True)

    cfg = {
        "user": os.getenv("POSTGRES_USER", ""),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "host": os.getenv("POSTGRES_HOST", ""),
        "port": int(os.getenv("POSTGRES_PORT", "5432") or 5432),
        "db": os.getenv("POSTGRES_DB", ""),
    }

    if not all(cfg.values()):
        url = os.getenv("DATABASE_URL", "")
        if url:
            derived = parse_from_database_url(url)
            cfg = {
                "user": cfg["user"] or derived["user"],
                "password": cfg["password"] or derived["password"],
                "host": cfg["host"] or derived["host"],
                "port": cfg["port"] or derived["port"],
                "db": cfg["db"] or derived["db"],
            }

    missing = [k for k, v in cfg.items() if not v and k != "port"]
    if missing:
        raise SystemExit(f"Missing required env vars in {env_path}: {', '.join(missing)}")

    return cfg


def ensure_local_host(host: str, allow_non_local: bool) -> None:
    if allow_non_local:
        return
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"Refusing to flush non-local database host '{host}'. Use --force to override."
        )


def confirm_if_needed(skip: bool, db: str, host: str, port: int, user: str) -> None:
    if skip:
        return
    print(f"Target database: '{db}' on {host}:{port} (user: {user})")
    ans = input("This will DROP and RECREATE the database. Type 'flush' to proceed: ")
    if ans.strip() != "flush":
        raise SystemExit("Aborted.")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def ensure_database_exists(cfg: dict) -> None:
    """Ensure the target database exists; create if missing and privileges allow."""
    try:
        conn = await asyncpg.connect(
            user=cfg["user"],
            password=cfg["password"],
            host=cfg["host"],
            port=cfg["port"],
            database=cfg["db"],
        )
        await conn.close()
        return
    except asyncpg.InvalidCatalogNameError:
        # Database does not exist; try to create it using connection to 'postgres'
        pass

    try:
        admin = await asyncpg.connect(
            user=cfg["user"],
            password=cfg["password"],
            host=cfg["host"],
            port=cfg["port"],
            database="postgres",
        )
        try:
            db_ident = quote_ident(cfg["db"])
            print("Target database not found; attempting to create it...")
            await admin.execute(f"CREATE DATABASE {db_ident}")
        finally:
            await admin.close()
    except asyncpg.InsufficientPrivilegeError:
        raise SystemExit(
            "Database does not exist and the configured user lacks CREATEDB privilege. "
            "Create the database manually or run with a user that can create databases.\n"
            f"Example: psql -h {cfg['host']} -p {cfg['port']} -U postgres -d postgres -c \"CREATE DATABASE {cfg['db']};\""
        )


async def flush_schema(cfg: dict) -> None:
    """Drop and recreate the public schema in the target DB."""
    conn = await asyncpg.connect(
        user=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["db"],
    )
    try:
        print("Dropping schema 'public' (cascade)...")
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")

        print("Recreating schema 'public'...")
        owner_ident = quote_ident(cfg["user"])
        await conn.execute(f"CREATE SCHEMA public AUTHORIZATION {owner_ident}")

        print(f"Done. Schema reset completed for database '{cfg['db']}'.")
    finally:
        await conn.close()


def main() -> None:
    args = parse_args()
    env_path = os.path.abspath(args.env_file)
    if not os.path.isfile(env_path):
        raise SystemExit(f"Env file not found: {env_path}")

    cfg = load_config(env_path)
    ensure_local_host(cfg["host"], args.force)
    confirm_if_needed(args.yes, cfg["db"], cfg["host"], cfg["port"], cfg["user"])

    try:
        asyncio.run(ensure_database_exists(cfg))
        asyncio.run(flush_schema(cfg))
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "a dependency")
        print(
            f"Missing Python dependency '{missing}'. Ensure requirements are installed (e.g., 'pip install -r requirements.txt').",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    main()

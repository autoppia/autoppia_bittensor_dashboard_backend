#!/usr/bin/env python3
"""
IWAP - Simplified Python CLI for Autoppia.

Commands:
  iwap flush
  iwap seed round

This CLI sets DATABASE_URL in the process environment before importing any
app modules, ensuring the FastAPI app and seed utilities target the same DB
specified in the project's .env file.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import List, Optional

from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def _mask_dsn(dsn: str) -> str:
    try:
        url = make_url(dsn)
        if url.password:
            url = url.set(password="***")
        return str(url)
    except Exception:
        return dsn


def _resolve_cli_dsn() -> str:
    """Resolve DB URL from project .env (preferred) or OS env.

    This intentionally avoids importing app.config.settings so we can set
    os.environ["DATABASE_URL"] first, then import seed modules.
    """
    values = {}
    try:
        values = dotenv_values(".env") or {}
    except Exception:
        values = {}
    # Prefer explicit DATABASE_URL only if it's Postgres
    db_url = (values.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if db_url:
        if db_url.lower().startswith("postgres"):
            return db_url
        # Ignore non-Postgres DATABASE_URL in favor of POSTGRES_* variables
    # Build from POSTGRES_* (fall back to OS env for any missing)
    def _v(key: str, default: str = "") -> str:
        return (values.get(key) or os.environ.get(key) or default).strip().strip('"')

    user = _v("POSTGRES_USER", "")
    password = _v("POSTGRES_PASSWORD", "")
    host = _v("POSTGRES_HOST", "127.0.0.1")
    port = _v("POSTGRES_PORT", "5432")
    db = _v("POSTGRES_DB", "")
    if user and db:
        auth = f"{user}:{password}@" if password else f"{user}@"
        return f"postgresql+asyncpg://{auth}{host}:{port}/{db}"
    # As a last resort, fail fast rather than defaulting to SQLite
    print("❌ Unable to resolve Postgres DATABASE_URL from .env. Set POSTGRES_* or DATABASE_URL=postgresql+asyncpg://...")
    raise SystemExit(2)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="IWAP - Autoppia command-line interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  iwa flush
  iwa seed round
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Flush
    subparsers.add_parser("flush", help="Flush (reset) the database")

    # Seed group
    seed_parser = subparsers.add_parser("seed", help="Seed data into the database")
    seed_subparsers = seed_parser.add_subparsers(dest="seed_command", help="Seed subcommand")
    seed_subparsers.add_parser("round", help="Seed validator rounds")

    args = parser.parse_args()

    if args.command == "flush":
        # Use standalone flush script to avoid Python DB driver quirks
        dsn = _resolve_cli_dsn()
        os.environ["DATABASE_URL"] = dsn
        print("=" * 60)
        print("DATABASE FLUSH (psql)")
        print("=" * 60)
        print(f"🔄 Using database: {_mask_dsn(dsn)}")
        resp = input("⚠️  This will TRUNCATE ALL USER TABLES and RESET IDENTITIES. Continue? [y/N]: ").strip().lower()
        if resp not in {"y", "yes"}:
            print("Aborted.")
            return 1
        # Delegate to the dedicated psql-based script
        import subprocess
        code = subprocess.call([sys.executable, "scripts/flush.py"])  # returns exit code
        return int(code)

    if args.command == "seed":
        if args.seed_command == "round":
            # Ensure the FastAPI app uses the exact same DB as the CLI
            dsn = _resolve_cli_dsn()
            os.environ["DATABASE_URL"] = dsn
            print("=" * 60)
            print("SEED ROUND (Multiple Validators)")
            print("=" * 60)
            print(f"📡 Using database: {_mask_dsn(dsn)}")

            # Import seeding utilities only after DATABASE_URL is set
            from scripts.seed_round import seed_multiple_rounds

            rounds_str = input(
                "Enter round number(s) (comma-separated, e.g., 1,2,3): "
            ).strip()
            if not rounds_str:
                print("❌ No rounds specified.")
                return 1

            def _parse_int_list(value: str) -> List[int]:
                items = [s.strip() for s in value.split(",") if s.strip()]
                cleaned: List[int] = []
                for item in items:
                    try:
                        n = int(item)
                        if n > 0:
                            cleaned.append(n)
                    except ValueError:
                        pass
                return sorted(set(cleaned))

            round_numbers = _parse_int_list(rounds_str)
            if not round_numbers:
                print("❌ Invalid round numbers provided.")
                return 1

            uids_str = input(
                "Enter validator UID(s) (comma-separated, or press Enter for all): "
            ).strip()
            validator_uids: Optional[List[int]] = None
            if uids_str:
                validator_uids = _parse_int_list(uids_str)
                if not validator_uids:
                    print("❌ Invalid validator UIDs provided.")
                    return 1

            miners_str = input("Number of miners (or press Enter for random 10-20): ").strip()
            tasks_str = input("Number of tasks (or press Enter for random 10-20): ").strip()
            num_miners = int(miners_str) if miners_str.isdigit() else None
            num_tasks = int(tasks_str) if tasks_str.isdigit() else None

            print("🔄 Seeding round(s)...")
            results = seed_multiple_rounds(
                round_numbers=round_numbers,
                validator_uids=validator_uids,
                num_miners=num_miners,
                num_tasks=num_tasks,
            )

            total_validator_rounds = sum(len(v) for v in results.values())
            if len(round_numbers) == 1:
                print(
                    f"✅ Seeded round {round_numbers[0]} for {total_validator_rounds} validator(s)."
                )
            else:
                print(
                    f"✅ Seeded {len(round_numbers)} round(s) with {total_validator_rounds} total validator round(s)."
                )
            return 0
        print("Error: specify a subcommand, e.g. 'iwap seed round'")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

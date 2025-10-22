#!/usr/bin/env python3
"""
IWAP - Simplified Python CLI for Autoppia.

Commands:
  iwap flush
  iwap seed round
"""

from __future__ import annotations

import argparse
import asyncio
from typing import List, Optional

from scripts.db_utils import get_database_url
from scripts.flush_db import flush_database
from scripts.seed_round import seed_multiple_rounds

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
        dsn = get_database_url()
        print("=" * 60)
        print("DATABASE FLUSH")
        print("=" * 60)
        print(f"🔄 Using database: {dsn}")
        if dsn.startswith("sqlite"):
            print("⚠️  Detected SQLite DSN. If you expected Postgres, ensure .env has DATABASE_URL or POSTGRES_* set.")

        resp = input("⚠️  This will DROP ALL TABLES. Continue? [y/N]: ").strip().lower()
        if resp not in {"y", "yes"}:
            print("Aborted.")
            return 1

        asyncio.run(flush_database(database_url=dsn, assume_yes=True))
        print("✅ Database flushed successfully!")
        return 0

    if args.command == "seed":
        if args.seed_command == "round":
            dsn = get_database_url()
            print("=" * 60)
            print("SEED ROUND (Multiple Validators)")
            print("=" * 60)
            print(f"📡 Using database: {dsn}")
            if dsn.startswith("sqlite"):
                print("⚠️  Detected SQLite DSN. If you expected Postgres, ensure .env has DATABASE_URL or POSTGRES_* set.")

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

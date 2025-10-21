#!/usr/bin/env python3
"""
IWAP - Interactive Wrapper for Autoppia (Clean Edition)

Simple, reliable CLI for database and seeding operations.

Usage examples:
  iwa flush
  iwa seed round
"""

import argparse
import asyncio
from scripts.db_utils import get_database_url


# ------------------------------------------------------------
# FLUSH COMMAND
# ------------------------------------------------------------

async def flush_database_cli() -> None:
    """Flush (reset) the database cleanly using .env POSTGRES_* vars."""
    from scripts.flush_db import flush_database

    database_url = get_database_url()
    print("=" * 60)
    print("DATABASE FLUSH")
    print("=" * 60)
    print(f"🔄 Using database: {database_url}")

    confirm = input("⚠️  This will DROP ALL TABLES. Continue? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Aborted.")
        return

    await flush_database(database_url, assume_yes=True)
    print("✅ Database flushed successfully!")


# ------------------------------------------------------------
# SEED COMMANDS
# ------------------------------------------------------------

def _get_last_round_number() -> int:
    """
    Query the database for the latest round number.
    Returns 0 if no rounds exist yet.
    """
    import sqlalchemy
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import asyncio

    async def _fetch_last_round():
        database_url = get_database_url()
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            try:
                result = await conn.execute(text("SELECT MAX(round_number) FROM validator_rounds"))
                value = result.scalar()
                return value or 0
            except sqlalchemy.exc.ProgrammingError:
                # Table doesn't exist yet
                return 0
            finally:
                await engine.dispose()

    return asyncio.run(_fetch_last_round())


def seed_round_cli() -> None:
    """Seed multiple rounds using .env connection, starting from the last round."""
    from scripts.seed_round import seed_multiple_rounds

    database_url = get_database_url()
    print("=" * 60)
    print("SEED ROUNDS")
    print("=" * 60)
    print(f"📡 Using database: {database_url}")

    last_round = _get_last_round_number()
    print(f"ℹ️  Last existing round in DB: {last_round}")

    num_to_seed_input = input("How many new rounds to seed? (e.g. 3): ").strip()
    if not num_to_seed_input.isdigit() or int(num_to_seed_input) <= 0:
        print("❌ Invalid number of rounds.")
        return

    num_to_seed = int(num_to_seed_input)
    start_round = last_round + 1
    end_round = last_round + num_to_seed
    rounds_to_seed = list(range(start_round, end_round + 1))  # ✅ fixed inclusive range
    print(f"➡️  Will seed rounds {rounds_to_seed}")

    num_miners_input = input("Number of miners (default random 10–20): ").strip()
    num_tasks_input = input("Number of tasks  (default random 10–20): ").strip()

    num_miners = int(num_miners_input) if num_miners_input else None
    num_tasks = int(num_tasks_input) if num_tasks_input else None

    print(f"🔄 Seeding rounds {rounds_to_seed} ...")
    seeded = seed_multiple_rounds(
        round_numbers=rounds_to_seed,
        validator_uids=None,  # all validators
        num_miners=num_miners,
        num_tasks=num_tasks,
    )

    total = sum(len(v) for v in seeded.values())
    print(f"✅ Seeded {len(rounds_to_seed)} new round(s) across {total} validator(s).")


# ------------------------------------------------------------
# CLI ENTRYPOINT
# ------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="IWAP - Simplified Wrapper for Autoppia",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  iwa flush
  iwa seed round
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Main command")

    # --- FLUSH
    subparsers.add_parser("flush", help="Flush and reinitialize the database")

    # --- SEED GROUP
    seed_parser = subparsers.add_parser("seed", help="Seed data into the database")
    seed_subparsers = seed_parser.add_subparsers(dest="seed_command", help="Seed command")
    seed_subparsers.add_parser("round", help="Seed round(s) across validators")

    args = parser.parse_args()

    if args.command == "flush":
        asyncio.run(flush_database_cli())
        return 0

    if args.command == "seed":
        if not args.seed_command:
            print("Error: Please specify a seed command (e.g. 'iwa seed round')")
            return 1
        if args.seed_command == "round":
            seed_round_cli()
            return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

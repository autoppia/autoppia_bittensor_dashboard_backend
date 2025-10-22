#!/usr/bin/env python3
"""
Flush (reset) the database cleanly using .env POSTGRES_* variables.
"""

import asyncio
from scripts.db_utils import get_database_url

async def main():
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

if __name__ == "__main__":
    asyncio.run(main())

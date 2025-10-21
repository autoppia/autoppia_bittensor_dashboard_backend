#!/usr/bin/env python3
"""
Flush (reset) the Autoppia database cleanly using .env POSTGRES_* variables.
"""

import asyncio
from scripts.db_utils import get_database_url

async def main():
    from scripts.flush_db import flush_database

    database_url = get_database_url()
    print(f"🔄 Flushing database: {database_url}")

    # Directly await the coroutine instead of calling flush_seed_database()
    await flush_database(database_url, assume_yes=True)

    print("✅ Database flushed successfully!")

if __name__ == "__main__":
    asyncio.run(main())

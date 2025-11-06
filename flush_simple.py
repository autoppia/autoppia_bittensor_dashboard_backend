#!/usr/bin/env python3
"""
Simple script to flush (reset) the PostgreSQL database using the .env DATABASE_URL.
"""

import asyncio
import os
from dotenv import load_dotenv

# Load DATABASE_URL from .env
load_dotenv()

async def main():
    from scripts.flush_db import flush_seed_database

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not found in environment or .env file")

    print(f"🔄 Flushing database: {database_url}")
    flush_seed_database(database_url=database_url, assume_yes=True)
    print("✅ Database flushed successfully!")

if __name__ == "__main__":
    asyncio.run(main())

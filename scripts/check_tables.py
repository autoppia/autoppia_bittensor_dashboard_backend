#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.config import settings
import asyncpg

async def check():
    conn = await asyncpg.connect(settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://'))
    tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'validator_round%' ORDER BY tablename")
    print('Tablas validator_round* en la base de datos:')
    for t in tables:
        print(f'  - {t["tablename"]}')
    await conn.close()

asyncio.run(check())


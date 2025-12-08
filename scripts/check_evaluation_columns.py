#!/usr/bin/env python3
"""Verificar columnas reales en la tabla evaluations."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def check():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'evaluations' 
            AND column_name IN ('eval_score', 'reward', 'final_score', 'evaluation_time', 'execution_history', 'feedback', 'meta')
            ORDER BY column_name
        """))
        rows = result.fetchall()
        print("Columnas en tabla 'evaluations':")
        for row in rows:
            print(f"  - {row[0]}: {row[1]}")

asyncio.run(check())


#!/usr/bin/env python3
"""
Script para renombrar la tabla validator_round_summary a validator_round_summary_miners
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
import asyncpg


async def run_migration():
    """Execute the migration to rename the table."""
    # Read migration SQL
    migration_file = Path(__file__).parent / "migrations" / "migrate_rename_validator_round_summary.sql"
    
    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False
    
    sql_content = migration_file.read_text()
    
    # Build connection string (asyncpg uses postgresql:// not postgresql+asyncpg://)
    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    
    try:
        print(f"🔌 Connecting to database: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
        conn = await asyncpg.connect(db_url)
        
        print("📝 Executing migration...")
        await conn.execute(sql_content)
        
        print("✅ Migration completed successfully!")
        await conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(run_migration())
    sys.exit(0 if success else 1)


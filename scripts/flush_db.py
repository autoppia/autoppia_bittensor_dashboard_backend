#!/usr/bin/env python3
"""
Dialect-aware database flush utilities.

- PostgreSQL: DROP/CREATE public schema
- SQLite: reflect and drop all tables safely
"""

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import create_async_engine


async def flush_database(database_url: str, assume_yes: bool = False) -> None:
    """
    Flush the database at `database_url` in a dialect-aware manner.

    Args:
        database_url: Async SQLAlchemy DSN (e.g., postgresql+asyncpg://...)
        assume_yes: reserved for future interactivity (confirmation handled by caller).
    """
    engine = create_async_engine(database_url, future=True)

    try:
        async with engine.begin() as conn:
            dialect = conn.dialect.name  # 'postgresql', 'sqlite', etc.

            if dialect in ("postgresql", "postgres"):
                # PostgreSQL: nuke public schema, then recreate it
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
                # Optional: reset search_path or grants here if needed
                # await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
                # await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres"))

            elif dialect == "sqlite":
                # SQLite: reflect & drop all tables. Disable FKs during drop.
                await conn.execute(text("PRAGMA foreign_keys = OFF"))

                async def _drop_all(sync_conn):
                    md = MetaData()
                    md.reflect(bind=sync_conn)
                    md.drop_all(bind=sync_conn)

                await conn.run_sync(_drop_all)
                await conn.execute(text("PRAGMA foreign_keys = ON"))
                # Optionally VACUUM (requires autocommit; safe to skip here)

            else:
                # Generic fallback: reflect & drop_all
                async def _drop_all(sync_conn):
                    md = MetaData()
                    md.reflect(bind=sync_conn)
                    md.drop_all(bind=sync_conn)

                await conn.run_sync(_drop_all)
    finally:
        await engine.dispose()

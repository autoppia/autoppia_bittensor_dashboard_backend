#!/usr/bin/env python3
"""
Database flush utilities for IWAP.

Drops and recreates all tables using the SQLAlchemy metadata from
the application. Reads database configuration from .env via
`app.config.settings` to keep a single source of truth.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.db.base import Base


def _build_engine(dsn: Optional[str] = None) -> AsyncEngine:
    """Create an async engine for the given DSN, normalizing the driver.

    Falls back to `settings.DATABASE_URL` when `dsn` is not provided.
    Ensures an async driver variant is used (e.g., postgresql+asyncpg, sqlite+aiosqlite).
    """
    raw_url = dsn or settings.DATABASE_URL
    url = make_url(raw_url)
    driver = url.drivername
    if driver in {"postgres", "postgresql"} or (
        driver.startswith("postgresql") and "+asyncpg" not in driver
    ):
        url = url.set(drivername="postgresql+asyncpg")
    elif driver == "sqlite":
        url = url.set(drivername="sqlite+aiosqlite")
    return create_async_engine(str(url), echo=False, future=True)


async def flush_database(database_url: Optional[str] = None, assume_yes: bool = False) -> None:
    """Drop and recreate all tables.

    Parameters
    - database_url: Optional override DSN. Defaults to settings.DATABASE_URL
    - assume_yes: Skip confirmation when True (useful for non-interactive runs)
    """
    # Import models to populate Base.metadata before running DDL
    import app.db.models  # noqa: F401

    engine = _build_engine(database_url)

    # Optional interactive confirmation when not bypassed
    if not assume_yes:
        dsn_display = database_url or settings.DATABASE_URL
        try:
            _url = make_url(dsn_display)
            if _url.password:
                _url = _url.set(password="***")
            dsn_display = str(_url)
        except Exception:
            pass
        resp = input(
            f"⚠️  This will DROP ALL TABLES in: {dsn_display}\nAre you sure you want to continue? [y/N]: "
        ).strip().lower()
        if resp not in {"y", "yes"}:
            print("Aborted.")
            await engine.dispose()
            return

    async with engine.begin() as conn:
        # Drop and recreate schema in a single transaction
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()


# Backwards/alias for programmatic imports referenced in docs
async def flush_seed_database(database_url: Optional[str] = None, assume_yes: bool = False) -> None:
    await flush_database(database_url=database_url, assume_yes=assume_yes)


def _main() -> None:  # Manual quick run support
    asyncio.run(flush_database(assume_yes=False))


if __name__ == "__main__":
    _main()

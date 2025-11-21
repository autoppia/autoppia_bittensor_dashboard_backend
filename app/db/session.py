from __future__ import annotations

from collections.abc import AsyncGenerator
import logging

import asyncpg
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InterfaceError as SQLInterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base

logger = logging.getLogger(__name__)


def _redact_dsn(dsn: str) -> str:
    """Render a DSN string with password redacted for safe logging."""
    try:
        u = make_url(dsn)
        # Force a visible placeholder so we don't rely on driver hiding behavior
        return str(u.set(password="***"))
    except Exception:
        # Best‑effort fallback
        return dsn.replace("@", "@***:") if "://" in dsn else dsn


# Ensure we are using the async variant of PostgreSQL (postgresql+asyncpg)
database_url = settings.DATABASE_URL
if not database_url:
    raise ValueError("DATABASE_URL must be configured - PostgreSQL is required")

try:
    url = make_url(database_url)
    driver = url.drivername
except Exception as e:
    raise ValueError(f"Invalid DATABASE_URL: {e}") from e

# Log the configured URL (redacted)
logger.info("DB init: configured DATABASE_URL=%s", _redact_dsn(settings.DATABASE_URL))

# Force asyncpg driver for PostgreSQL
if driver.startswith("postgresql"):
    # If already using asyncpg, keep it; otherwise force it
    if "+asyncpg" not in driver:
        database_url = str(url.set(drivername="postgresql+asyncpg"))
elif driver in {"postgres"}:
    # Convert generic 'postgres' to 'postgresql+asyncpg'
    database_url = str(url.set(drivername="postgresql+asyncpg"))
else:
    raise ValueError(
        f"Unsupported database driver: {driver}. Only PostgreSQL is supported."
    )

# Log the resolved driver/DSN that will actually be used
try:
    resolved = make_url(database_url)
    logger.info(
        "DB init: resolved driver=%s dsn=%s",
        resolved.drivername,
        _redact_dsn(database_url),
    )
except Exception:
    pass

# Create async engine and session factory
engine = create_async_engine(
    database_url,
    echo=False,
    future=True,
    pool_size=20,  # keep pool bounded; DB has max_connections=250
    max_overflow=20,  # allow short bursts without exhausting slots
    pool_timeout=30,  # fail fast when pool is exhausted
    pool_recycle=300,  # recycle connections to avoid stale sockets
    pool_pre_ping=True,  # verify connections before use
    connect_args={
        # Timeout for establishing a connection (seconds)
        "timeout": 10,
        # Apply server-side statement timeout to avoid long-lived queries
        "server_settings": {
            "statement_timeout": "30000",  # 30s
        },
    },
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        # Handle connection errors during session close gracefully
        # These errors occur when concurrent operations leave the connection
        # in an inconsistent state. We catch and log them but don't propagate,
        # as the connection pool will handle broken connections automatically.
        try:
            await session.close()
        except (
            AsyncAdapt_asyncpg_dbapi.InterfaceError,
            asyncpg.exceptions.InternalClientError,
            asyncpg.exceptions.ConnectionDoesNotExistError,
            AsyncAdapt_asyncpg_dbapi.Error,  # Catch other asyncpg errors
            SQLInterfaceError,  # SQLAlchemy wraps asyncpg errors
            DBAPIError,  # Base class for all DBAPI errors
        ) as e:
            # Connection is in an inconsistent state due to concurrent operations
            # The pool will detect and remove broken connections on next use
            # (pool_pre_ping=True ensures connections are verified)
            logger.debug(
                "Connection error during session close (concurrent operation): %s",
                str(e),
            )
        except Exception as e:
            logger.error("Unexpected error during session close: %s", str(e))
            raise


async def init_db() -> None:
    """Create database schema if it does not exist."""
    # Import models dynamically to ensure metadata is populated
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

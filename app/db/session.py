from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base

# Ensure we are using the async variant of the configured driver (e.g. postgresql+asyncpg)
database_url = settings.DATABASE_URL
url = make_url(database_url)
driver = url.drivername
if driver in {"postgres", "postgresql"} or (
    driver.startswith("postgresql") and "+asyncpg" not in driver
):
    database_url = str(url.set(drivername="postgresql+asyncpg"))
elif driver == "sqlite":
    database_url = str(url.set(drivername="sqlite+aiosqlite"))

# Create async engine and session factory
engine = create_async_engine(database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create database schema if it does not exist."""
    # Import models dynamically to ensure metadata is populated
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

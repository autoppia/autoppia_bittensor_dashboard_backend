from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import pytest
from httpx import ASGITransport, AsyncClient

# Configure test database before application modules are imported
TEST_DB_PATH = (Path(__file__).parent / "test.db").resolve()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

PROJECT_ROOT = TEST_DB_PATH.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def reset_database():
    """Ensure the SQL schema is rebuilt and clean for every test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    """Provide an AsyncClient with the FastAPI app."""
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    await app.router.shutdown()


@pytest.fixture
async def db_session():
    """Provide a database session for assertions."""
    async with AsyncSessionLocal() as session:
        yield session

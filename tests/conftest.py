from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Configure test database before application modules are imported
os.environ["DATABASE_URL"] = "postgresql+asyncpg://autoppia:password@127.0.0.1/autoppia_test"
os.environ.setdefault("TESTING", "true")

# Configure AWS defaults for tests before application settings are loaded
os.environ.setdefault("AWS_REGION", "eu-west-1")
os.environ.setdefault("AWS_S3_BUCKET", "autoppia-subnet-test")
os.environ.setdefault("AWS_S3_GIF_PREFIX", "gifs")
os.environ.setdefault(
    "AWS_S3_PUBLIC_BASE_URL",
    "https://autoppia-subnet-test.s3.eu-west-1.amazonaws.com",
)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault(
    "ASSET_BASE_URL",
    "https://autoppia-subnet-test.s3.eu-west-1.amazonaws.com",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)


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

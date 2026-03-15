from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


# Configure test database before application modules are imported.
# Allow overriding DB explicitly via DATABASE_URL; otherwise derive it from the
# same POSTGRES_* env vars used by the app so local/prod-like test setups work.
def _derive_test_database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    candidates = []
    environment = (os.getenv("ENVIRONMENT") or "").strip().upper()
    if environment:
        candidates.append(environment)
    candidates.extend(["DEVELOPMENT", "LOCAL", "PRODUCTION", ""])

    def _first(base_name: str, default: str) -> str:
        for suffix in candidates:
            key = f"{base_name}_{suffix}" if suffix else base_name
            value = os.getenv(key)
            if value not in (None, ""):
                return value
        return default

    user = _first("POSTGRES_USER", "autoppia_user")
    password = _first("POSTGRES_PASSWORD", "password")
    host = _first("POSTGRES_HOST", "127.0.0.1")
    port = _first("POSTGRES_PORT", "5432")
    database = _first("POSTGRES_DB", "autoppia_test")
    return f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"


os.environ.setdefault("DATABASE_URL", _derive_test_database_url())
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

import app.db.models  # noqa: F401,E402
from app.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.round_config_service import ConfigSeasonRound, set_config_season_round_cache  # noqa: E402

# Use default pytest-asyncio event loop (function-scoped, asyncio: mode=auto)


@pytest_asyncio.fixture(autouse=True)
async def reset_database(request):
    """Ensure the PostgreSQL schema is rebuilt and clean for every test."""
    if request.node.get_closest_marker("no_db"):
        yield
        return

    async def _canonical_schema_present(conn) -> bool:
        rows = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        available = set(rows)
        required = {"round_validators", "rounds", "seasons"}
        return required.issubset(available)

    async def _truncate_public_tables(conn) -> None:
        table_names = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
        if not table_names:
            return
        joined = ", ".join(f'public."{name}"' for name in table_names)
        await conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))

    async def _reset(conn) -> None:
        if await _canonical_schema_present(conn):
            await _truncate_public_tables(conn)
            return
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    async with engine.begin() as conn:
        await _reset(conn)
    yield
    await engine.dispose()
    async with engine.begin() as conn:
        await _reset(conn)


@pytest_asyncio.fixture
async def client():
    """Provide an AsyncClient with the FastAPI app."""
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    await app.router.shutdown()


@pytest_asyncio.fixture
async def seeded_runtime_round_config():
    """Seed the singleton runtime config rows required by round lifecycle endpoints."""
    main_validator_uid = 1001
    main_validator_hotkey = "5FHeaderHotkey111111111111111111111111111111"
    cfg = ConfigSeasonRound(
        round_size_epochs=float(settings.ROUND_SIZE_EPOCHS),
        season_size_epochs=float(settings.SEASON_SIZE_EPOCHS),
        minimum_start_block=int(settings.MINIMUM_START_BLOCK),
        blocks_per_epoch=int(settings.BLOCKS_PER_EPOCH),
    )

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO config_app_runtime (
                    id,
                    main_validator_uid,
                    main_validator_hotkey,
                    updated_at,
                    created_at
                )
                VALUES (1, :uid, :hotkey, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    main_validator_uid = EXCLUDED.main_validator_uid,
                    main_validator_hotkey = EXCLUDED.main_validator_hotkey,
                    updated_at = NOW()
                """
            ),
            {"uid": main_validator_uid, "hotkey": main_validator_hotkey},
        )
        await conn.execute(
            text(
                """
                INSERT INTO config_season_round (
                    id,
                    round_size_epochs,
                    season_size_epochs,
                    minimum_start_block,
                    blocks_per_epoch,
                    updated_at,
                    updated_by_validator_uid
                )
                VALUES (1, :round_size_epochs, :season_size_epochs, :minimum_start_block, :blocks_per_epoch, NOW(), :uid)
                ON CONFLICT (id) DO UPDATE SET
                    round_size_epochs = EXCLUDED.round_size_epochs,
                    season_size_epochs = EXCLUDED.season_size_epochs,
                    minimum_start_block = EXCLUDED.minimum_start_block,
                    blocks_per_epoch = EXCLUDED.blocks_per_epoch,
                    updated_at = NOW(),
                    updated_by_validator_uid = EXCLUDED.updated_by_validator_uid
                """
            ),
            {
                "round_size_epochs": cfg.round_size_epochs,
                "season_size_epochs": cfg.season_size_epochs,
                "minimum_start_block": cfg.minimum_start_block,
                "blocks_per_epoch": cfg.blocks_per_epoch,
                "uid": main_validator_uid,
            },
        )

    set_config_season_round_cache(cfg)
    yield {
        "main_validator_uid": main_validator_uid,
        "main_validator_hotkey": main_validator_hotkey,
        "round_size_epochs": cfg.round_size_epochs,
        "season_size_epochs": cfg.season_size_epochs,
        "minimum_start_block": cfg.minimum_start_block,
        "blocks_per_epoch": cfg.blocks_per_epoch,
    }
    set_config_season_round_cache(None)


@pytest_asyncio.fixture
async def configured_client(seeded_runtime_round_config):
    """Client with runtime round config seeded before app startup."""
    await app.router.startup()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    await app.router.shutdown()


@pytest_asyncio.fixture
async def db_session():
    """Provide a database session for assertions."""
    async with AsyncSessionLocal() as session:
        yield session

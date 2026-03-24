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
        required = {"validator_rounds", "validator_round_validators", "config_app_runtime"}
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

    async def _ensure_run_early_stop_columns(conn) -> None:
        statements = [
            "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS round_validator_id INTEGER NULL",
            "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS tasks_attempted INTEGER NULL",
            "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_reason VARCHAR(128) NULL",
            "ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS early_stop_message TEXT NULL",
        ]
        for sql in statements:
            await conn.execute(text(sql))
        round_validators_exists = await conn.scalar(text("SELECT to_regclass('public.round_validators') IS NOT NULL"))
        if not round_validators_exists:
            return
        rv_round_validator_id_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'round_validators'
                      AND column_name = 'round_validator_id'
                )
                """
            )
        )
        rv_validator_round_id_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'round_validators'
                      AND column_name = 'validator_round_id'
                )
                """
            )
        )
        if not rv_round_validator_id_exists or not rv_validator_round_id_exists:
            return
        await conn.execute(
            text(
                """
                UPDATE miner_evaluation_runs mer
                SET round_validator_id = rv.round_validator_id
                FROM round_validators rv
                WHERE mer.round_validator_id IS NULL
                  AND rv.validator_round_id = mer.validator_round_id
                """
            )
        )

    async def _ensure_legacy_compat_columns(conn) -> None:
        """
        Patch older local schemas used by tests so app startup views can be created.
        """
        statements = [
            """
            CREATE TABLE IF NOT EXISTS config_app_runtime (
                id INTEGER PRIMARY KEY,
                main_validator_uid INTEGER,
                main_validator_hotkey VARCHAR(128),
                minimum_validator_version VARCHAR(64),
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS config_season_round (
                id INTEGER PRIMARY KEY,
                round_size_epochs DOUBLE PRECISION,
                season_size_epochs DOUBLE PRECISION,
                minimum_start_block INTEGER,
                blocks_per_epoch INTEGER,
                updated_at TIMESTAMPTZ,
                updated_by_validator_uid INTEGER
            );
            """,
            """
            DO $$
            DECLARE rv_kind CHAR;
            BEGIN
                SELECT c.relkind
                INTO rv_kind
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'round_validators'
                LIMIT 1;

                IF rv_kind IN ('r', 'p') THEN
                    ALTER TABLE round_validators
                        ADD COLUMN IF NOT EXISTS s3_logs_url TEXT;
                END IF;
            END
            $$;
            """,
            """
            CREATE TABLE IF NOT EXISTS seasons (
                season_id SERIAL PRIMARY KEY,
                season_number INTEGER UNIQUE,
                start_block INTEGER,
                end_block INTEGER,
                end_at TIMESTAMPTZ,
                start_epoch INTEGER,
                end_epoch INTEGER,
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                status VARCHAR(32),
                required_improvement_pct DOUBLE PRECISION,
                leader_miner_uid INTEGER,
                leader_reward DOUBLE PRECISION,
                leader_github_url TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS rounds (
                round_id SERIAL PRIMARY KEY,
                season_id INTEGER,
                round_number_in_season INTEGER,
                start_block INTEGER,
                end_block INTEGER,
                planned_start_block INTEGER,
                planned_end_block INTEGER,
                start_epoch INTEGER,
                end_epoch INTEGER,
                started_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                status VARCHAR(32),
                consensus_status VARCHAR(32),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS round_validator_miners (
                id SERIAL PRIMARY KEY,
                round_validator_id INTEGER NOT NULL,
                round_id INTEGER,
                miner_uid INTEGER,
                miner_hotkey VARCHAR(128),
                miner_coldkey VARCHAR(128),
                name VARCHAR(256),
                image_url VARCHAR(512),
                github_url VARCHAR(512),
                is_sota BOOLEAN DEFAULT FALSE,
                version VARCHAR(64),
                local_avg_reward DOUBLE PRECISION,
                local_avg_eval_score DOUBLE PRECISION,
                local_avg_eval_time DOUBLE PRECISION,
                local_avg_eval_cost DOUBLE PRECISION,
                local_tasks_received INTEGER,
                local_tasks_success INTEGER,
                post_consensus_rank INTEGER,
                post_consensus_avg_reward DOUBLE PRECISION,
                post_consensus_avg_eval_score DOUBLE PRECISION,
                post_consensus_avg_eval_time DOUBLE PRECISION,
                post_consensus_avg_eval_cost DOUBLE PRECISION,
                post_consensus_tasks_received INTEGER,
                post_consensus_tasks_success INTEGER,
                weight DOUBLE PRECISION,
                subnet_price DOUBLE PRECISION,
                best_local_rank INTEGER,
                best_local_reward DOUBLE PRECISION,
                best_local_eval_score DOUBLE PRECISION,
                best_local_eval_time DOUBLE PRECISION,
                best_local_eval_cost DOUBLE PRECISION,
                best_local_tasks_received INTEGER,
                best_local_tasks_success INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """,
            """
            DO $$
            BEGIN
                IF to_regclass('public.config_app_runtime') IS NOT NULL THEN
                    ALTER TABLE config_app_runtime
                        ADD COLUMN IF NOT EXISTS main_validator_hotkey VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS minimum_validator_version VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ,
                        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
                END IF;
            END
            $$;
            """,
            """
            DO $$
            BEGIN
                IF to_regclass('public.seasons') IS NOT NULL THEN
                    ALTER TABLE seasons
                        ADD COLUMN IF NOT EXISTS end_at TIMESTAMPTZ,
                        ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ,
                        ADD COLUMN IF NOT EXISTS required_improvement_pct DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS leader_miner_uid INTEGER,
                        ADD COLUMN IF NOT EXISTS leader_reward DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS leader_github_url TEXT;
                END IF;
            END
            $$;
            """,
            """
            DO $$
            BEGIN
                IF to_regclass('public.rounds') IS NOT NULL THEN
                    ALTER TABLE rounds
                        ADD COLUMN IF NOT EXISTS planned_start_block INTEGER,
                        ADD COLUMN IF NOT EXISTS planned_end_block INTEGER,
                        ADD COLUMN IF NOT EXISTS consensus_status VARCHAR(32);
                END IF;
            END
            $$;
            """,
            """
            DO $$
            DECLARE rv_kind CHAR;
            BEGIN
                SELECT c.relkind
                INTO rv_kind
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'round_validators'
                LIMIT 1;

                -- If legacy table exists with this name, keep it untouched.
                IF rv_kind IN ('r', 'p') THEN
                    ALTER TABLE round_validators
                        ADD COLUMN IF NOT EXISTS post_consensus_json JSONB,
                        ADD COLUMN IF NOT EXISTS ipfs_uploaded JSONB,
                        ADD COLUMN IF NOT EXISTS ipfs_downloaded JSONB,
                        ADD COLUMN IF NOT EXISTS validator_state JSONB,
                        ADD COLUMN IF NOT EXISTS s3_logs_url TEXT;
                    RETURN;
                END IF;

                IF rv_kind = 'v' THEN
                    EXECUTE 'DROP VIEW IF EXISTS round_validators';
                END IF;

                IF to_regclass('public.round_validators') IS NULL THEN
                    EXECUTE '
                        CREATE TABLE round_validators (
                            round_validator_id SERIAL PRIMARY KEY,
                            round_id INTEGER,
                            season_number INTEGER,
                            round_number_in_season INTEGER,
                            start_block INTEGER,
                            end_block INTEGER,
                            start_epoch INTEGER,
                            end_epoch INTEGER,
                            pending_round_link BOOLEAN DEFAULT FALSE,
                            is_main_validator BOOLEAN DEFAULT FALSE,
                            validator_uid INTEGER,
                            validator_hotkey VARCHAR(128),
                            validator_coldkey VARCHAR(128),
                            validator_round_id VARCHAR(128) UNIQUE,
                            name VARCHAR(256),
                            image_url VARCHAR(512),
                            version VARCHAR(64),
                            stake DOUBLE PRECISION,
                            vtrust DOUBLE PRECISION,
                            config JSONB,
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ,
                            post_consensus_json JSONB,
                            ipfs_uploaded JSONB,
                            ipfs_downloaded JSONB,
                            validator_state JSONB,
                            s3_logs_url TEXT,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    ';
                END IF;
            END
            $$;
            """,
        ]
        for sql in statements:
            await conn.execute(text(sql))

    async def _reset(conn) -> None:
        if await _canonical_schema_present(conn):
            await _ensure_run_early_stop_columns(conn)
            await _ensure_legacy_compat_columns(conn)
            await _truncate_public_tables(conn)
            return
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
        # Fresh schema path also needs legacy compatibility tables/columns
        # before seeding singleton config rows.
        await _ensure_run_early_stop_columns(conn)
        await _ensure_legacy_compat_columns(conn)

    async def _seed_runtime_config_defaults(conn) -> None:
        main_validator_uid = 1001
        main_validator_hotkey = "5FHeaderHotkey111111111111111111111111111111"
        await conn.execute(
            text(
                """
                INSERT INTO config_app_runtime (
                    id, main_validator_uid, main_validator_hotkey, created_at, updated_at
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
                    id, round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch, updated_at, updated_by_validator_uid
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
                "round_size_epochs": float(settings.ROUND_SIZE_EPOCHS),
                "season_size_epochs": float(settings.SEASON_SIZE_EPOCHS),
                "minimum_start_block": int(settings.MINIMUM_START_BLOCK),
                "blocks_per_epoch": int(settings.BLOCKS_PER_EPOCH),
                "uid": main_validator_uid,
            },
        )

    await engine.dispose()
    async with engine.begin() as conn:
        await _reset(conn)
        await _seed_runtime_config_defaults(conn)
    yield
    await engine.dispose()
    async with engine.begin() as conn:
        await _reset(conn)
        await _seed_runtime_config_defaults(conn)


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

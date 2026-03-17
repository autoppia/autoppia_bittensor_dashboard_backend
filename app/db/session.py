from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import asyncpg
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
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
    raise ValueError(f"Unsupported database driver: {driver}. Only PostgreSQL is supported.")

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
        "timeout": 15,  # Aumentado de 10 a 15 segundos
        # Apply server-side statement timeout to avoid long-lived queries
        "command_timeout": 30,  # Aumentado de 10 a 30 segundos
        "server_settings": {
            "statement_timeout": "30000",  # 30s (aumentado para queries complejas)
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
        # Roll back any failed or uncommitted transaction so the connection is not
        # returned to the pool in "aborted transaction" state (InFailedSQLTransactionError).
        try:
            await session.rollback()
        except Exception:  # noqa: S110
            pass
        # Handle connection errors during session close gracefully
        try:
            await session.close()
        except (
            AsyncAdapt_asyncpg_dbapi.InterfaceError,
            asyncpg.exceptions.InternalClientError,
            asyncpg.exceptions.ConnectionDoesNotExistError,
            AsyncAdapt_asyncpg_dbapi.Error,  # Catch other asyncpg errors
            DBAPIError,  # Base class for all DBAPI errors (includes SQLInterfaceError)
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
    """Create tables (if not exist) and recreate views/triggers needed by the API."""
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # ------------------------------------------------------------------
        # Views — recreated on every start so schema changes are picked up
        # ------------------------------------------------------------------
        for view_sql in (
            """
            CREATE OR REPLACE VIEW validator_rounds AS
            SELECT
                rv.round_validator_id AS id,
                rv.validator_round_id::TEXT AS validator_round_id,
                COALESCE(s.season_number, rv.season_number) AS season_number,
                COALESCE(r.round_number_in_season, rv.round_number_in_season) AS round_number_in_season,
                COALESCE(r.start_block, rv.start_block, 0) AS start_block,
                COALESCE(r.end_block, rv.end_block) AS end_block,
                COALESCE(r.start_epoch::INTEGER, rv.start_epoch, 0) AS start_epoch,
                COALESCE(r.end_epoch::INTEGER, rv.end_epoch) AS end_epoch,
                COALESCE(EXTRACT(EPOCH FROM r.started_at)::DOUBLE PRECISION, EXTRACT(EPOCH FROM rv.started_at)::DOUBLE PRECISION, 0.0) AS started_at,
                COALESCE(EXTRACT(EPOCH FROM r.ended_at)::DOUBLE PRECISION, EXTRACT(EPOCH FROM rv.finished_at)::DOUBLE PRECISION) AS ended_at,
                COALESCE(t.tasks_count, 0) AS n_tasks,
                COALESCE(r.status, 'active')::VARCHAR(32) AS status,
                rv.post_consensus_json AS validator_summary,
                rv.s3_logs_url AS s3_logs_url,
                NULL::INTEGER AS winner_uid,
                NULL::DOUBLE PRECISION AS winner_score,
                NULL::INTEGER AS reigning_uid_before_round,
                NULL::DOUBLE PRECISION AS reigning_score_before_round,
                NULL::INTEGER AS top_candidate_uid,
                NULL::DOUBLE PRECISION AS top_candidate_score,
                NULL::DOUBLE PRECISION AS required_improvement_pct,
                NULL::BOOLEAN AS dethroned,
                rv.created_at,
                rv.updated_at
            FROM round_validators rv
            LEFT JOIN rounds r ON r.round_id = rv.round_id
            LEFT JOIN seasons s ON s.season_id = r.season_id
            LEFT JOIN (
                SELECT tasks.round_validator_id, COUNT(*)::INTEGER AS tasks_count
                FROM tasks
                GROUP BY tasks.round_validator_id
            ) t ON t.round_validator_id = rv.round_validator_id
            """,
            """
            CREATE OR REPLACE VIEW validator_round_validators AS
            SELECT
                rv.round_validator_id AS id,
                rv.validator_round_id::TEXT AS validator_round_id,
                rv.validator_uid,
                rv.validator_hotkey,
                rv.validator_coldkey,
                rv.name,
                rv.stake,
                rv.vtrust,
                rv.image_url,
                rv.version,
                rv.config,
                rv.created_at,
                rv.updated_at
            FROM round_validators rv
            """,
            """
            CREATE OR REPLACE VIEW validator_round_miners AS
            SELECT
                rvm.id,
                rv.validator_round_id::TEXT AS validator_round_id,
                rvm.miner_uid,
                rvm.miner_hotkey,
                rvm.miner_coldkey,
                COALESCE(rvm.name, CONCAT('miner ', rvm.miner_uid)::VARCHAR(256))::VARCHAR(256) AS name,
                rvm.image_url,
                rvm.github_url,
                COALESCE(rvm.is_sota, FALSE) AS is_sota,
                rvm.version,
                rvm.created_at,
                rvm.updated_at
            FROM round_validator_miners rvm
            JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
            """,
            """
            CREATE OR REPLACE VIEW validator_round_summary_miners AS
            SELECT
                rvm.id,
                rv.validator_round_id::TEXT AS validator_round_id,
                rvm.miner_uid,
                rvm.miner_hotkey,
                rvm.local_avg_reward,
                rvm.local_avg_eval_score,
                rvm.local_avg_eval_time,
                rvm.local_avg_eval_cost,
                rvm.local_tasks_received,
                rvm.local_tasks_success,
                rvm.post_consensus_rank,
                rvm.post_consensus_avg_reward,
                rvm.post_consensus_avg_eval_score,
                rvm.post_consensus_avg_eval_time,
                rvm.post_consensus_avg_eval_cost,
                rvm.post_consensus_tasks_received,
                rvm.post_consensus_tasks_success,
                rvm.weight,
                rvm.subnet_price,
                rvm.created_at,
                rvm.updated_at
            FROM round_validator_miners rvm
            JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
            """,
        ):
            await conn.execute(text(view_sql))

        # ------------------------------------------------------------------
        # Triggers — prevent malformed block/time ranges being persisted
        # ------------------------------------------------------------------
        for fn_sql, drop_sql, trigger_sql in (
            (
                """
                CREATE OR REPLACE FUNCTION normalize_round_boundaries()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.start_block IS NOT NULL AND NEW.end_block IS NOT NULL AND NEW.end_block < NEW.start_block THEN
                        NEW.end_block := NEW.start_block;
                    END IF;
                    IF NEW.started_at IS NOT NULL AND NEW.ended_at IS NOT NULL AND NEW.ended_at < NEW.started_at THEN
                        NEW.ended_at := NEW.started_at;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """,
                "DROP TRIGGER IF EXISTS trg_normalize_round_boundaries ON rounds",
                """
                CREATE TRIGGER trg_normalize_round_boundaries
                BEFORE INSERT OR UPDATE ON rounds
                FOR EACH ROW EXECUTE FUNCTION normalize_round_boundaries()
                """,
            ),
            (
                """
                CREATE OR REPLACE FUNCTION normalize_round_validator_boundaries()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.start_block IS NOT NULL AND NEW.end_block IS NOT NULL AND NEW.end_block < NEW.start_block THEN
                        NEW.end_block := NEW.start_block;
                    END IF;
                    IF NEW.started_at IS NOT NULL AND NEW.finished_at IS NOT NULL AND NEW.finished_at < NEW.started_at THEN
                        NEW.finished_at := NEW.started_at;
                    END IF;
                    IF NEW.started_at IS NOT NULL AND NEW.started_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN
                        NEW.started_at := NULL;
                    END IF;
                    IF NEW.finished_at IS NOT NULL AND NEW.finished_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN
                        NEW.finished_at := NULL;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """,
                "DROP TRIGGER IF EXISTS trg_normalize_round_validator_boundaries ON round_validators",
                """
                CREATE TRIGGER trg_normalize_round_validator_boundaries
                BEFORE INSERT OR UPDATE ON round_validators
                FOR EACH ROW EXECUTE FUNCTION normalize_round_validator_boundaries()
                """,
            ),
        ):
            await conn.execute(text(fn_sql))
            await conn.execute(text(drop_sql))
            await conn.execute(text(trigger_sql))

from __future__ import annotations

from collections.abc import AsyncGenerator
import logging

import asyncpg
from sqlalchemy import text
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
    """Crea tablas si no existen y añade columnas que falten (conecta con Postgres usando DATABASE_URL)."""
    import app.db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS s3_logs JSONB"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS winner_uid INTEGER"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS winner_score DOUBLE PRECISION"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS reigning_uid_before_round INTEGER"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS reigning_score_before_round DOUBLE PRECISION"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS top_candidate_uid INTEGER"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS top_candidate_score DOUBLE PRECISION"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS required_improvement_pct DOUBLE PRECISION"))
        await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS dethroned BOOLEAN"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_validator_rounds_winner_uid ON validator_rounds(winner_uid)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_validator_rounds_reigning_uid_before_round ON validator_rounds(reigning_uid_before_round)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_validator_rounds_top_candidate_uid ON validator_rounds(top_candidate_uid)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_validator_rounds_dethroned ON validator_rounds(dethroned)"))
        await conn.execute(text("COMMENT ON COLUMN validator_rounds.s3_logs IS 'Structured S3 log references captured during validator finish (task logs / round artifacts).'"))
        await conn.execute(
            text(
                """
                UPDATE validator_rounds
                SET
                  winner_uid = COALESCE(
                    winner_uid,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'miner_uid') ~ '^[0-9]+$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'miner_uid')::INTEGER
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'uid') ~ '^[0-9]+$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'uid')::INTEGER
                      ELSE NULL
                    END
                  ),
                  winner_score = COALESCE(
                    winner_score,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'score') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'score')::DOUBLE PRECISION
                      ELSE NULL
                    END
                  ),
                  reigning_uid_before_round = COALESCE(
                    reigning_uid_before_round,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_uid_before_round') ~ '^[0-9]+$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_uid_before_round')::INTEGER
                      ELSE NULL
                    END
                  ),
                  reigning_score_before_round = COALESCE(
                    reigning_score_before_round,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_score_before_round') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_score_before_round')::DOUBLE PRECISION
                      ELSE NULL
                    END
                  ),
                  top_candidate_uid = COALESCE(
                    top_candidate_uid,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_uid') ~ '^[0-9]+$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_uid')::INTEGER
                      ELSE NULL
                    END
                  ),
                  top_candidate_score = COALESCE(
                    top_candidate_score,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_score') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_score')::DOUBLE PRECISION
                      ELSE NULL
                    END
                  ),
                  required_improvement_pct = COALESCE(
                    required_improvement_pct,
                    CASE
                      WHEN (validator_summary->'evaluation_post_consensus'->'season_summary'->>'required_improvement_pct') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                      THEN (validator_summary->'evaluation_post_consensus'->'season_summary'->>'required_improvement_pct')::DOUBLE PRECISION
                      WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'required_improvement_pct') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'required_improvement_pct')::DOUBLE PRECISION
                      ELSE NULL
                    END
                  ),
                  dethroned = COALESCE(
                    dethroned,
                    CASE
                      WHEN LOWER(COALESCE(validator_summary->'evaluation_post_consensus'->'season_summary'->>'dethroned', '')) IN ('true', 'false')
                      THEN (validator_summary->'evaluation_post_consensus'->'season_summary'->>'dethroned')::BOOLEAN
                      WHEN LOWER(COALESCE(validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'dethroned', '')) IN ('true', 'false')
                      THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'dethroned')::BOOLEAN
                      ELSE NULL
                    END
                  )
                WHERE validator_summary IS NOT NULL
                """
            )
        )
        await conn.execute(text("ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS is_reused BOOLEAN NOT NULL DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS reused_from_agent_run_id VARCHAR(128) NULL"))
        await conn.execute(text("ALTER TABLE miner_evaluation_runs DROP CONSTRAINT IF EXISTS fk_miner_evaluation_runs_reused_from"))
        await conn.execute(
            text(
                "ALTER TABLE miner_evaluation_runs ADD CONSTRAINT fk_miner_evaluation_runs_reused_from "
                "FOREIGN KEY (reused_from_agent_run_id) REFERENCES miner_evaluation_runs(agent_run_id) ON DELETE SET NULL"
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_miner_evaluation_runs_reused_from ON miner_evaluation_runs(reused_from_agent_run_id) WHERE reused_from_agent_run_id IS NOT NULL"))
        await conn.execute(text("ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS zero_reason VARCHAR(128) NULL"))
        await conn.execute(text("ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS zero_reason VARCHAR(128) NULL"))
        await conn.execute(text("ALTER TABLE evaluations DROP COLUMN IF EXISTS feedback"))
        await conn.execute(
            text(
                """
                DO $$ BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'evaluations'
                      AND column_name = 'eval_score'
                  ) THEN
                    IF NOT EXISTS (
                      SELECT 1
                      FROM information_schema.columns
                      WHERE table_schema = current_schema()
                        AND table_name = 'evaluations'
                        AND column_name = 'evaluation_score'
                    ) THEN
                      ALTER TABLE evaluations RENAME COLUMN eval_score TO evaluation_score;
                    ELSE
                      UPDATE evaluations
                      SET evaluation_score = COALESCE(evaluation_score, eval_score)
                      WHERE evaluation_score IS NULL;
                      ALTER TABLE evaluations DROP COLUMN IF EXISTS eval_score;
                    END IF;
                  END IF;
                END $$;
                """
            )
        )
        # Rename evaluations.meta -> extra_info (solo si existe la columna meta)
        await conn.execute(
            text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='evaluations' AND column_name='meta') THEN "
                "ALTER TABLE evaluations RENAME COLUMN meta TO extra_info; "
                "END IF; END $$"
            )
        )
        # Rellenar zero_reason en evaluaciones ya guardadas con score 0 y extra_info.timeout = true
        await conn.execute(text("UPDATE evaluations SET zero_reason = 'task_timeout' WHERE evaluation_score = 0 AND zero_reason IS NULL AND (extra_info->>'timeout') = 'true'"))
        # Eliminar columna extra_info/meta de validator_rounds (todo está en validator_summary)
        await conn.execute(
            text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='validator_rounds' AND column_name='extra_info') THEN "
                "ALTER TABLE validator_rounds DROP COLUMN extra_info; "
                "END IF; "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='validator_rounds' AND column_name='meta') THEN "
                "ALTER TABLE validator_rounds DROP COLUMN meta; "
                "END IF; END $$"
            )
        )
        # Eliminar columnas n_winners y n_miners de validator_rounds (redundantes con validator_summary.round)
        await conn.execute(
            text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='validator_rounds' AND column_name='n_winners') THEN "
                "ALTER TABLE validator_rounds DROP COLUMN n_winners; "
                "END IF; "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='validator_rounds' AND column_name='n_miners') THEN "
                "ALTER TABLE validator_rounds DROP COLUMN n_miners; "
                "END IF; END $$"
            )
        )

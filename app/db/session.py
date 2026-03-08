from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import asyncpg
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import InterfaceError as SQLInterfaceError
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
        # ------------------------------------------------------------------
        # Canonical new schema (must exist even after full public schema reset)
        # ------------------------------------------------------------------
        for ddl in (
            """
            CREATE TABLE IF NOT EXISTS seasons (
                season_id BIGSERIAL PRIMARY KEY,
                season_number INTEGER NOT NULL UNIQUE,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                start_block BIGINT NULL,
                end_block BIGINT NULL,
                start_at TIMESTAMPTZ NULL,
                end_at TIMESTAMPTZ NULL,
                required_improvement_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05,
                leader_miner_uid INTEGER NULL,
                leader_reward DOUBLE PRECISION NULL,
                leader_github_url TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rounds (
                round_id BIGSERIAL PRIMARY KEY,
                season_id BIGINT NOT NULL REFERENCES seasons(season_id) ON DELETE CASCADE,
                round_number_in_season INTEGER NOT NULL,
                start_block BIGINT NULL,
                end_block BIGINT NULL,
                planned_start_block BIGINT NULL,
                planned_end_block BIGINT NULL,
                start_epoch INTEGER NULL,
                end_epoch INTEGER NULL,
                started_at TIMESTAMPTZ NULL,
                ended_at TIMESTAMPTZ NULL,
                opened_by_validator_uid INTEGER NULL,
                closed_by_validator_uid INTEGER NULL,
                authority_mode VARCHAR(16) NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                consensus_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_rounds_season_round UNIQUE (season_id, round_number_in_season)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS round_validators (
                round_validator_id BIGSERIAL PRIMARY KEY,
                round_id BIGINT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
                season_number INTEGER NULL,
                round_number_in_season INTEGER NULL,
                start_block BIGINT NULL,
                end_block BIGINT NULL,
                start_epoch INTEGER NULL,
                end_epoch INTEGER NULL,
                pending_round_link BOOLEAN NOT NULL DEFAULT FALSE,
                validator_uid INTEGER NULL,
                validator_hotkey VARCHAR(128) NULL,
                validator_coldkey VARCHAR(128) NULL,
                validator_round_id VARCHAR(128) NULL,
                name VARCHAR(256) NULL,
                image_url TEXT NULL,
                version VARCHAR(64) NULL,
                stake DOUBLE PRECISION NULL,
                vtrust DOUBLE PRECISION NULL,
                started_at TIMESTAMPTZ NULL,
                finished_at TIMESTAMPTZ NULL,
                config JSONB NULL,
                local_summary_json JSONB NULL,
                post_consensus_json JSONB NULL,
                post_consensus_summary JSONB NULL,
                ipfs_uploaded JSONB NULL,
                ipfs_downloaded JSONB NULL,
                s3_logs_url TEXT NULL,
                validator_state JSONB NULL,
                validator_iwap_prev_round_json JSONB NULL,
                is_main_validator BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS round_validator_miners (
                id BIGSERIAL PRIMARY KEY,
                round_validator_id BIGINT NOT NULL REFERENCES round_validators(round_validator_id) ON DELETE CASCADE,
                round_id BIGINT REFERENCES rounds(round_id) ON DELETE CASCADE,
                miner_uid INTEGER NOT NULL,
                miner_hotkey VARCHAR(128) NULL,
                miner_coldkey VARCHAR(128) NULL,
                name VARCHAR(256) NULL,
                image_url TEXT NULL,
                github_url TEXT NULL,
                is_sota BOOLEAN NOT NULL DEFAULT FALSE,
                version VARCHAR(64) NULL,
                is_reused BOOLEAN NOT NULL DEFAULT FALSE,
                reused_from_agent_run_id VARCHAR(128) NULL,
                reused_from_round_id BIGINT NULL REFERENCES rounds(round_id) ON DELETE SET NULL,
                local_rank INTEGER NULL,
                local_avg_reward DOUBLE PRECISION NULL,
                local_avg_eval_score DOUBLE PRECISION NULL,
                local_avg_eval_time DOUBLE PRECISION NULL,
                local_tasks_received INTEGER NULL,
                local_tasks_success INTEGER NULL,
                local_avg_eval_cost DOUBLE PRECISION NULL,
                post_consensus_rank INTEGER NULL,
                post_consensus_avg_reward DOUBLE PRECISION NULL,
                post_consensus_avg_eval_score DOUBLE PRECISION NULL,
                post_consensus_avg_eval_time DOUBLE PRECISION NULL,
                post_consensus_tasks_received INTEGER NULL,
                post_consensus_tasks_success INTEGER NULL,
                post_consensus_avg_eval_cost DOUBLE PRECISION NULL,
                best_local_rank INTEGER NULL,
                best_local_reward DOUBLE PRECISION NULL,
                best_local_eval_score DOUBLE PRECISION NULL,
                best_local_eval_time DOUBLE PRECISION NULL,
                best_local_tasks_received INTEGER NULL,
                best_local_tasks_success INTEGER NULL,
                best_local_eval_cost DOUBLE PRECISION NULL,
                weight DOUBLE PRECISION NULL,
                subnet_price DOUBLE PRECISION NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_round_validator_miners_round_validator_miner UNIQUE (round_validator_id, miner_uid)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS round_summary (
                round_summary_id BIGSERIAL PRIMARY KEY,
                round_id BIGINT NOT NULL UNIQUE REFERENCES rounds(round_id) ON DELETE CASCADE,
                leader_before_miner_uid INTEGER NULL,
                leader_before_miner_hotkey VARCHAR(128) NULL,
                leader_before_github_url TEXT NULL,
                leader_before_reward DOUBLE PRECISION NULL,
                candidate_miner_uid INTEGER NULL,
                candidate_miner_hotkey VARCHAR(128) NULL,
                candidate_github_url TEXT NULL,
                candidate_reward DOUBLE PRECISION NULL,
                leader_after_miner_uid INTEGER NULL,
                leader_after_miner_hotkey VARCHAR(128) NULL,
                leader_after_github_url TEXT NULL,
                leader_after_reward DOUBLE PRECISION NULL,
                required_improvement_pct DOUBLE PRECISION NULL,
                required_reward_to_dethrone DOUBLE PRECISION NULL,
                dethroned BOOLEAN NULL,
                validators_count INTEGER NULL,
                miners_evaluated INTEGER NULL,
                tasks_evaluated INTEGER NULL,
                tasks_success INTEGER NULL,
                avg_reward DOUBLE PRECISION NULL,
                avg_eval_score DOUBLE PRECISION NULL,
                avg_eval_time DOUBLE PRECISION NULL,
                avg_eval_cost DOUBLE PRECISION NULL,
                summary_json JSONB NULL,
                post_consensus_summary JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_rounds_season_id ON rounds(season_id)",
            "CREATE INDEX IF NOT EXISTS ix_rounds_season_round ON rounds(season_id, round_number_in_season)",
            "CREATE INDEX IF NOT EXISTS ix_rounds_status ON rounds(status)",
            "CREATE INDEX IF NOT EXISTS ix_round_validators_round_id ON round_validators(round_id)",
            "CREATE INDEX IF NOT EXISTS ix_round_validators_uid ON round_validators(validator_uid)",
            "CREATE INDEX IF NOT EXISTS ux_round_validators_round_uid ON round_validators(round_id, validator_uid)",
            "CREATE INDEX IF NOT EXISTS ix_round_validator_miners_round_id ON round_validator_miners(round_id)",
            "CREATE INDEX IF NOT EXISTS ix_round_validator_miners_miner_uid ON round_validator_miners(miner_uid)",
            "CREATE INDEX IF NOT EXISTS ix_round_validator_miners_round_validator_id ON round_validator_miners(round_validator_id)",
            "CREATE INDEX IF NOT EXISTS ix_round_summary_leader_after_miner_uid ON round_summary(leader_after_miner_uid)",
        ):
            await conn.execute(text(ddl))
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_outcomes'
                    ) THEN
                        INSERT INTO round_summary (
                            round_id,
                            leader_before_miner_uid,
                            leader_before_reward,
                            candidate_miner_uid,
                            candidate_reward,
                            leader_after_miner_uid,
                            leader_after_reward,
                            required_improvement_pct,
                            required_reward_to_dethrone,
                            dethroned,
                            validators_count,
                            miners_evaluated,
                            tasks_evaluated,
                            tasks_success,
                            avg_reward,
                            avg_eval_score,
                            avg_eval_time,
                            summary_json,
                            post_consensus_summary,
                            created_at,
                            updated_at
                        )
                        SELECT
                            ro.round_id,
                            ro.reigning_miner_uid_before_round,
                            ro.reigning_score_before_round,
                            ro.top_candidate_miner_uid,
                            ro.top_candidate_score,
                            ro.winner_miner_uid,
                            ro.winner_score,
                            ro.required_improvement_pct,
                            CASE
                                WHEN ro.reigning_score_before_round IS NOT NULL AND ro.required_improvement_pct IS NOT NULL
                                THEN ro.reigning_score_before_round * (1.0 + ro.required_improvement_pct)
                                ELSE NULL
                            END,
                            ro.dethroned,
                            ro.validators_count,
                            ro.miners_evaluated,
                            ro.tasks_evaluated,
                            ro.tasks_success,
                            ro.avg_reward,
                            ro.avg_eval_score,
                            ro.avg_eval_time,
                            ro.summary_json,
                            ro.post_consensus_summary,
                            COALESCE(ro.created_at, NOW()),
                            COALESCE(ro.updated_at, NOW())
                        FROM round_outcomes ro
                        WHERE NOT EXISTS (
                            SELECT 1 FROM round_summary rs WHERE rs.round_id = ro.round_id
                        );
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                DECLARE
                    rec RECORD;
                    current_season_id BIGINT := NULL;
                    leader_uid INTEGER := NULL;
                    leader_reward DOUBLE PRECISION := NULL;
                    required_pct DOUBLE PRECISION := 0.05;
                    dethroned_flag BOOLEAN := FALSE;
                    threshold_reward DOUBLE PRECISION := NULL;
                BEGIN
                    FOR rec IN
                        SELECT
                            r.season_id,
                            rs.round_id,
                            COALESCE(r.round_number_in_season, 2147483647) AS round_number_in_season,
                            rs.candidate_miner_uid,
                            rs.candidate_reward,
                            rs.required_improvement_pct
                        FROM round_summary rs
                        JOIN rounds r ON r.round_id = rs.round_id
                        ORDER BY r.season_id, COALESCE(r.round_number_in_season, 2147483647), rs.round_id
                    LOOP
                        IF current_season_id IS DISTINCT FROM rec.season_id THEN
                            current_season_id := rec.season_id;
                            leader_uid := NULL;
                            leader_reward := NULL;
                        END IF;

                        required_pct := COALESCE(rec.required_improvement_pct, 0.05);
                        dethroned_flag := FALSE;
                        threshold_reward := NULL;

                        IF rec.candidate_miner_uid IS NOT NULL AND rec.candidate_reward IS NOT NULL THEN
                            IF leader_uid IS NULL OR leader_reward IS NULL THEN
                                leader_uid := rec.candidate_miner_uid;
                                leader_reward := rec.candidate_reward;
                            ELSIF rec.candidate_miner_uid = leader_uid THEN
                                leader_reward := GREATEST(leader_reward, rec.candidate_reward);
                            ELSE
                                threshold_reward := leader_reward * (1.0 + required_pct);
                                IF rec.candidate_reward >= threshold_reward THEN
                                    dethroned_flag := TRUE;
                                    leader_uid := rec.candidate_miner_uid;
                                    leader_reward := rec.candidate_reward;
                                END IF;
                            END IF;
                        END IF;

                        UPDATE round_summary
                        SET
                            leader_before_miner_uid = (
                                SELECT prev.leader_after_miner_uid
                                FROM round_summary prev
                                JOIN rounds pr ON pr.round_id = prev.round_id
                                WHERE pr.season_id = rec.season_id
                                  AND (
                                    COALESCE(pr.round_number_in_season, 2147483647) < rec.round_number_in_season
                                    OR (COALESCE(pr.round_number_in_season, 2147483647) = rec.round_number_in_season AND prev.round_id < rec.round_id)
                                  )
                                ORDER BY COALESCE(pr.round_number_in_season, 2147483647) DESC, prev.round_id DESC
                                LIMIT 1
                            ),
                            leader_before_reward = (
                                SELECT prev.leader_after_reward
                                FROM round_summary prev
                                JOIN rounds pr ON pr.round_id = prev.round_id
                                WHERE pr.season_id = rec.season_id
                                  AND (
                                    COALESCE(pr.round_number_in_season, 2147483647) < rec.round_number_in_season
                                    OR (COALESCE(pr.round_number_in_season, 2147483647) = rec.round_number_in_season AND prev.round_id < rec.round_id)
                                  )
                                ORDER BY COALESCE(pr.round_number_in_season, 2147483647) DESC, prev.round_id DESC
                                LIMIT 1
                            ),
                            leader_after_miner_uid = leader_uid,
                            leader_after_reward = leader_reward,
                            required_improvement_pct = required_pct,
                            required_reward_to_dethrone = (
                                SELECT CASE
                                    WHEN prev.leader_after_reward IS NOT NULL
                                    THEN prev.leader_after_reward * (1.0 + required_pct)
                                    ELSE NULL
                                END
                                FROM round_summary prev
                                JOIN rounds pr ON pr.round_id = prev.round_id
                                WHERE pr.season_id = rec.season_id
                                  AND (
                                    COALESCE(pr.round_number_in_season, 2147483647) < rec.round_number_in_season
                                    OR (COALESCE(pr.round_number_in_season, 2147483647) = rec.round_number_in_season AND prev.round_id < rec.round_id)
                                  )
                                ORDER BY COALESCE(pr.round_number_in_season, 2147483647) DESC, prev.round_id DESC
                                LIMIT 1
                            ),
                            dethroned = dethroned_flag,
                            updated_at = NOW()
                        WHERE round_id = rec.round_id;
                    END LOOP;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE round_summary rs
                SET
                    leader_before_miner_hotkey = (
                        SELECT rvm.miner_hotkey
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.leader_before_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    leader_before_github_url = (
                        SELECT rvm.github_url
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.leader_before_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    candidate_miner_hotkey = (
                        SELECT rvm.miner_hotkey
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.candidate_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    candidate_github_url = (
                        SELECT rvm.github_url
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.candidate_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    leader_after_miner_hotkey = (
                        SELECT rvm.miner_hotkey
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.leader_after_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    leader_after_github_url = (
                        SELECT rvm.github_url
                        FROM round_validator_miners rvm
                        WHERE rvm.round_id = rs.round_id
                          AND rvm.miner_uid = rs.leader_after_miner_uid
                        ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                    ),
                    avg_eval_cost = COALESCE(
                        rs.avg_eval_cost,
                        (
                            SELECT AVG(rvm.post_consensus_avg_eval_cost)
                            FROM round_validator_miners rvm
                            WHERE rvm.round_id = rs.round_id
                              AND rvm.post_consensus_avg_eval_cost IS NOT NULL
                        )
                    ),
                    required_reward_to_dethrone = COALESCE(
                        rs.required_reward_to_dethrone,
                        CASE
                            WHEN rs.leader_before_reward IS NOT NULL AND rs.required_improvement_pct IS NOT NULL
                            THEN rs.leader_before_reward * (1.0 + rs.required_improvement_pct)
                            ELSE NULL
                        END
                    )
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE seasons s
                SET
                    leader_miner_uid = latest.leader_after_miner_uid,
                    leader_reward = latest.leader_after_reward,
                    leader_github_url = latest.leader_after_github_url,
                    updated_at = NOW()
                FROM (
                    SELECT DISTINCT ON (r.season_id)
                        r.season_id,
                        rs.leader_after_miner_uid,
                        rs.leader_after_reward,
                        rs.leader_after_github_url
                    FROM round_summary rs
                    JOIN rounds r ON r.round_id = rs.round_id
                    ORDER BY r.season_id, COALESCE(r.round_number_in_season, 2147483647) DESC, rs.round_id DESC
                ) latest
                WHERE latest.season_id = s.season_id
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = 'round_outcomes'
                          AND c.relkind IN ('v', 'm')
                    ) THEN
                        EXECUTE 'DROP VIEW round_outcomes CASCADE';
                    ELSIF EXISTS (
                        SELECT 1
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = 'round_outcomes'
                          AND c.relkind IN ('r', 'p')
                    ) THEN
                        EXECUTE 'DROP TABLE round_outcomes CASCADE';
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE VIEW round_outcomes AS
                SELECT
                    round_summary_id AS round_outcome_id,
                    round_id,
                    leader_after_miner_uid AS winner_miner_uid,
                    leader_after_reward AS winner_score,
                    leader_before_miner_uid AS reigning_miner_uid_before_round,
                    leader_before_reward AS reigning_score_before_round,
                    candidate_miner_uid AS top_candidate_miner_uid,
                    candidate_reward AS top_candidate_score,
                    required_improvement_pct,
                    dethroned,
                    validators_count,
                    miners_evaluated,
                    tasks_evaluated,
                    tasks_success,
                    avg_reward,
                    avg_eval_score,
                    avg_eval_time,
                    NULL::TIMESTAMPTZ AS computed_at,
                    summary_json,
                    post_consensus_summary,
                    NULL::BIGINT AS source_round_validator_id,
                    created_at,
                    updated_at
                FROM round_summary
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_rank'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_rank'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_rank TO best_local_rank;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_reward'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_reward'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_reward TO best_local_reward;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_eval_score'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_eval_score'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_eval_score TO best_local_eval_score;
                    END IF;                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_eval_time'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_eval_time'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_eval_time TO best_local_eval_time;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_tasks_received'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_tasks_received'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_tasks_received TO best_local_tasks_received;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_tasks_success'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_tasks_success'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_tasks_success TO best_local_tasks_success;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'effective_eval_cost'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_validator_miners'
                          AND column_name = 'best_local_eval_cost'
                    ) THEN
                        ALTER TABLE round_validator_miners RENAME COLUMN effective_eval_cost TO best_local_eval_cost;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                WITH ranked_history AS (
                    SELECT
                        current_rvm.id AS target_id,
                        COALESCE(hist_rvm.local_rank, 9999) AS best_local_rank,
                        COALESCE(hist_rvm.local_avg_reward, 0) AS best_local_reward,
                        COALESCE(hist_rvm.local_avg_eval_score, 0) AS best_local_eval_score,
                        COALESCE(hist_rvm.local_avg_eval_time, 0) AS best_local_eval_time,
                        COALESCE(hist_rvm.local_tasks_received, 0) AS best_local_tasks_received,
                        COALESCE(hist_rvm.local_tasks_success, 0) AS best_local_tasks_success,
                        COALESCE(hist_rvm.local_avg_eval_cost, 0) AS best_local_eval_cost,
                        ROW_NUMBER() OVER (
                            PARTITION BY current_rvm.id
                            ORDER BY
                                COALESCE(hist_rvm.local_avg_reward, 0) DESC,
                                COALESCE(hist_rvm.local_rank, 9999) ASC,
                                rv_hist.round_number_in_season ASC,
                                hist_rvm.id ASC
                        ) AS rn
                    FROM round_validator_miners current_rvm
                    JOIN round_validators rv_current
                      ON rv_current.round_validator_id = current_rvm.round_validator_id
                    JOIN round_validator_miners hist_rvm
                      ON hist_rvm.miner_uid = current_rvm.miner_uid
                    JOIN round_validators rv_hist
                      ON rv_hist.round_validator_id = hist_rvm.round_validator_id
                    WHERE rv_hist.validator_uid = rv_current.validator_uid
                      AND rv_hist.season_number = rv_current.season_number
                      AND rv_hist.round_number_in_season <= rv_current.round_number_in_season
                )
                UPDATE round_validator_miners rvm
                SET
                    best_local_rank = rh.best_local_rank,
                    best_local_reward = rh.best_local_reward,
                    best_local_eval_score = rh.best_local_eval_score,
                    best_local_eval_time = rh.best_local_eval_time,
                    best_local_tasks_received = rh.best_local_tasks_received,
                    best_local_tasks_success = rh.best_local_tasks_success,
                    best_local_eval_cost = rh.best_local_eval_cost,
                    updated_at = NOW()
                FROM ranked_history rh
                WHERE rvm.id = rh.target_id
                  AND rh.rn = 1
                """
            )
        )
        await conn.execute(text("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS planned_start_block BIGINT NULL"))
        await conn.execute(text("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS planned_end_block BIGINT NULL"))
        await conn.execute(text("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS opened_by_validator_uid INTEGER NULL"))
        await conn.execute(text("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS closed_by_validator_uid INTEGER NULL"))
        await conn.execute(text("ALTER TABLE rounds ADD COLUMN IF NOT EXISTS authority_mode VARCHAR(16) NULL"))
        await conn.execute(text("ALTER TABLE tasks DROP COLUMN IF EXISTS is_web_real"))
        await conn.execute(text("UPDATE rounds SET planned_start_block = COALESCE(planned_start_block, start_block) WHERE planned_start_block IS NULL"))
        await conn.execute(text("UPDATE rounds SET planned_end_block = COALESCE(planned_end_block, end_block) WHERE planned_end_block IS NULL"))
        await conn.execute(
            text(
                """
                WITH active_seasons AS (
                    SELECT season_id,
                           ROW_NUMBER() OVER (ORDER BY season_number DESC, season_id DESC) AS rn
                    FROM seasons
                    WHERE LOWER(COALESCE(status, '')) = 'active'
                )
                UPDATE seasons s
                SET status = 'finished', updated_at = NOW(), end_at = COALESCE(end_at, NOW())
                FROM active_seasons a
                WHERE s.season_id = a.season_id
                  AND a.rn > 1
                """
            )
        )
        await conn.execute(
            text(
                """
                WITH active_rounds AS (
                    SELECT r.round_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY r.season_id
                               ORDER BY r.round_number_in_season DESC, r.round_id DESC
                           ) AS rn
                    FROM rounds r
                    WHERE LOWER(COALESCE(r.status, '')) = 'active'
                )
                UPDATE rounds r
                SET status = 'finished',
                    consensus_status = CASE
                        WHEN LOWER(COALESCE(consensus_status, '')) = 'pending' THEN 'failed'
                        ELSE consensus_status
                    END,
                    ended_at = COALESCE(ended_at, NOW()),
                    updated_at = NOW()
                FROM active_rounds a
                WHERE r.round_id = a.round_id
                  AND a.rn > 1
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds_one_active_per_season
                ON rounds(season_id)
                WHERE LOWER(COALESCE(status, '')) = 'active'
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_seasons_one_active_global
                ON seasons((1))
                WHERE LOWER(COALESCE(status, '')) = 'active'
                """
            )
        )
        # Keep equivalent JSON fields aligned (legacy/new readers use different keys).
        await conn.execute(
            text(
                """
                UPDATE round_validators
                SET
                    post_consensus_json = COALESCE(post_consensus_json, post_consensus_summary),
                    post_consensus_summary = COALESCE(post_consensus_summary, post_consensus_json)
                WHERE post_consensus_json IS NULL OR post_consensus_summary IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE round_outcomes
                SET
                    post_consensus_summary = COALESCE(post_consensus_summary, summary_json),
                    summary_json = COALESCE(summary_json, post_consensus_summary)
                WHERE post_consensus_summary IS NULL OR summary_json IS NULL
                """
            )
        )

        validator_rounds_relkind = await conn.scalar(
            text(
                """
                SELECT c.relkind
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'validator_rounds'
                """
            )
        )
        validator_rounds_is_table = validator_rounds_relkind == "r"
        if validator_rounds_is_table:
            await conn.execute(text("ALTER TABLE validator_rounds DROP COLUMN IF EXISTS s3_logs"))
            await conn.execute(text("ALTER TABLE validator_rounds ADD COLUMN IF NOT EXISTS s3_logs_url TEXT"))
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
            await conn.execute(text("COMMENT ON COLUMN validator_rounds.s3_logs_url IS 'Public URL to validator round logs stored in S3.'"))
            await conn.execute(
                text(
                    """
                    UPDATE validator_rounds
                    SET
                      s3_logs_url = COALESCE(
                        s3_logs_url,
                        NULLIF(validator_summary->>'s3_logs_url', ''),
                        NULLIF(validator_summary->'s3_logs'->'round_log'->>'url', '')
                      ),
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
                          WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'reward') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                          THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'winner'->>'reward')::DOUBLE PRECISION
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
                          WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_reward_before_round') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                          THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'reigning_reward_before_round')::DOUBLE PRECISION
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
                          WHEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_reward') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                          THEN (validator_summary->'evaluation_post_consensus'->'round_summary'->'decision'->>'top_candidate_reward')::DOUBLE PRECISION
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
        # Runs should represent actual fresh evaluations only.
        # Clean up historical synthetic reused rows when they carry no artefacts of their own.
        await conn.execute(
            text(
                """
                DELETE FROM miner_evaluation_runs mer
                WHERE COALESCE(mer.is_reused, FALSE) = TRUE
                  AND NOT EXISTS (
                    SELECT 1 FROM task_solutions ts WHERE ts.agent_run_id = mer.agent_run_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM evaluations e WHERE e.agent_run_id = mer.agent_run_id
                  )
                """
            )
        )
        await conn.execute(text("ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS zero_reason VARCHAR(128) NULL"))
        await conn.execute(text("ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS zero_reason VARCHAR(128) NULL"))
        # Keep compatibility with ORM expecting evaluation_llm_usage.tokens/cost.
        await conn.execute(text("ALTER TABLE evaluation_llm_usage ADD COLUMN IF NOT EXISTS tokens INTEGER NULL"))
        await conn.execute(text("ALTER TABLE evaluation_llm_usage ADD COLUMN IF NOT EXISTS cost DOUBLE PRECISION NULL"))
        await conn.execute(
            text(
                """
                DO $$
                DECLARE
                    has_total BOOLEAN;
                    has_input BOOLEAN;
                    has_output BOOLEAN;
                BEGIN
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'evaluation_llm_usage'
                          AND column_name = 'total_tokens'
                    ) INTO has_total;
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'evaluation_llm_usage'
                          AND column_name = 'input_tokens'
                    ) INTO has_input;
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'evaluation_llm_usage'
                          AND column_name = 'output_tokens'
                    ) INTO has_output;

                    IF has_total THEN
                        UPDATE evaluation_llm_usage
                        SET tokens = COALESCE(tokens, total_tokens)
                        WHERE tokens IS NULL;
                    END IF;

                    IF has_input AND has_output THEN
                        UPDATE evaluation_llm_usage
                        SET tokens = COALESCE(tokens, COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0))
                        WHERE tokens IS NULL;
                    END IF;
                END $$;
                """
            )
        )
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
        if validator_rounds_is_table:
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
        if validator_rounds_is_table:
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

        # ------------------------------------------------------------------
        # New-DB compatibility layer for legacy validator-round endpoints
        # ------------------------------------------------------------------
        main_uid = settings.MAIN_VALIDATOR_UID
        main_hotkey = (settings.MAIN_VALIDATOR_HOTKEY or "").strip() or None
        main_hotkey_sql = main_hotkey.replace("'", "''") if main_hotkey else ""

        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'app_runtime_config'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'config_app_runtime'
                    ) THEN
                        ALTER TABLE app_runtime_config RENAME TO config_app_runtime;
                    END IF;

                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_config'
                    ) AND NOT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'config_season_round'
                    ) THEN
                        ALTER TABLE round_config RENAME TO config_season_round;
                    END IF;
                END $$;
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS config_app_runtime (
                    id SMALLINT PRIMARY KEY DEFAULT 1,
                    main_validator_uid INTEGER NULL,
                    main_validator_hotkey VARCHAR(128) NULL,
                    minimum_validator_version VARCHAR(32) NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT app_runtime_config_singleton CHECK (id = 1)
                )
                """
            )
        )
        await conn.execute(text("ALTER TABLE config_app_runtime ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
        await conn.execute(text("ALTER TABLE config_app_runtime ADD COLUMN IF NOT EXISTS minimum_validator_version VARCHAR(32)"))
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'app_runtime_config'
                    ) THEN
                        INSERT INTO config_app_runtime (
                            id,
                            main_validator_uid,
                            main_validator_hotkey,
                            minimum_validator_version,
                            created_at,
                            updated_at
                        )
                        SELECT
                            id,
                            main_validator_uid,
                            main_validator_hotkey,
                            minimum_validator_version,
                            COALESCE(created_at, NOW()),
                            COALESCE(updated_at, NOW())
                        FROM app_runtime_config
                        WHERE NOT EXISTS (
                            SELECT 1 FROM config_app_runtime car WHERE car.id = app_runtime_config.id
                        );
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                f"""
                INSERT INTO config_app_runtime (id, main_validator_uid, main_validator_hotkey, minimum_validator_version, created_at, updated_at)
                VALUES (1, {str(int(main_uid)) if main_uid is not None else "NULL"}, {("'" + main_hotkey_sql + "'") if main_hotkey else "NULL"}, NULL, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    main_validator_uid = COALESCE(EXCLUDED.main_validator_uid, config_app_runtime.main_validator_uid),
                    main_validator_hotkey = COALESCE(EXCLUDED.main_validator_hotkey, config_app_runtime.main_validator_hotkey),
                    updated_at = NOW()
                """
            )
        )

        # Season/round config: single row, only main validator can write.
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS config_season_round (
                    id SMALLINT PRIMARY KEY DEFAULT 1,
                    round_size_epochs DOUBLE PRECISION NOT NULL,
                    season_size_epochs DOUBLE PRECISION NOT NULL,
                    minimum_start_block BIGINT NOT NULL,
                    blocks_per_epoch INTEGER NOT NULL DEFAULT 360,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by_validator_uid INTEGER NULL,
                    CONSTRAINT round_config_singleton CHECK (id = 1)
                )
                """
            )
        )
        await conn.execute(text("ALTER TABLE config_season_round ADD COLUMN IF NOT EXISTS updated_by_validator_uid INTEGER"))
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'round_config'
                    ) THEN
                        INSERT INTO config_season_round (
                            id,
                            round_size_epochs,
                            season_size_epochs,
                            minimum_start_block,
                            blocks_per_epoch,
                            updated_at,
                            updated_by_validator_uid
                        )
                        SELECT
                            id,
                            round_size_epochs,
                            season_size_epochs,
                            minimum_start_block,
                            blocks_per_epoch,
                            COALESCE(updated_at, NOW()),
                            updated_by_validator_uid
                        FROM round_config
                        WHERE NOT EXISTS (
                            SELECT 1 FROM config_season_round csr WHERE csr.id = round_config.id
                        );
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION enforce_config_season_round_main_validator()
                RETURNS trigger AS $$
                DECLARE
                    main_uid INTEGER;
                BEGIN
                    SELECT main_validator_uid
                    INTO main_uid
                    FROM config_app_runtime
                    WHERE id = 1;

                    IF main_uid IS NULL THEN
                        RAISE EXCEPTION 'config_season_round write blocked: config_app_runtime.main_validator_uid is NULL';
                    END IF;

                    IF NEW.updated_by_validator_uid IS NULL THEN
                        RAISE EXCEPTION 'config_season_round write blocked: updated_by_validator_uid is required';
                    END IF;

                    IF NEW.updated_by_validator_uid <> main_uid THEN
                        RAISE EXCEPTION
                            'config_season_round write blocked: uid % is not main validator uid %',
                            NEW.updated_by_validator_uid,
                            main_uid;
                    END IF;

                    NEW.id := 1;
                    NEW.updated_at := NOW();
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_round_config_enforce_main_validator ON config_season_round"))
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_config_season_round_enforce_main_validator ON config_season_round"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_config_season_round_enforce_main_validator
                BEFORE INSERT OR UPDATE ON config_season_round
                FOR EACH ROW
                EXECUTE FUNCTION enforce_config_season_round_main_validator()
                """
            )
        )
        await conn.execute(text("DROP TABLE IF EXISTS app_runtime_config CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS round_config CASCADE"))

        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_execution_logs_pending (
                    id BIGSERIAL PRIMARY KEY,
                    task_id VARCHAR(128) NOT NULL,
                    agent_run_id VARCHAR(128) NOT NULL,
                    validator_round_id VARCHAR(128) NOT NULL,
                    validator_uid INTEGER NULL,
                    miner_uid INTEGER NULL,
                    season INTEGER NULL,
                    round_in_season INTEGER NULL,
                    payload_ref TEXT NOT NULL,
                    payload_size BIGINT NOT NULL DEFAULT 0,
                    last_error TEXT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_task_execution_logs_pending UNIQUE (task_id, agent_run_id)
                )
                """
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_execution_logs_pending_round ON task_execution_logs_pending(validator_round_id)"))

        # Bridge legacy tables with canonical round_validators table.
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS round_validator_id BIGINT"))
        await conn.execute(text("ALTER TABLE miner_evaluation_runs ADD COLUMN IF NOT EXISTS round_validator_id BIGINT"))
        await conn.execute(
            text(
                """
                UPDATE tasks t
                SET round_validator_id = rv.round_validator_id
                FROM round_validators rv
                WHERE t.round_validator_id IS NULL
                  AND t.validator_round_id IS NOT NULL
                  AND rv.validator_round_id = t.validator_round_id
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE miner_evaluation_runs mer
                SET round_validator_id = rv.round_validator_id
                FROM round_validators rv
                WHERE mer.round_validator_id IS NULL
                  AND mer.validator_round_id IS NOT NULL
                  AND rv.validator_round_id = mer.validator_round_id
                """
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_round_validator_id ON tasks(round_validator_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_miner_eval_runs_round_validator_id ON miner_evaluation_runs(round_validator_id)"))

        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS validator_round_id VARCHAR(128)"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS validator_state JSONB"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS validator_iwap_prev_round_json JSONB"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS s3_logs_url TEXT"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS season_number INTEGER"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS round_number_in_season INTEGER"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS start_block BIGINT"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS end_block BIGINT"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS start_epoch INTEGER"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS end_epoch INTEGER"))
        await conn.execute(text("ALTER TABLE round_validators ADD COLUMN IF NOT EXISTS pending_round_link BOOLEAN NOT NULL DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE round_validators ALTER COLUMN round_id DROP NOT NULL"))
        await conn.execute(text("ALTER TABLE round_validator_miners ALTER COLUMN round_id DROP NOT NULL"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_round_validators_season_round ON round_validators(season_number, round_number_in_season)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_round_validators_pending_link ON round_validators(pending_round_link)"))
        await conn.execute(
            text(
                """
                UPDATE round_validators rv
                SET
                    season_number = COALESCE(rv.season_number, s.season_number),
                    round_number_in_season = COALESCE(rv.round_number_in_season, r.round_number_in_season),
                    start_block = COALESCE(rv.start_block, r.start_block),
                    end_block = COALESCE(rv.end_block, r.end_block),
                    start_epoch = COALESCE(rv.start_epoch, r.start_epoch),
                    end_epoch = COALESCE(rv.end_epoch, r.end_epoch)
                FROM rounds r
                JOIN seasons s ON s.season_id = r.season_id
                WHERE rv.round_id = r.round_id
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE round_validators
                SET validator_round_id = CONCAT('validator_round_', round_id, '_', validator_uid)
                WHERE validator_round_id IS NULL OR validator_round_id = ''
                """
            )
        )
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_round_validators_validator_round_id ON round_validators(validator_round_id)"))
        # Replace legacy physical tables with compatibility views.
        await conn.execute(
            text(
                """
                DO $$
                DECLARE
                    obj TEXT;
                    rk CHAR;
                BEGIN
                    FOREACH obj IN ARRAY ARRAY[
                        'validator_round_summary_miners',
                        'validator_round_miners',
                        'validator_round_validators',
                        'validator_rounds'
                    ]
                    LOOP
                        SELECT c.relkind
                        INTO rk
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = current_schema()
                          AND c.relname = obj
                        LIMIT 1;

                        IF rk = 'r' THEN
                            EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', obj);
                        ELSIF rk = 'v' THEN
                            EXECUTE format('DROP VIEW IF EXISTS %I CASCADE', obj);
                        END IF;
                    END LOOP;
                END $$;
                """
            )
        )

        await conn.execute(
            text(
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
                    rv.post_consensus_summary AS validator_summary,
                    rv.s3_logs_url AS s3_logs_url,
                    ro.winner_miner_uid AS winner_uid,
                    ro.winner_score,
                    ro.reigning_miner_uid_before_round AS reigning_uid_before_round,
                    ro.reigning_score_before_round,
                    ro.top_candidate_miner_uid AS top_candidate_uid,
                    ro.top_candidate_score,
                    ro.required_improvement_pct,
                    ro.dethroned,
                    rv.created_at,
                    rv.updated_at
                FROM round_validators rv
                LEFT JOIN rounds r ON r.round_id = rv.round_id
                LEFT JOIN seasons s ON s.season_id = r.season_id
                LEFT JOIN round_outcomes ro ON ro.round_id = r.round_id
                LEFT JOIN (
                    SELECT tasks.round_validator_id, COUNT(*)::INTEGER AS tasks_count
                    FROM tasks
                    GROUP BY tasks.round_validator_id
                ) t ON t.round_validator_id = rv.round_validator_id
                """
            )
        )

        await conn.execute(
            text(
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
                """
            )
        )

        await conn.execute(
            text(
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
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE OR REPLACE VIEW validator_round_summary_miners AS
                SELECT
                    rvm.id,
                    rv.validator_round_id::TEXT AS validator_round_id,
                    rvm.miner_uid,
                    rvm.miner_hotkey,
                    rvm.local_rank,
                    rvm.local_avg_reward,
                    rvm.local_avg_eval_score,
                    rvm.local_avg_eval_time,
                    rvm.local_tasks_received,
                    rvm.local_tasks_success,
                    rvm.post_consensus_rank,
                    rvm.post_consensus_avg_reward,
                    rvm.post_consensus_avg_eval_score,
                    rvm.post_consensus_avg_eval_time,
                    rvm.post_consensus_tasks_received,
                    rvm.post_consensus_tasks_success,
                    rvm.weight,
                    rvm.subnet_price,
                    rvm.created_at,
                    rvm.updated_at
                FROM round_validator_miners rvm
                JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_fill_round_validator_id_tasks()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.round_validator_id IS NULL AND NEW.validator_round_id IS NOT NULL THEN
                        SELECT rv.round_validator_id
                        INTO NEW.round_validator_id
                        FROM round_validators rv
                        WHERE rv.validator_round_id = NEW.validator_round_id
                        LIMIT 1;
                    END IF;
                    IF NEW.round_validator_id IS NULL THEN
                        RAISE EXCEPTION 'tasks.round_validator_id is required (validator_round_id=%)', NEW.validator_round_id;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_fill_round_validator_id_tasks ON tasks"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_fill_round_validator_id_tasks
                BEFORE INSERT OR UPDATE OF round_validator_id, validator_round_id
                ON tasks
                FOR EACH ROW
                EXECUTE FUNCTION compat_fill_round_validator_id_tasks()
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_fill_round_validator_id_runs()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.round_validator_id IS NULL AND NEW.validator_round_id IS NOT NULL THEN
                        SELECT rv.round_validator_id
                        INTO NEW.round_validator_id
                        FROM round_validators rv
                        WHERE rv.validator_round_id = NEW.validator_round_id
                        LIMIT 1;
                    END IF;
                    IF NEW.round_validator_id IS NULL THEN
                        RAISE EXCEPTION 'miner_evaluation_runs.round_validator_id is required (validator_round_id=%)', NEW.validator_round_id;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_fill_round_validator_id_runs ON miner_evaluation_runs"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_fill_round_validator_id_runs
                BEFORE INSERT OR UPDATE OF round_validator_id, validator_round_id
                ON miner_evaluation_runs
                FOR EACH ROW
                EXECUTE FUNCTION compat_fill_round_validator_id_runs()
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_validator_rounds_iou()
                RETURNS TRIGGER AS $$
                DECLARE
                    sid BIGINT;
                    rid BIGINT;
                    rvid BIGINT;
                    ts TIMESTAMPTZ;
                    te TIMESTAMPTZ;
                    cfg_uid INTEGER;
                    cfg_hotkey VARCHAR(128);
                    cur_uid INTEGER;
                    cur_hotkey VARCHAR(128);
                    is_main BOOLEAN;
                BEGIN
                    SELECT main_validator_uid, main_validator_hotkey
                    INTO cfg_uid, cfg_hotkey
                    FROM config_app_runtime
                    WHERE id = 1;
                    -- INSERT on compatibility view validator_rounds does not expose validator_uid/hotkey.
                    -- Authority is finalized later when validator_round_validators upsert arrives.
                    is_main := FALSE;

                    IF TG_OP = 'INSERT' THEN
                        IF NEW.season_number IS NULL OR NEW.round_number_in_season IS NULL THEN
                            RAISE EXCEPTION 'season_number and round_number_in_season are required';
                        END IF;

                        ts := CASE WHEN NEW.started_at IS NULL OR NEW.started_at <= 0 THEN NULL ELSE to_timestamp(NEW.started_at) END;
                        te := CASE WHEN NEW.ended_at IS NULL OR NEW.ended_at <= 0 THEN NULL ELSE to_timestamp(NEW.ended_at) END;

                        SELECT season_id INTO sid
                        FROM seasons
                        WHERE season_number = NEW.season_number
                        LIMIT 1;
                        IF sid IS NULL THEN
                            INSERT INTO seasons (season_number, status, start_block, end_block, start_at, end_at, required_improvement_pct, created_at, updated_at)
                            VALUES (NEW.season_number, 'active', NEW.start_block, NEW.end_block, ts, te, COALESCE(NEW.required_improvement_pct, 0.05), NOW(), NOW())
                            RETURNING season_id INTO sid;
                        END IF;

                        SELECT round_id INTO rid
                        FROM rounds
                        WHERE season_id = sid AND round_number_in_season = NEW.round_number_in_season
                        LIMIT 1;
                        IF rid IS NULL THEN
                            IF EXISTS (
                                SELECT 1
                                FROM rounds rchk
                                WHERE rchk.season_id = sid
                                  AND LOWER(COALESCE(rchk.status, '')) = 'active'
                            ) THEN
                                RAISE EXCEPTION 'cannot create round %/% while another round in same season is active',
                                    NEW.season_number, NEW.round_number_in_season;
                            END IF;
                            INSERT INTO rounds (
                                season_id, round_number_in_season,
                                start_block, end_block, planned_start_block, planned_end_block, start_epoch, end_epoch,
                                opened_by_validator_uid, authority_mode,
                                started_at, ended_at, status, consensus_status,
                                created_at, updated_at
                            )
                            VALUES (
                                sid,
                                NEW.round_number_in_season,
                                NEW.start_block,
                                NEW.end_block,
                                NEW.start_block,
                                NEW.end_block,
                                NEW.start_epoch,
                                NEW.end_epoch,
                                NULL,
                                NULL,
                                ts,
                                te,
                                COALESCE(NEW.status, 'active'),
                                CASE WHEN LOWER(COALESCE(NEW.status, '')) IN ('finished', 'evaluating_finished') THEN 'finalized' ELSE 'pending' END,
                                NOW(),
                                NOW()
                            )
                            RETURNING round_id INTO rid;
                        ELSE
                            IF COALESCE(is_main, FALSE) AND LOWER(COALESCE(NEW.status, '')) = 'active' AND EXISTS (
                                SELECT 1
                                FROM rounds rchk
                                WHERE rchk.season_id = sid
                                  AND rchk.round_id <> rid
                                  AND LOWER(COALESCE(rchk.status, '')) = 'active'
                            ) THEN
                                RAISE EXCEPTION 'cannot activate round %/% while another round in same season is active',
                                    NEW.season_number, NEW.round_number_in_season;
                            END IF;
                            UPDATE rounds
                            SET
                                start_block = CASE WHEN is_main THEN COALESCE(NEW.start_block, start_block) ELSE COALESCE(start_block, NEW.start_block) END,
                                end_block = CASE WHEN is_main THEN COALESCE(NEW.end_block, end_block) ELSE COALESCE(end_block, NEW.end_block) END,
                                planned_start_block = COALESCE(planned_start_block, NEW.start_block),
                                planned_end_block = COALESCE(planned_end_block, NEW.end_block),
                                start_epoch = CASE WHEN is_main THEN COALESCE(NEW.start_epoch, start_epoch) ELSE COALESCE(start_epoch, NEW.start_epoch) END,
                                end_epoch = CASE WHEN is_main THEN COALESCE(NEW.end_epoch, end_epoch) ELSE COALESCE(end_epoch, NEW.end_epoch) END,
                                started_at = CASE WHEN is_main THEN COALESCE(ts, started_at) ELSE COALESCE(started_at, ts) END,
                                ended_at = CASE WHEN is_main THEN COALESCE(te, ended_at) ELSE COALESCE(ended_at, te) END,
                                status = CASE WHEN is_main THEN COALESCE(NEW.status, status) ELSE COALESCE(status, NEW.status) END,
                                closed_by_validator_uid = CASE
                                    WHEN LOWER(COALESCE(NEW.status, '')) IN ('finished', 'evaluating_finished')
                                    THEN COALESCE(
                                        (SELECT rvv.validator_uid FROM round_validators rvv WHERE rvv.round_validator_id = rvid LIMIT 1),
                                        closed_by_validator_uid
                                    )
                                    ELSE closed_by_validator_uid
                                END,
                                authority_mode = CASE
                                    WHEN authority_mode IS NULL THEN CASE WHEN COALESCE(is_main, FALSE) THEN 'main' ELSE 'fallback' END
                                    ELSE authority_mode
                                END,
                                consensus_status = CASE
                                    WHEN is_main AND LOWER(COALESCE(NEW.status, status)) IN ('finished', 'evaluating_finished') THEN 'finalized'
                                    ELSE consensus_status
                                END,
                                updated_at = NOW()
                            WHERE round_id = rid;
                        END IF;

                        SELECT round_validator_id INTO rvid
                        FROM round_validators
                        WHERE validator_round_id = NEW.validator_round_id
                        LIMIT 1;
                        IF rvid IS NULL THEN
                            INSERT INTO round_validators (
                                round_id, season_number, round_number_in_season,
                                start_block, end_block, start_epoch, end_epoch,
                                validator_uid, validator_hotkey, validator_round_id,
                                started_at, finished_at, post_consensus_summary, post_consensus_json, s3_logs_url, is_main_validator, created_at, updated_at
                            )
                            VALUES (
                                rid, NEW.season_number, NEW.round_number_in_season,
                                NEW.start_block, NEW.end_block, NEW.start_epoch, NEW.end_epoch,
                                0, NULL, NEW.validator_round_id,
                                ts, te, NEW.validator_summary, NEW.validator_summary, NEW.s3_logs_url, FALSE, NOW(), NOW()
                            )
                            RETURNING round_validator_id INTO rvid;
                        ELSE
                            UPDATE round_validators
                            SET
                                round_id = rid,
                                season_number = COALESCE(NEW.season_number, season_number),
                                round_number_in_season = COALESCE(NEW.round_number_in_season, round_number_in_season),
                                start_block = COALESCE(NEW.start_block, start_block),
                                end_block = COALESCE(NEW.end_block, end_block),
                                start_epoch = COALESCE(NEW.start_epoch, start_epoch),
                                end_epoch = COALESCE(NEW.end_epoch, end_epoch),
                                pending_round_link = CASE WHEN rid IS NULL THEN TRUE ELSE FALSE END,
                                started_at = COALESCE(ts, started_at),
                                finished_at = COALESCE(te, finished_at),
                                post_consensus_summary = COALESCE(NEW.validator_summary, post_consensus_summary),
                                post_consensus_json = COALESCE(NEW.validator_summary, post_consensus_json),
                                s3_logs_url = COALESCE(NEW.s3_logs_url, s3_logs_url),
                                updated_at = NOW()
                            WHERE round_validator_id = rvid;
                        END IF;                        NEW.id := rvid;
                        RETURN NEW;
                    ELSIF TG_OP = 'UPDATE' THEN
                        ts := CASE WHEN NEW.started_at IS NULL OR NEW.started_at <= 0 THEN NULL ELSE to_timestamp(NEW.started_at) END;
                        te := CASE WHEN NEW.ended_at IS NULL OR NEW.ended_at <= 0 THEN NULL ELSE to_timestamp(NEW.ended_at) END;

                        SELECT rv.round_validator_id, rv.round_id
                        INTO rvid, rid
                        FROM round_validators rv
                        WHERE rv.validator_round_id = COALESCE(NEW.validator_round_id, OLD.validator_round_id)
                        LIMIT 1;

                        IF rvid IS NULL THEN
                            RAISE EXCEPTION 'validator_round_id not found: %', COALESCE(NEW.validator_round_id, OLD.validator_round_id);
                        END IF;

                        SELECT validator_uid, validator_hotkey
                        INTO cur_uid, cur_hotkey
                        FROM round_validators
                        WHERE round_validator_id = rvid
                        LIMIT 1;
                        is_main := (
                            (cfg_uid IS NULL AND (cfg_hotkey IS NULL OR cfg_hotkey = ''))
                            OR
                            (cfg_uid IS NOT NULL AND cur_uid = cfg_uid)
                            OR
                            (cfg_hotkey IS NOT NULL AND cfg_hotkey <> '' AND cur_hotkey = cfg_hotkey)
                        );

                        UPDATE rounds
                        SET
                            start_block = CASE WHEN is_main THEN COALESCE(NEW.start_block, start_block) ELSE COALESCE(start_block, NEW.start_block) END,
                            end_block = CASE WHEN is_main THEN COALESCE(NEW.end_block, end_block) ELSE COALESCE(end_block, NEW.end_block) END,
                            start_epoch = CASE WHEN is_main THEN COALESCE(NEW.start_epoch, start_epoch) ELSE COALESCE(start_epoch, NEW.start_epoch) END,
                            end_epoch = CASE WHEN is_main THEN COALESCE(NEW.end_epoch, end_epoch) ELSE COALESCE(end_epoch, NEW.end_epoch) END,
                            started_at = CASE WHEN is_main THEN COALESCE(ts, started_at) ELSE COALESCE(started_at, ts) END,
                            ended_at = CASE WHEN is_main THEN COALESCE(te, ended_at) ELSE COALESCE(ended_at, te) END,
                            status = CASE WHEN is_main THEN COALESCE(NEW.status, status) ELSE status END,
                            consensus_status = CASE
                                WHEN is_main AND LOWER(COALESCE(NEW.status, status)) IN ('finished', 'evaluating_finished') THEN 'finalized'
                                ELSE consensus_status
                            END,
                            updated_at = NOW()
                        WHERE round_id = rid;

                        UPDATE round_validators
                        SET
                            round_id = COALESCE(rid, round_id),
                            season_number = COALESCE(NEW.season_number, season_number),
                            round_number_in_season = COALESCE(NEW.round_number_in_season, round_number_in_season),
                            start_block = COALESCE(NEW.start_block, start_block),
                            end_block = COALESCE(NEW.end_block, end_block),
                            start_epoch = COALESCE(NEW.start_epoch, start_epoch),
                            end_epoch = COALESCE(NEW.end_epoch, end_epoch),
                            pending_round_link = CASE WHEN rid IS NULL THEN TRUE ELSE FALSE END,
                            finished_at = COALESCE(te, finished_at),
                            post_consensus_summary = COALESCE(NEW.validator_summary, post_consensus_summary),
                            post_consensus_json = COALESCE(NEW.validator_summary, post_consensus_json),
                            s3_logs_url = COALESCE(NEW.s3_logs_url, s3_logs_url),
                            is_main_validator = COALESCE(is_main, is_main_validator),
                            updated_at = NOW()
                        WHERE round_validator_id = rvid;

                        IF COALESCE(is_main, FALSE) THEN
                            UPDATE round_validators
                            SET is_main_validator = FALSE, updated_at = NOW()
                            WHERE round_id = rid AND round_validator_id <> rvid AND is_main_validator = TRUE;

                            INSERT INTO round_summary (
                                round_id,
                                leader_before_miner_uid,
                                leader_before_reward,
                                candidate_miner_uid,
                                candidate_reward,
                                leader_after_miner_uid,
                                leader_after_reward,
                                required_improvement_pct,
                                required_reward_to_dethrone,
                                dethroned,
                                summary_json,
                                post_consensus_summary,
                                created_at,
                                updated_at
                            )
                            VALUES (
                                rid,
                                NEW.reigning_uid_before_round,
                                NEW.reigning_score_before_round,
                                NEW.top_candidate_uid,
                                NEW.top_candidate_score,
                                NEW.winner_uid,
                                NEW.winner_score,
                                COALESCE(NEW.required_improvement_pct, 0.05),
                                CASE
                                    WHEN NEW.reigning_score_before_round IS NOT NULL
                                    THEN NEW.reigning_score_before_round * (1.0 + COALESCE(NEW.required_improvement_pct, 0.05))
                                    ELSE NULL
                                END,
                                NEW.dethroned,
                                NEW.validator_summary,
                                NEW.validator_summary,
                                NOW(),
                                NOW()
                            )
                            ON CONFLICT (round_id) DO UPDATE SET
                                leader_before_miner_uid = EXCLUDED.leader_before_miner_uid,
                                leader_before_reward = EXCLUDED.leader_before_reward,
                                candidate_miner_uid = EXCLUDED.candidate_miner_uid,
                                candidate_reward = EXCLUDED.candidate_reward,
                                leader_after_miner_uid = EXCLUDED.leader_after_miner_uid,
                                leader_after_reward = EXCLUDED.leader_after_reward,
                                required_improvement_pct = EXCLUDED.required_improvement_pct,
                                required_reward_to_dethrone = EXCLUDED.required_reward_to_dethrone,
                                dethroned = EXCLUDED.dethroned,
                                summary_json = COALESCE(EXCLUDED.summary_json, round_summary.summary_json),
                                post_consensus_summary = COALESCE(EXCLUDED.post_consensus_summary, round_summary.post_consensus_summary),
                                updated_at = NOW();
                        END IF;

                        NEW.id := rvid;
                        RETURN NEW;
                    ELSIF TG_OP = 'DELETE' THEN
                        DELETE FROM round_validators
                        WHERE validator_round_id = OLD.validator_round_id;
                        RETURN OLD;
                    END IF;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_validator_rounds_iou ON validator_rounds"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_validator_rounds_iou
                INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_rounds
                FOR EACH ROW
                EXECUTE FUNCTION compat_validator_rounds_iou()
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_validator_round_validators_iou()
                RETURNS TRIGGER AS $$
                DECLARE
                    rvid BIGINT;
                    cfg_uid INTEGER;
                    cfg_hotkey VARCHAR(128);
                    is_main BOOLEAN;
                BEGIN
                    SELECT main_validator_uid, main_validator_hotkey
                    INTO cfg_uid, cfg_hotkey
                    FROM config_app_runtime
                    WHERE id = 1;
                    is_main := (
                        (cfg_uid IS NULL AND (cfg_hotkey IS NULL OR cfg_hotkey = ''))
                        OR
                        (cfg_uid IS NOT NULL AND NEW.validator_uid = cfg_uid)
                        OR
                        (cfg_hotkey IS NOT NULL AND cfg_hotkey <> '' AND NEW.validator_hotkey = cfg_hotkey)
                    );

                    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
                        SELECT round_validator_id INTO rvid
                        FROM round_validators
                        WHERE validator_round_id = NEW.validator_round_id
                        LIMIT 1;

                        IF rvid IS NULL THEN
                            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
                        END IF;

                        UPDATE round_validators
                        SET
                            validator_uid = NEW.validator_uid,
                            validator_hotkey = NEW.validator_hotkey,
                            validator_coldkey = NEW.validator_coldkey,
                            name = NEW.name,
                            stake = NEW.stake,
                            vtrust = NEW.vtrust,
                            image_url = NEW.image_url,
                            version = NEW.version,
                            config = NEW.config,
                            is_main_validator = COALESCE(is_main, is_main_validator),
                            updated_at = NOW()
                        WHERE round_validator_id = rvid;                        UPDATE rounds
                        SET
                            opened_by_validator_uid = COALESCE(opened_by_validator_uid, NEW.validator_uid),
                            authority_mode = COALESCE(
                                authority_mode,
                                CASE WHEN COALESCE(is_main, FALSE) THEN 'main' ELSE 'fallback' END
                            ),
                            updated_at = NOW()
                        WHERE round_id = (SELECT round_id FROM round_validators WHERE round_validator_id = rvid);

                        IF COALESCE(is_main, FALSE) THEN
                            UPDATE round_validators
                            SET is_main_validator = FALSE, updated_at = NOW()
                            WHERE round_id = (SELECT round_id FROM round_validators WHERE round_validator_id = rvid)
                              AND round_validator_id <> rvid
                              AND is_main_validator = TRUE;
                        END IF;

                        NEW.id := rvid;
                        RETURN NEW;
                    ELSIF TG_OP = 'DELETE' THEN
                        DELETE FROM round_validators WHERE validator_round_id = OLD.validator_round_id;
                        RETURN OLD;
                    END IF;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_validator_round_validators_iou ON validator_round_validators"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_validator_round_validators_iou
                INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_validators
                FOR EACH ROW
                EXECUTE FUNCTION compat_validator_round_validators_iou()
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_validator_round_miners_iou()
                RETURNS TRIGGER AS $$
                DECLARE
                    rvid BIGINT;
                    rid BIGINT;
                    miner_key INTEGER;
                    target_id BIGINT;
                BEGIN
                    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
                        IF NEW.miner_uid IS NULL THEN
                            RAISE EXCEPTION 'miner_uid is required for validator_round_miners compatibility write';
                        END IF;
                        miner_key := NEW.miner_uid;

                        SELECT round_validator_id, round_id INTO rvid, rid
                        FROM round_validators
                        WHERE validator_round_id = NEW.validator_round_id
                        LIMIT 1;
                        IF rvid IS NULL THEN
                            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
                        END IF;

                        INSERT INTO round_validator_miners (
                            round_validator_id, round_id, miner_uid, miner_hotkey, miner_coldkey,
                            name, image_url, github_url, is_sota, version, created_at, updated_at
                        )
                        VALUES (
                            rvid, rid, miner_key, NEW.miner_hotkey, NEW.miner_coldkey,
                            NEW.name, NEW.image_url, NEW.github_url, COALESCE(NEW.is_sota, FALSE), NEW.version, NOW(), NOW()
                        )
                        ON CONFLICT (round_validator_id, miner_uid) DO UPDATE SET
                            miner_hotkey = EXCLUDED.miner_hotkey,
                            miner_coldkey = EXCLUDED.miner_coldkey,
                            name = EXCLUDED.name,
                            image_url = EXCLUDED.image_url,
                            github_url = EXCLUDED.github_url,
                            is_sota = EXCLUDED.is_sota,
                            version = EXCLUDED.version,
                            updated_at = NOW();

                        SELECT id INTO target_id
                        FROM round_validator_miners
                        WHERE round_validator_id = rvid AND miner_uid = miner_key
                        LIMIT 1;
                        NEW.id := target_id;
                        RETURN NEW;
                    ELSIF TG_OP = 'DELETE' THEN
                        DELETE FROM round_validator_miners rvm
                        USING round_validators rv
                        WHERE rv.round_validator_id = rvm.round_validator_id
                          AND rv.validator_round_id = OLD.validator_round_id
                          AND rvm.miner_uid = OLD.miner_uid;
                        RETURN OLD;
                    END IF;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_validator_round_miners_iou ON validator_round_miners"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_validator_round_miners_iou
                INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_miners
                FOR EACH ROW
                EXECUTE FUNCTION compat_validator_round_miners_iou()
                """
            )
        )

        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION compat_validator_round_summary_miners_iou()
                RETURNS TRIGGER AS $$
                DECLARE
                    rvid BIGINT;
                    rid BIGINT;
                    target_id BIGINT;
                BEGIN
                    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
                        SELECT round_validator_id, round_id INTO rvid, rid
                        FROM round_validators
                        WHERE validator_round_id = NEW.validator_round_id
                        LIMIT 1;
                        IF rvid IS NULL THEN
                            RAISE EXCEPTION 'validator_round_id not found: %', NEW.validator_round_id;
                        END IF;

                        INSERT INTO round_validator_miners (
                            round_validator_id, round_id, miner_uid, miner_hotkey,
                            local_rank, local_avg_reward, local_avg_eval_score, local_avg_eval_time, local_avg_eval_cost, local_tasks_received, local_tasks_success,
                            post_consensus_rank, post_consensus_avg_reward, post_consensus_avg_eval_score, post_consensus_avg_eval_time, post_consensus_avg_eval_cost,
                            post_consensus_tasks_received, post_consensus_tasks_success, weight, subnet_price, created_at, updated_at
                        )
                        VALUES (
                            rvid, rid, NEW.miner_uid, NEW.miner_hotkey,
                            NEW.local_rank, NEW.local_avg_reward, NEW.local_avg_eval_score, NEW.local_avg_eval_time, NEW.local_avg_eval_cost, NEW.local_tasks_received, NEW.local_tasks_success,
                            NEW.post_consensus_rank, NEW.post_consensus_avg_reward, NEW.post_consensus_avg_eval_score, NEW.post_consensus_avg_eval_time, NEW.post_consensus_avg_eval_cost,
                            NEW.post_consensus_tasks_received, NEW.post_consensus_tasks_success, NEW.weight, NEW.subnet_price, NOW(), NOW()
                        )
                        ON CONFLICT (round_validator_id, miner_uid) DO UPDATE SET
                            miner_hotkey = COALESCE(EXCLUDED.miner_hotkey, round_validator_miners.miner_hotkey),
                            local_rank = EXCLUDED.local_rank,
                            local_avg_reward = EXCLUDED.local_avg_reward,
                            local_avg_eval_score = EXCLUDED.local_avg_eval_score,
                            local_avg_eval_time = EXCLUDED.local_avg_eval_time,
                            local_avg_eval_cost = EXCLUDED.local_avg_eval_cost,
                            local_tasks_received = EXCLUDED.local_tasks_received,
                            local_tasks_success = EXCLUDED.local_tasks_success,
                            post_consensus_rank = EXCLUDED.post_consensus_rank,
                            post_consensus_avg_reward = EXCLUDED.post_consensus_avg_reward,
                            post_consensus_avg_eval_score = EXCLUDED.post_consensus_avg_eval_score,
                            post_consensus_avg_eval_time = EXCLUDED.post_consensus_avg_eval_time,
                            post_consensus_avg_eval_cost = EXCLUDED.post_consensus_avg_eval_cost,
                            post_consensus_tasks_received = EXCLUDED.post_consensus_tasks_received,
                            post_consensus_tasks_success = EXCLUDED.post_consensus_tasks_success,
                            weight = EXCLUDED.weight,
                            subnet_price = EXCLUDED.subnet_price,
                            updated_at = NOW();

                        SELECT id INTO target_id
                        FROM round_validator_miners
                        WHERE round_validator_id = rvid AND miner_uid = NEW.miner_uid
                        LIMIT 1;
                        NEW.id := target_id;
                        RETURN NEW;
                    ELSIF TG_OP = 'DELETE' THEN
                        DELETE FROM round_validator_miners rvm
                        USING round_validators rv
                        WHERE rv.round_validator_id = rvm.round_validator_id
                          AND rv.validator_round_id = OLD.validator_round_id
                          AND rvm.miner_uid = OLD.miner_uid;
                        RETURN OLD;
                    END IF;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_compat_validator_round_summary_miners_iou ON validator_round_summary_miners"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_compat_validator_round_summary_miners_iou
                INSTEAD OF INSERT OR UPDATE OR DELETE ON validator_round_summary_miners
                FOR EACH ROW
                EXECUTE FUNCTION compat_validator_round_summary_miners_iou()
                """
            )
        )

        # Enforce sane boundaries across canonical tables regardless of write path
        # (new API, compatibility views, or old validators).
        await conn.execute(
            text(
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
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_normalize_round_boundaries ON rounds"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_normalize_round_boundaries
                BEFORE INSERT OR UPDATE ON rounds
                FOR EACH ROW
                EXECUTE FUNCTION normalize_round_boundaries()
                """
            )
        )
        await conn.execute(
            text(
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
                """
            )
        )
        await conn.execute(text("DROP TRIGGER IF EXISTS trg_normalize_round_validator_boundaries ON round_validators"))
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_normalize_round_validator_boundaries
                BEFORE INSERT OR UPDATE ON round_validators
                FOR EACH ROW
                EXECUTE FUNCTION normalize_round_validator_boundaries()
                """
            )
        )

        # One-time self-healing for previously persisted malformed rows.
        await conn.execute(
            text(
                """
                UPDATE rounds
                SET
                    end_block = CASE
                        WHEN start_block IS NOT NULL AND end_block IS NOT NULL AND end_block < start_block THEN start_block
                        ELSE end_block
                    END,
                    ended_at = CASE
                        WHEN started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at < started_at THEN started_at
                        ELSE ended_at
                    END,
                    updated_at = NOW()
                WHERE
                    (start_block IS NOT NULL AND end_block IS NOT NULL AND end_block < start_block)
                    OR (started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at < started_at)
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE round_validators
                SET
                    end_block = CASE
                        WHEN start_block IS NOT NULL AND end_block IS NOT NULL AND end_block < start_block THEN start_block
                        ELSE end_block
                    END,
                    finished_at = CASE
                        WHEN finished_at IS NOT NULL AND finished_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN NULL
                        WHEN started_at IS NOT NULL AND finished_at IS NOT NULL AND finished_at < started_at THEN started_at
                        ELSE finished_at
                    END,
                    started_at = CASE
                        WHEN started_at IS NOT NULL AND started_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00' THEN NULL
                        ELSE started_at
                    END,
                    updated_at = NOW()
                WHERE
                    (start_block IS NOT NULL AND end_block IS NOT NULL AND end_block < start_block)
                    OR (started_at IS NOT NULL AND finished_at IS NOT NULL AND finished_at < started_at)
                    OR (started_at IS NOT NULL AND started_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00')
                    OR (finished_at IS NOT NULL AND finished_at < TIMESTAMP WITH TIME ZONE '2001-01-01 00:00:00+00')
                """
            )
        )

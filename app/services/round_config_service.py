"""
Round/season config: single source of truth from DB, written only by main validator.

The backend reads round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch
from the round_config table (one row). Only the main validator can persist this config
(via finish_round with round_metadata). No .env fallback is allowed for round timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# In-memory cache so sync code (round_calc, etc.) can read without session.
_round_config_cache: Optional["RoundConfig"] = None


@dataclass
class RoundConfig:
    round_size_epochs: float
    season_size_epochs: float
    minimum_start_block: int
    blocks_per_epoch: int

    def round_blocks(self) -> int:
        return int(self.round_size_epochs * self.blocks_per_epoch)

    def season_blocks(self) -> int:
        return int(self.season_size_epochs * self.blocks_per_epoch)


def set_round_config_cache(config: Optional[RoundConfig]) -> None:
    """Set the in-memory cache (called at startup from DB or when main validator upserts)."""
    global _round_config_cache
    _round_config_cache = config


def get_round_config() -> RoundConfig:
    """
    Return current round config from cache loaded from DB.
    Raises when round_config has not been loaded yet.
    """
    if _round_config_cache is not None:
        return _round_config_cache
    raise RuntimeError("round_config is not loaded. Initialize table row id=1 and refresh cache before serving requests.")


async def load_round_config_from_db(session: AsyncSession) -> Optional[RoundConfig]:
    """Load the single row from round_config table. Returns None if table empty or not yet populated."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch
                FROM round_config
                WHERE id = 1
                LIMIT 1
                """
                )
            )
        )
        .mappings()
        .first()
    )
    if not row or row.get("round_size_epochs") is None:
        return None
    return RoundConfig(
        round_size_epochs=float(row["round_size_epochs"]),
        season_size_epochs=float(row["season_size_epochs"]),
        minimum_start_block=int(row["minimum_start_block"]),
        blocks_per_epoch=int(row["blocks_per_epoch"] or 360),
    )


async def refresh_round_config_cache(session: AsyncSession) -> None:
    """Load round_config from DB into cache. Fails when DB row is missing."""
    config = await load_round_config_from_db(session)
    if config is None:
        raise RuntimeError("round_config row id=1 is missing. Backend requires DB round_config and does not fallback to .env.")
    set_round_config_cache(config)


async def upsert_round_config(
    session: AsyncSession,
    validator_uid: int,
    round_size_epochs: float,
    season_size_epochs: float,
    minimum_start_block: int,
    blocks_per_epoch: int = 360,
) -> bool:
    """
    Upsert round_config. Only succeeds if validator_uid is the main validator.
    Returns True if the config was updated, False if not allowed or error.
    """
    # Check main validator
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT main_validator_uid FROM app_runtime_config WHERE id = 1 LIMIT 1
                """
                )
            )
        )
        .mappings()
        .first()
    )
    main_uid = int(row["main_validator_uid"]) if row and row.get("main_validator_uid") is not None else None
    if main_uid is None or validator_uid != main_uid:
        return False

    await session.execute(
        text(
            """
            INSERT INTO round_config (
                id, round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch,
                updated_at, updated_by_validator_uid
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
            "round_size_epochs": round_size_epochs,
            "season_size_epochs": season_size_epochs,
            "minimum_start_block": minimum_start_block,
            "blocks_per_epoch": blocks_per_epoch,
            "uid": validator_uid,
        },
    )
    await session.flush()

    cfg = RoundConfig(
        round_size_epochs=round_size_epochs,
        season_size_epochs=season_size_epochs,
        minimum_start_block=minimum_start_block,
        blocks_per_epoch=blocks_per_epoch,
    )
    set_round_config_cache(cfg)
    return True

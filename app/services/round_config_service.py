"""
Round/season config: single source of truth from DB, written only by main validator.

The backend reads round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch
from the config_season_round table (one row). Only the main validator can persist this config
(via finish_round with round_metadata). No .env fallback is allowed for round timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# In-memory cache so sync code (round_calc, etc.) can read without session.
_config_season_round_cache: Optional["ConfigSeasonRound"] = None


@dataclass
class ConfigSeasonRound:
    round_size_epochs: float
    season_size_epochs: float
    minimum_start_block: int
    blocks_per_epoch: int

    def round_blocks(self) -> int:
        return int(self.round_size_epochs * self.blocks_per_epoch)

    def season_blocks(self) -> int:
        return int(self.season_size_epochs * self.blocks_per_epoch)


def set_config_season_round_cache(config: Optional[ConfigSeasonRound]) -> None:
    """Set the in-memory cache (called at startup from DB or when main validator upserts)."""
    global _config_season_round_cache
    _config_season_round_cache = config


def get_config_season_round() -> ConfigSeasonRound:
    """
    Return current round config from cache loaded from DB.
    Raises when config_season_round has not been loaded yet.
    """
    if _config_season_round_cache is not None:
        return _config_season_round_cache
    raise RuntimeError("config_season_round is not loaded. Initialize table row id=1 and refresh cache before serving requests.")


async def load_config_season_round_from_db(session: AsyncSession) -> Optional[ConfigSeasonRound]:
    """Load the single row from config_season_round table. Returns None if table empty or not yet populated."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT round_size_epochs, season_size_epochs, minimum_start_block, blocks_per_epoch
                FROM config_season_round
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
    return ConfigSeasonRound(
        round_size_epochs=float(row["round_size_epochs"]),
        season_size_epochs=float(row["season_size_epochs"]),
        minimum_start_block=int(row["minimum_start_block"]),
        blocks_per_epoch=int(row["blocks_per_epoch"] or 360),
    )


async def refresh_config_season_round_cache(session: AsyncSession) -> None:
    """Load config_season_round from DB into cache. Fails when DB row is missing."""
    config = await load_config_season_round_from_db(session)
    if config is None:
        raise RuntimeError("config_season_round row id=1 is missing. Backend requires DB config_season_round and does not fallback to .env.")
    set_config_season_round_cache(config)


async def upsert_config_season_round(
    session: AsyncSession,
    validator_uid: int,
    round_size_epochs: float,
    season_size_epochs: float,
    minimum_start_block: int,
    blocks_per_epoch: int = 360,
) -> bool:
    """
    Upsert config_season_round. Only succeeds if validator_uid is the main validator.
    Returns True if the config was updated, False if not allowed or error.
    """
    # Check main validator
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT main_validator_uid FROM config_app_runtime WHERE id = 1 LIMIT 1
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
            INSERT INTO config_season_round (
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

    cfg = ConfigSeasonRound(
        round_size_epochs=round_size_epochs,
        season_size_epochs=season_size_epochs,
        minimum_start_block=minimum_start_block,
        blocks_per_epoch=blocks_per_epoch,
    )
    set_config_season_round_cache(cfg)
    return True

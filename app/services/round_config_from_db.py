"""
Round config derived from validator round data in DB.

Use these helpers so the backend uses the same timing as the validator (main):
round/season boundaries and block lengths come from what the validator sent,
not from duplicate env (ROUND_SIZE_EPOCHS, etc.). Env config is only fallback
when no validator round data exists yet.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_round_blocks_from_latest_round(session: AsyncSession) -> Optional[int]:
    """
    Return (end_block - start_block) from the latest round in DB that has both set.
    Used to infer round length in blocks when the validator did not send end_block.
    """
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT (r.end_block - r.start_block) AS block_len
                FROM rounds r
                WHERE r.start_block IS NOT NULL
                  AND r.end_block IS NOT NULL
                  AND (r.end_block - r.start_block) > 0
                ORDER BY r.round_id DESC
                LIMIT 1
                """
                )
            )
        )
        .mappings()
        .first()
    )
    if row and row.get("block_len") is not None:
        return int(row["block_len"])
    # Fallback: round_validators might have boundaries when rounds table is not yet filled
    rv = (
        (
            await session.execute(
                text(
                    """
                SELECT (end_block - start_block) AS block_len
                FROM round_validators
                WHERE start_block IS NOT NULL AND end_block IS NOT NULL
                  AND (end_block - start_block) > 0
                ORDER BY round_validator_id DESC
                LIMIT 1
                """
                )
            )
        )
        .mappings()
        .first()
    )
    if rv and rv.get("block_len") is not None:
        return int(rv["block_len"])
    return None


async def get_round_containing_block(session: AsyncSession, block: int) -> Optional[Dict[str, Any]]:
    """
    Return the round (from DB) that contains the given block, or None.
    Uses start_block/end_block that the validator sent.
    """
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT r.round_id, s.season_number, r.round_number_in_season,
                       r.start_block, r.end_block, r.start_epoch, r.end_epoch
                FROM rounds r
                JOIN seasons s ON s.season_id = r.season_id
                WHERE r.start_block IS NOT NULL AND r.end_block IS NOT NULL
                  AND r.start_block <= :block AND r.end_block >= :block
                ORDER BY r.round_id DESC
                LIMIT 1
                """
                ),
                {"block": block},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def get_previous_round_ids(session: AsyncSession, before_round_id: int, limit: int = 3) -> List[int]:
    """
    Return up to `limit` round_ids that are strictly before the given round_id
    (by round_id descending), so we can cache "last N completed rounds".
    """
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT round_id FROM rounds
                WHERE round_id < :before_round_id
                ORDER BY round_id DESC
                LIMIT :limit
                """
                ),
                {"before_round_id": before_round_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [int(r["round_id"]) for r in rows if r.get("round_id") is not None]

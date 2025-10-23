from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import settings


@dataclass
class RoundBoundaries:
    round_number: int
    start_block: int
    end_block: int
    start_epoch: float
    end_epoch: float


def _round_blocks() -> int:
    return int(settings.ROUND_SIZE_EPOCHS * settings.BLOCKS_PER_EPOCH)


def block_to_epoch(block: int) -> float:
    return block / float(settings.BLOCKS_PER_EPOCH)


def compute_round_number(current_block: int) -> int:
    """Compute 1-based round number from chain height.

    Returns 0 when current_block is at or before the DZ gate.
    """
    base = int(settings.DZ_STARTING_BLOCK)
    if current_block <= base:
        return 0
    length = _round_blocks()
    idx = (current_block - base) // length
    return int(idx + 1)


def compute_boundaries_for_round(round_number: int) -> RoundBoundaries:
    if round_number <= 0:
        # before first window
        start_block = int(settings.DZ_STARTING_BLOCK)
        end_block = start_block + _round_blocks()
        return RoundBoundaries(
            round_number=0,
            start_block=start_block,
            end_block=end_block,
            start_epoch=block_to_epoch(start_block),
            end_epoch=block_to_epoch(end_block),
        )

    start_block = int(settings.DZ_STARTING_BLOCK) + (round_number - 1) * _round_blocks()
    end_block = int(settings.DZ_STARTING_BLOCK) + round_number * _round_blocks()
    return RoundBoundaries(
        round_number=round_number,
        start_block=start_block,
        end_block=end_block,
        start_epoch=block_to_epoch(start_block),
        end_epoch=block_to_epoch(end_block),
    )


def progress_for_block(current_block: int, boundaries: RoundBoundaries) -> float:
    total = max(1, boundaries.end_block - boundaries.start_block)
    done = max(0, min(current_block - boundaries.start_block, total))
    return max(0.0, min(float(done) / float(total), 1.0))


def is_inside_window(current_block: int, boundaries: RoundBoundaries) -> bool:
    return current_block > boundaries.start_block and current_block <= boundaries.end_block


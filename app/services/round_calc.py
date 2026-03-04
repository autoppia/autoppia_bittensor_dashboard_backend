from __future__ import annotations

from dataclasses import dataclass

from app.services.round_config_service import get_round_config


@dataclass
class RoundBoundaries:
    round_number: int
    start_block: int
    end_block: int
    start_epoch: float
    end_epoch: float


def _round_blocks() -> int:
    return get_round_config().round_blocks()


def block_to_epoch(block: int) -> float:
    cfg = get_round_config()
    return block / float(cfg.blocks_per_epoch)


def compute_round_number(current_block: int) -> int:
    """Compute 1-based round number from chain height.

    Returns 0 when current_block is at or before the minimum start block.
    """
    cfg = get_round_config()
    base = cfg.minimum_start_block
    if current_block <= base:
        return 0
    length = cfg.round_blocks()
    idx = (current_block - base) // length
    return int(idx + 1)


def compute_boundaries_for_round(round_number: int) -> RoundBoundaries:
    cfg = get_round_config()
    if round_number <= 0:
        start_block = cfg.minimum_start_block
        end_block = start_block + cfg.round_blocks()
        return RoundBoundaries(
            round_number=0,
            start_block=start_block,
            end_block=end_block,
            start_epoch=block_to_epoch(start_block),
            end_epoch=block_to_epoch(end_block),
        )

    start_block = cfg.minimum_start_block + (round_number - 1) * cfg.round_blocks()
    end_block = cfg.minimum_start_block + round_number * cfg.round_blocks()
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


def compute_season_number(start_block: int) -> int:
    """Compute 1-based season number from start_block.

    Uses the same minimum_start_block as rounds, but with season_size_epochs.
    """
    cfg = get_round_config()
    base = cfg.minimum_start_block
    if start_block < base:
        return 0
    season_block_length = cfg.season_blocks()
    season_index = (start_block - base) // season_block_length
    return int(season_index + 1)


def compute_round_number_in_season(current_block: int, round_block_length: int) -> int:
    """Compute 1-based round number within the current season.

    Args:
        current_block: Current blockchain block height
        round_block_length: Number of blocks per round

    Returns:
        1-based round number within the season (1, 2, 3, ...)
    """
    cfg = get_round_config()
    base = cfg.minimum_start_block
    if current_block < base:
        return 0

    season_block_length = cfg.season_blocks()
    season_start_block = base + ((current_block - base) // season_block_length) * season_block_length

    blocks_into_season = current_block - season_start_block
    round_index = blocks_into_season // round_block_length
    return int(round_index + 1)

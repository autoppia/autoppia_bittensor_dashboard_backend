from __future__ import annotations

from app.services.round_calc import (
    RoundBoundaries,
    block_to_epoch,
    compute_boundaries_for_round,
    compute_round_number,
    compute_round_number_in_season,
    compute_season_number,
    is_inside_window,
    progress_for_block,
)
from app.services.round_config_service import ConfigSeasonRound, set_config_season_round_cache


def _cfg() -> ConfigSeasonRound:
    return ConfigSeasonRound(
        round_size_epochs=0.5,
        season_size_epochs=2.0,
        minimum_start_block=1_000,
        blocks_per_epoch=100,
    )


def test_round_number_and_boundaries() -> None:
    set_config_season_round_cache(_cfg())
    assert compute_round_number(1_000) == 0
    assert compute_round_number(1_001) == 1
    assert compute_round_number(1_050) == 2

    b = compute_boundaries_for_round(2)
    assert b.round_number == 2
    assert b.start_block == 1_050
    assert b.end_block == 1_100
    assert block_to_epoch(b.start_block) == 10.5


def test_season_progress_and_window_helpers() -> None:
    set_config_season_round_cache(_cfg())
    assert compute_season_number(999) == 0
    assert compute_season_number(1_000) == 1
    assert compute_season_number(1_200) == 2
    assert compute_round_number_in_season(1_000, 50) == 1
    assert compute_round_number_in_season(1_149, 50) == 3

    boundaries = RoundBoundaries(round_number=1, start_block=1_000, end_block=1_050, start_epoch=10.0, end_epoch=10.5)
    assert progress_for_block(999, boundaries) == 0.0
    assert progress_for_block(1_025, boundaries) == 0.5
    assert progress_for_block(2_000, boundaries) == 1.0
    assert is_inside_window(1_000, boundaries) is True
    assert is_inside_window(1_051, boundaries) is False

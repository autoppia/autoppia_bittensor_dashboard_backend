"""Utility script to pre-warm Redis caches for critical dashboard endpoints.

This script is intended to be executed periodically (e.g., from cron) so that
frontend users always hit warm caches instead of triggering expensive DB
queries. It reuses the same FastAPI route handlers that already keep Redis
updated, so the cache keys remain consistent with the API decorators.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Iterable

from app.api.ui import overview as overview_api
from app.api.ui import rounds as rounds_api
from app.db.session import AsyncSessionLocal
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)


async def _safe_call(label: str, coro: asyncio.Future) -> None:
    """Execute a coroutine and log failures without aborting the run."""
    try:
        await coro
        logger.info("✅ Cache warmed: %s", label)
    except Exception as exc:  # noqa: BLE001
        logger.exception("❌ Failed to warm %s: %s", label, exc)


async def _warm_overview(session) -> None:
    """Warm the overview endpoints (metrics, validators, leaderboard, etc.)."""
    await _safe_call("overview", overview_api.get_overview(session=session))
    await _safe_call(
        "overview_metrics",
        overview_api.get_overview_metrics(force=False, session=session),
    )
    await _safe_call(
        "overview_validators",
        overview_api.get_validators(
            session=session,
            page=1,
            limit=10,
            status=None,
            sortBy="weight",
            sortOrder="desc",
        ),
    )
    await _safe_call(
        "overview_leaderboard",
        overview_api.get_leaderboard(
            session=session,
            time_range=None,
            limit=50,
        ),
    )
    await _safe_call("overview_statistics", overview_api.get_statistics(session=session))
    await _safe_call("overview_network_status", overview_api.get_network_status(session=session))
    await _safe_call("overview_current_round", overview_api.get_current_round(session=session))


async def _warm_round_lists(session) -> None:
    """Warm cached endpoints that list rounds."""
    await _safe_call(
        "rounds_list",
        rounds_api.list_rounds(
            session=session,
            page=1,
            limit=10,
            status=None,
            sortBy="round",
            sortOrder="desc",
            skip=None,
        ),
    )
    await _safe_call("rounds_current_overview", rounds_api.get_current_round(session=session))


async def _warm_round_details(session, round_numbers: Iterable[int], miner_limit: int) -> None:
    """Warm per-round caches (detail, statistics, miners) for the given rounds."""
    for number in round_numbers:
        round_id = str(number)
        await _safe_call(
            f"round_basic:{round_id}",
            rounds_api.get_round_basic(round_id=round_id, session=session),
        )
        await _safe_call(
            f"round_detail:{round_id}",
            rounds_api.get_round(round_id=round_id, session=session),
        )
        await _safe_call(
            f"round_statistics:{round_id}",
            rounds_api.get_round_statistics(round_id=round_id, session=session),
        )
        await _safe_call(
            f"round_miners:{round_id}",
            rounds_api.get_round_miners(
                round_id=round_id,
                session=session,
                page=1,
                limit=miner_limit,
                sortBy="score",
                sortOrder="desc",
                success=None,
                minScore=None,
                maxScore=None,
            ),
        )


async def warm_caches(rounds_to_warm: int, miner_limit: int) -> None:
    """Entry point that opens a DB session and triggers the warmers."""
    async with AsyncSessionLocal() as session:
        logger.info(
            "🔄 Warming caches (rounds=%s, miners_limit=%s)",
            rounds_to_warm,
            miner_limit,
        )
        await _warm_overview(session)
        await _warm_round_lists(session)

        newdb = UIDataService(session)
        recent_rounds, _ = await newdb.get_rounds_list(page=1, limit=rounds_to_warm)
        round_numbers: list[int] = [int(r.get("id", 0)) for r in recent_rounds if int(r.get("id", 0)) > 0]
        if not round_numbers:
            logger.warning("No round numbers found to warm.")
            return

        await _warm_round_details(session, round_numbers, miner_limit)
        logger.info("🎉 Cache warming finished for %s rounds.", len(round_numbers))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm Redis caches for dashboard endpoints.")
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="Number of most recent rounds to warm (default: 4).",
    )
    parser.add_argument(
        "--miner-limit",
        type=int,
        default=100,
        help="Number of miners per round to warm (default: 100).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level for the warmer (default: INFO).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    asyncio.run(warm_caches(rounds_to_warm=args.rounds, miner_limit=args.miner_limit))


if __name__ == "__main__":
    main()

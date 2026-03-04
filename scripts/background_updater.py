#!/usr/bin/env python3
"""
Background Data Updater - Standalone Process

This script runs as a separate PM2 process and updates:
- Metagraph data (validators) every 30 minutes
- Subnet price every 5 minutes
- Current block every 30 seconds

This replaces the background threads that were running inside the FastAPI process.
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Add the project root to Python path (scripts/ is one level down from root)
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.chain_state import refresh_block_now  # noqa: E402
from app.services.metagraph_service import (  # noqa: E402
    METAGRAPH_CACHE_TTL,
    MetagraphError,
    get_last_update_time,
    refresh_metagraph_data,
)
from app.services.redis_cache import redis_cache  # noqa: E402

# Configure logging - send INFO to stdout, WARNING/ERROR to stderr
# This ensures PM2 routes logs correctly (stdout -> out.log, stderr -> error.log)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove default handlers
root_logger.handlers.clear()

# Handler for INFO and below -> stdout (out.log)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
stdout_handler.addFilter(lambda record: record.levelno <= logging.INFO)

# Handler for WARNING and above -> stderr (error.log)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.WARNING)

# Format for both handlers
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
stdout_handler.setFormatter(formatter)
stderr_handler.setFormatter(formatter)

# Add handlers
root_logger.addHandler(stdout_handler)
root_logger.addHandler(stderr_handler)

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, min_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(min_value, value)


# Update intervals (env-overridable).
METAGRAPH_UPDATE_INTERVAL = _int_env("UPDATER_METAGRAPH_INTERVAL_SEC", 30 * 60, 60)
PRICE_UPDATE_INTERVAL = _int_env("UPDATER_PRICE_INTERVAL_SEC", 5 * 60, 60)
# Use a safer default cadence; block value is estimated between refreshes.
BLOCK_UPDATE_INTERVAL = _int_env("UPDATER_BLOCK_INTERVAL_SEC", 5 * 60, 30)
ROUND_CACHE_INTERVAL = _int_env("UPDATER_ROUND_CACHE_INTERVAL_SEC", 4 * 60 * 60, 60)
ROUND_RECONCILE_INTERVAL = _int_env("UPDATER_ROUND_RECONCILE_INTERVAL_SEC", 30, 10)

# Redis keys for price
REDIS_KEY_SUBNET_PRICE = "subnet:price"
REDIS_KEY_PRICE_LAST_UPDATE = "subnet:price:last_update"
REDIS_KEY_BLOCK_LAST_UPDATE = "chain:block_timestamp"


def fetch_and_cache_block() -> bool:
    """Fetch current block from chain and cache in Redis."""
    try:
        block = refresh_block_now()
        if block is not None:
            logger.info(f"✅ Current block updated: {block}")
            return True
        logger.warning("⚠️  Could not fetch current block")
        return False
    except Exception as exc:
        logger.error(f"❌ Failed to update block: {exc}")
        return False


def fetch_and_cache_price() -> bool:
    """Fetch subnet price and cache in Redis."""
    try:
        from app.services.subnet_utils import _env_fallback, _try_fetch_price_sync

        netuid = settings.VALIDATOR_NETUID

        # Try to fetch from chain
        price = _try_fetch_price_sync(netuid)
        source = "chain"

        if price is None or price <= 0:
            # Fallback to env if blockchain fetch fails
            price = _env_fallback(netuid)
            source = "env-fallback"
            logger.warning(f"⚠️  Could not fetch price from blockchain, using env fallback: {price:.6f} TAO")

        # Store in Redis (either from chain or fallback)
        redis_cache.set(REDIS_KEY_SUBNET_PRICE, float(price), ttl=PRICE_UPDATE_INTERVAL * 2)
        redis_cache.set(REDIS_KEY_PRICE_LAST_UPDATE, float(time.time()), ttl=PRICE_UPDATE_INTERVAL * 2)
        logger.info(f"✅ Subnet price updated: {price:.6f} TAO (source: {source})")
        return True
    except Exception as exc:
        logger.error(f"❌ Failed to update subnet price: {exc}")
        return False


def perform_metagraph_update() -> bool:
    """Refresh metagraph data and cache in Redis."""
    try:
        logger.info("🔄 Refreshing metagraph data...")
        refresh_metagraph_data()
        logger.info("✅ Metagraph data refreshed successfully")
        return True
    except MetagraphError as exc:
        logger.error(f"❌ Metagraph update failed: {exc}")
        return False
    except Exception as exc:
        logger.error(f"❌ Unexpected error during metagraph update: {exc}", exc_info=True)
        return False


async def _cache_recent_rounds_async(current_block: int) -> bool:
    """
    Prefer round IDs from DB (last validator round containing current_block, then previous 3).
    Fall back to config-based compute_round_number when no round in DB.
    """
    import requests

    from app.services.round_config_from_db import (
        get_previous_round_ids,
        get_round_containing_block,
    )

    async with AsyncSessionLocal() as session:
        round_row = await get_round_containing_block(session, current_block)
        if round_row and round_row.get("round_id") is not None:
            current_round_id = int(round_row["round_id"])
            round_ids_to_cache = await get_previous_round_ids(session, current_round_id, limit=3)
        else:
            from app.services.round_calc import compute_round_number

            current_round = compute_round_number(current_block)
            # Fallback: API accepts round_id; if DB round_id matches global index, use it
            round_ids_to_cache = [
                current_round - 1,
                current_round - 2,
                current_round - 3,
            ]

    cached_count = 0
    for round_id in round_ids_to_cache:
        if round_id <= 0:
            continue
        try:
            response = requests.get(
                f"http://localhost:8080/api/v1/rounds/{round_id}",
                timeout=60,
            )
            if response.status_code == 200:
                logger.info(f"✅ Cached round {round_id}")
                cached_count += 1
            else:
                logger.warning(f"⚠️  Failed to cache round {round_id}: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Error caching round {round_id}: {e}")

    if cached_count > 0:
        logger.info(f"✅ Cached {cached_count} rounds successfully")
        return True
    return False


def cache_recent_rounds() -> bool:
    """
    Cache recent completed rounds by calling the API endpoint.
    Uses last validator round from DB when available; else env-based round number.
    """
    try:
        from app.services.chain_state import get_current_block_estimate

        current_block = get_current_block_estimate()
        if not current_block:
            logger.warning("⚠️  Could not get current block for round caching")
            return False

        return asyncio.run(_cache_recent_rounds_async(current_block))
    except Exception as exc:
        logger.error(f"❌ Failed to cache rounds: {exc}")
        return False


async def _reconcile_rounds_and_seasons_async(current_block: int) -> dict[str, int]:
    closed_rounds = 0
    async with AsyncSessionLocal() as session:
        rounds_result = await session.execute(
            text(
                """
                UPDATE rounds
                SET
                    status = 'finished',
                    consensus_status = CASE
                        WHEN LOWER(COALESCE(consensus_status, '')) = 'pending' THEN 'failed'
                        ELSE consensus_status
                    END,
                    ended_at = COALESCE(ended_at, NOW()),
                    closed_by_validator_uid = COALESCE(closed_by_validator_uid, opened_by_validator_uid),
                    end_block = COALESCE(end_block, planned_end_block, start_block),
                    end_epoch = COALESCE(end_epoch, start_epoch),
                    updated_at = NOW()
                WHERE status = 'active'
                  AND planned_end_block IS NOT NULL
                  AND planned_end_block < :current_block
                """
            ),
            {"current_block": int(current_block)},
        )
        closed_rounds = int(rounds_result.rowcount or 0)

        await session.commit()

    return {"closed_rounds": closed_rounds}


def reconcile_rounds_and_seasons(current_block: int) -> bool:
    try:
        result = asyncio.run(_reconcile_rounds_and_seasons_async(current_block))
        closed_rounds = int(result.get("closed_rounds", 0) or 0)
        if closed_rounds:
            logger.info(
                "✅ Reconciliation closed expired rounds=%s (current_block=%s)",
                closed_rounds,
                current_block,
            )
        return True
    except Exception as exc:
        logger.error(f"❌ Failed round/season reconciliation: {exc}", exc_info=True)
        return False


def main():
    """Main loop for background updates."""
    logger.info("=" * 80)
    logger.info("🚀 Background Data Updater Starting (Standalone Process)")
    logger.info(f"   - Metagraph update interval: {METAGRAPH_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info(f"   - Price update interval: {PRICE_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info(f"   - Block update interval: {BLOCK_UPDATE_INTERVAL} seconds")
    logger.info(f"   - Round/Season reconcile interval: {ROUND_RECONCILE_INTERVAL} seconds")
    logger.info(f"   - Round cache interval: {ROUND_CACHE_INTERVAL / 3600:.0f} hours")
    logger.info(f"   - Metagraph cache TTL: {METAGRAPH_CACHE_TTL / 60:.0f} minutes")
    logger.info("=" * 80)

    # Wait for Redis to be available
    max_retries = 30
    retry_count = 0
    while retry_count < max_retries:
        if redis_cache.is_available():
            logger.info("✅ Redis is available, starting updates")
            break
        retry_count += 1
        logger.warning(f"⏳ Waiting for Redis ({retry_count}/{max_retries}), retrying in 5 seconds...")
        time.sleep(5)

    if not redis_cache.is_available():
        logger.error("❌ Redis not available after timeout, will retry in 30s")
        time.sleep(30)
        return

    # Check if there's existing data
    last_update = get_last_update_time()
    should_update_immediately = True

    if last_update:
        age_minutes = (time.time() - last_update) / 60
        logger.info(f"📊 Found existing metagraph data in Redis (age: {age_minutes:.1f} minutes)")

        if age_minutes < METAGRAPH_CACHE_TTL / 60:
            should_update_immediately = False
            time_until_next = METAGRAPH_CACHE_TTL - (age_minutes * 60)
            logger.info(f"⏭️  Existing data is fresh, next update in {time_until_next / 60:.1f} minutes")

    # Perform initial update if needed
    if should_update_immediately:
        logger.info("🔄 Performing initial metagraph update...")
        perform_metagraph_update()

    # Prime price/block only if cache is stale or missing.
    try:
        last_price_update_cached = redis_cache.get(REDIS_KEY_PRICE_LAST_UPDATE)
        price_age = (time.time() - float(last_price_update_cached)) if last_price_update_cached is not None else None
        if price_age is None or price_age >= PRICE_UPDATE_INTERVAL:
            logger.info("💰 Performing initial price update...")
            fetch_and_cache_price()
        else:
            logger.info(
                "⏭️  Skipping initial price update (fresh cache: %.1fs old)",
                price_age,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed initial price update check: {exc}")

    try:
        last_block_update_cached = redis_cache.get(REDIS_KEY_BLOCK_LAST_UPDATE)
        block_age = (time.time() - float(last_block_update_cached)) if last_block_update_cached is not None else None
        if block_age is None or block_age >= BLOCK_UPDATE_INTERVAL:
            logger.info("🔢 Performing initial block update...")
            fetch_and_cache_block()
        else:
            logger.info(
                "⏭️  Skipping initial block update (fresh cache: %.1fs old)",
                block_age,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed initial block update check: {exc}")

    # Initialize counters and timestamps
    metagraph_update_count = 0
    price_update_count = 0
    block_update_count = 0
    round_cache_count = 0
    reconcile_count = 0
    last_metagraph_update = last_update or time.time()
    last_price_update = time.time()
    last_block_update = time.time()
    last_round_cache = time.time()
    last_reconcile = time.time()

    # Main update loop
    logger.info("🔄 Entering main update loop...")
    try:
        while True:
            now = time.time()

            # Check if metagraph needs update
            time_since_metagraph = now - last_metagraph_update
            metagraph_due = time_since_metagraph >= METAGRAPH_UPDATE_INTERVAL

            # Check if price needs update
            time_since_price = now - last_price_update
            price_due = time_since_price >= PRICE_UPDATE_INTERVAL

            # Check if block needs update
            time_since_block = now - last_block_update
            block_due = time_since_block >= BLOCK_UPDATE_INTERVAL

            # Check if round cache needs update
            time_since_round_cache = now - last_round_cache
            round_cache_due = time_since_round_cache >= ROUND_CACHE_INTERVAL

            # Check if reconcile is due
            time_since_reconcile = now - last_reconcile
            reconcile_due = time_since_reconcile >= ROUND_RECONCILE_INTERVAL

            # Perform updates if due
            if metagraph_due:
                logger.info("📊 Metagraph update due...")
                metagraph_update_count += 1
                perform_metagraph_update()
                last_metagraph_update = now

            if price_due:
                logger.info("💰 Price update due...")
                price_update_count += 1
                fetch_and_cache_price()
                last_price_update = now

            if block_due:
                logger.info("🔢 Block update due...")
                block_update_count += 1
                fetch_and_cache_block()
                last_block_update = now

            if round_cache_due:
                logger.info("📦 Round cache update due...")
                round_cache_count += 1
                cache_recent_rounds()
                last_round_cache = now

            if reconcile_due:
                reconcile_count += 1
                try:
                    current_block = refresh_block_now()
                    if current_block is not None:
                        reconcile_rounds_and_seasons(int(current_block))
                except Exception as exc:
                    logger.error(f"❌ Reconcile run failed: {exc}", exc_info=True)
                last_reconcile = now

            # Calculate next wakeup time (whichever comes first)
            time_until_metagraph = METAGRAPH_UPDATE_INTERVAL - time_since_metagraph
            time_until_price = PRICE_UPDATE_INTERVAL - time_since_price
            time_until_block = BLOCK_UPDATE_INTERVAL - time_since_block
            time_until_round_cache = ROUND_CACHE_INTERVAL - time_since_round_cache
            time_until_reconcile = ROUND_RECONCILE_INTERVAL - time_since_reconcile
            time_until_next = min(time_until_metagraph, time_until_price, time_until_block, time_until_round_cache, time_until_reconcile, 10)  # Max 10s sleep

            if time_until_next > 0:
                time.sleep(time_until_next)

            # Log periodic status
            total_updates = metagraph_update_count + price_update_count + block_update_count + round_cache_count + reconcile_count
            if total_updates > 0 and total_updates % 50 == 0:
                logger.info(
                    "📊 Updater status: %s metagraph, %s price, %s block, %s round cache, %s reconcile updates",
                    metagraph_update_count,
                    price_update_count,
                    block_update_count,
                    round_cache_count,
                    reconcile_count,
                )

    except KeyboardInterrupt:
        logger.info("🛑 Received shutdown signal")
    except Exception as exc:
        logger.error(f"❌ Fatal error in updater loop: {exc}", exc_info=True)
    finally:
        logger.info("=" * 80)
        logger.info("🛑 Background Data Updater Stopped")
        logger.info(f"   - Metagraph updates performed: {metagraph_update_count}")
        logger.info(f"   - Price updates performed: {price_update_count}")
        logger.info(f"   - Block updates performed: {block_update_count}")
        logger.info(f"   - Round cache updates performed: {round_cache_count}")
        logger.info(f"   - Reconcile updates performed: {reconcile_count}")
        logger.info("=" * 80)


def run_forever():
    """Keep the updater alive even if the main loop exits unexpectedly."""
    while True:
        main()
        logger.warning("⚠️  Updater loop exited; restarting in 30s")
        time.sleep(30)


if __name__ == "__main__":
    run_forever()

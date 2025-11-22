#!/usr/bin/env python3
"""
Background Data Updater - Standalone Process

This script runs as a separate PM2 process and updates:
- Metagraph data (validators) every 30 minutes
- Subnet price every 5 minutes  
- Current block every 30 seconds

This replaces the background threads that were running inside the FastAPI process.
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.config import settings
from app.services.metagraph_service import (
    refresh_metagraph_data,
    get_last_update_time,
    MetagraphError,
    METAGRAPH_CACHE_TTL,
)
from app.services.redis_cache import redis_cache
from app.services.chain_state import refresh_block_now

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Update intervals
METAGRAPH_UPDATE_INTERVAL = 30 * 60  # 30 minutes
PRICE_UPDATE_INTERVAL = 5 * 60  # 5 minutes
BLOCK_UPDATE_INTERVAL = 30  # 30 seconds

# Redis keys for price
REDIS_KEY_SUBNET_PRICE = "subnet:price"
REDIS_KEY_PRICE_LAST_UPDATE = "subnet:price:last_update"


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
        from app.services.subnet_utils import get_subnet_price

        price = get_subnet_price(settings.VALIDATOR_NETUID)
        if price is not None and price > 0:
            redis_cache.set(REDIS_KEY_SUBNET_PRICE, str(price), ttl=PRICE_UPDATE_INTERVAL * 2)
            redis_cache.set(REDIS_KEY_PRICE_LAST_UPDATE, str(time.time()), ttl=PRICE_UPDATE_INTERVAL * 2)
            logger.info(f"✅ Subnet price updated: {price:.6f} TAO (source: chain)")
            return True
        logger.warning(f"⚠️  Invalid price returned: {price}")
        return False
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


def main():
    """Main loop for background updates."""
    logger.info("=" * 80)
    logger.info("🚀 Background Data Updater Starting (Standalone Process)")
    logger.info(f"   - Metagraph update interval: {METAGRAPH_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info(f"   - Price update interval: {PRICE_UPDATE_INTERVAL / 60:.0f} minutes")
    logger.info(f"   - Block update interval: {BLOCK_UPDATE_INTERVAL} seconds")
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
        logger.warning(
            f"⏳ Waiting for Redis ({retry_count}/{max_retries}), retrying in 5 seconds..."
        )
        time.sleep(5)

    if not redis_cache.is_available():
        logger.error("❌ Redis not available after timeout, exiting")
        sys.exit(1)

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
    
    # Initialize counters and timestamps
    metagraph_update_count = 0
    price_update_count = 0
    block_update_count = 0
    last_metagraph_update = last_update or time.time()
    last_price_update = time.time()
    last_block_update = time.time()

    # Main update loop
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

            # Perform updates if due
            if metagraph_due:
                metagraph_update_count += 1
                perform_metagraph_update()
                last_metagraph_update = now

            if price_due:
                price_update_count += 1
                fetch_and_cache_price()
                last_price_update = now

            if block_due:
                block_update_count += 1
                fetch_and_cache_block()
                last_block_update = now

            # Calculate next wakeup time (whichever comes first)
            time_until_metagraph = METAGRAPH_UPDATE_INTERVAL - time_since_metagraph
            time_until_price = PRICE_UPDATE_INTERVAL - time_since_price
            time_until_block = BLOCK_UPDATE_INTERVAL - time_since_block
            time_until_next = min(
                time_until_metagraph, time_until_price, time_until_block, 10
            )  # Max 10s sleep

            if time_until_next > 0:
                time.sleep(time_until_next)

            # Log periodic status
            total_updates = metagraph_update_count + price_update_count + block_update_count
            if total_updates > 0 and total_updates % 50 == 0:
                logger.info(
                    f"📊 Updater status: {metagraph_update_count} metagraph, "
                    f"{price_update_count} price, {block_update_count} block updates"
                )

    except KeyboardInterrupt:
        logger.info("🛑 Received shutdown signal")
    except Exception as exc:
        logger.error(f"❌ Fatal error in updater loop: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("=" * 80)
        logger.info("🛑 Background Data Updater Stopped")
        logger.info(f"   - Metagraph updates performed: {metagraph_update_count}")
        logger.info(f"   - Price updates performed: {price_update_count}")
        logger.info(f"   - Block updates performed: {block_update_count}")
        logger.info("=" * 80)


if __name__ == "__main__":
    main()


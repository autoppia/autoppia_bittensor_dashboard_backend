"""
Background Data Updater - Background Thread

This module provides a background thread that runs within the FastAPI application
process and refreshes various blockchain data in Redis:

1. Metagraph data (validators: stake, vtrust, version) - every 30 minutes
2. Subnet price - every 5 minutes

The thread is automatically started when FastAPI starts and gracefully stopped
when the application shuts down.

Usage:
    # In main.py or startup event:
    from app.services.metagraph_updater_thread import start_metagraph_updater, stop_metagraph_updater

    @app.on_event("startup")
    async def startup():
        start_metagraph_updater()

    @app.on_event("shutdown")
    async def shutdown():
        stop_metagraph_updater()
"""

import logging
import threading
import time
from datetime import datetime

from app.config import settings
from app.services.metagraph_service import (
    METAGRAPH_CACHE_TTL,
    MetagraphError,
    get_last_update_time,
    get_update_status,
    refresh_metagraph_data,
)
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)

# Update intervals
METAGRAPH_UPDATE_INTERVAL = 30 * 60  # 30 minutes (1800 seconds)
PRICE_UPDATE_INTERVAL = 5 * 60  # 5 minutes (300 seconds)
BLOCK_UPDATE_INTERVAL = 30  # 30 seconds

# Redis keys for price
REDIS_KEY_SUBNET_PRICE = "subnet:price"
REDIS_KEY_PRICE_LAST_UPDATE = "subnet:price:last_update"

# Global state
_updater_thread: threading.Thread | None = None
_should_stop = threading.Event()
_is_running = False


def _fetch_and_cache_block() -> bool:
    """
    Fetch current block from blockchain and store in Redis.

    Returns:
        True if successful, False otherwise
    """
    try:
        from app.services.chain_state import refresh_block_now

        block = refresh_block_now()
        if block is not None:
            logger.debug("✅ Block updated: %s", block)
            return True
        else:
            logger.warning("Failed to fetch current block from chain")
            return False
    except Exception as exc:
        logger.error("Error fetching/caching block: %s", exc, exc_info=True)
        return False


def _fetch_and_cache_price() -> bool:
    """
    Fetch subnet price from blockchain and store in Redis.

    Returns:
        True if successful, False otherwise
    """
    try:
        # Import here to avoid circular dependencies
        from app.services.subnet_utils import _env_fallback, _try_fetch_price_sync

        netuid = settings.VALIDATOR_NETUID

        # Try to fetch from chain
        price = _try_fetch_price_sync(netuid)
        source = "chain"

        if price is None or price <= 0:
            # Fallback to env
            price = _env_fallback(netuid)
            source = "env-fallback"

        # Store in Redis
        redis_cache.set(REDIS_KEY_SUBNET_PRICE, float(price), ttl=PRICE_UPDATE_INTERVAL)
        redis_cache.set(REDIS_KEY_PRICE_LAST_UPDATE, time.time(), ttl=PRICE_UPDATE_INTERVAL)

        logger.info("✅ Subnet price updated: %.6f TAO (source: %s)", price, source)
        return True

    except Exception as exc:
        logger.error("❌ Failed to update subnet price: %s", exc)
        return False


def _updater_worker():
    """
    Background worker function that runs in a separate thread.
    Refreshes:
    - Metagraph data every 30 minutes
    - Subnet price every 5 minutes
    """
    global _is_running

    logger.info("=" * 80)
    logger.info("🚀 Background Data Updater Thread Starting")
    logger.info("   - Metagraph update interval: %.0f minutes", METAGRAPH_UPDATE_INTERVAL / 60)
    logger.info("   - Price update interval: %.0f minutes", PRICE_UPDATE_INTERVAL / 60)
    logger.info("   - Block update interval: %s seconds", BLOCK_UPDATE_INTERVAL)
    logger.info("   - Metagraph cache TTL: %.0f minutes", METAGRAPH_CACHE_TTL / 60)
    logger.info("=" * 80)

    _is_running = True

    # Wait for Redis to be available
    max_retries = 30
    retry_count = 0
    while retry_count < max_retries and not _should_stop.is_set():
        if redis_cache.is_available():
            logger.info("✅ Redis is available, starting metagraph updates")
            break
        retry_count += 1
        logger.warning("⏳ Waiting for Redis (%s/%s), retrying in 5 seconds...", retry_count, max_retries)
        time.sleep(5)

    if not redis_cache.is_available():
        logger.error("❌ Redis not available after timeout, metagraph updates disabled")
        _is_running = False
        return

    # Check if there's existing data
    last_update = get_last_update_time()
    should_update_immediately = True

    if last_update:
        age_seconds = time.time() - last_update
        age_minutes = age_seconds / 60
        logger.info("📊 Found existing metagraph data in Redis (age: %.1f minutes)", age_minutes)

        if age_seconds < METAGRAPH_UPDATE_INTERVAL:
            # Data is fresh, skip initial update
            should_update_immediately = False
            logger.info("⏭️  Existing data is fresh, next update in %.1f minutes", (METAGRAPH_UPDATE_INTERVAL - age_seconds) / 60)
        else:
            logger.info("⚠️  Existing data is stale, performing immediate update")
    else:
        logger.info("📭 No existing data in Redis, performing initial update")

    # Perform initial updates if needed
    if should_update_immediately:
        _perform_update()

    # Always update price and block on startup (they're quick)
    _fetch_and_cache_price()
    _fetch_and_cache_block()

    # Main update loop with separate timers for metagraph, price, and block
    metagraph_update_count = 0
    price_update_count = 0
    block_update_count = 0
    last_metagraph_update = time.time()
    last_price_update = time.time()
    last_block_update = time.time()

    while not _should_stop.is_set():
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
            _perform_update()
            last_metagraph_update = now

        if price_due:
            price_update_count += 1
            _fetch_and_cache_price()
            last_price_update = now

        if block_due:
            block_update_count += 1
            _fetch_and_cache_block()
            last_block_update = now

        # Calculate next wakeup time (whichever comes first)
        time_until_metagraph = METAGRAPH_UPDATE_INTERVAL - time_since_metagraph
        time_until_price = PRICE_UPDATE_INTERVAL - time_since_price
        time_until_block = BLOCK_UPDATE_INTERVAL - time_since_block
        time_until_next = min(time_until_metagraph, time_until_price, time_until_block, 10)  # Max 10s sleep

        if time_until_next > 0:
            if _should_stop.wait(timeout=time_until_next):
                break  # Stop flag was set

        # Log periodic status
        total_updates = metagraph_update_count + price_update_count + block_update_count
        if total_updates > 0 and total_updates % 50 == 0:
            logger.info("📊 Updater status: %s metagraph, %s price, %s block updates", metagraph_update_count, price_update_count, block_update_count)

    logger.info("=" * 80)
    logger.info("🛑 Background Data Updater Thread Stopped")
    logger.info("   - Metagraph updates performed: %s", metagraph_update_count)
    logger.info("   - Price updates performed: %s", price_update_count)
    logger.info("=" * 80)
    _is_running = False


def _perform_update() -> bool:
    """
    Perform a single metagraph data update.

    Returns:
        True if update succeeded, False otherwise
    """
    try:
        logger.info("🔄 Starting metagraph update at %s", datetime.now().isoformat())

        start_time = time.time()
        refresh_metagraph_data()
        elapsed = time.time() - start_time

        # Get status after update
        status = get_update_status()
        validator_count = status.get("validator_count", 0)
        vtrust_source = status.get("vtrust_source", "unknown")

        logger.info("✅ Update completed in %.2fs - %s validators (vTrust: %s)", elapsed, validator_count, vtrust_source)
        return True

    except MetagraphError as exc:
        logger.error("❌ Metagraph update failed: %s", exc)
        return False
    except Exception as exc:
        logger.error("❌ Unexpected error during update: %s", exc, exc_info=True)
        return False


def start_metagraph_updater():
    """
    Start the background data updater thread.

    This updates:
    - Metagraph data (validators) every 30 minutes
    - Subnet price every 5 minutes

    This should be called during application startup.
    If the thread is already running, this is a no-op.
    """
    global _updater_thread, _should_stop

    if _updater_thread is not None and _updater_thread.is_alive():
        logger.warning("⚠️  Background updater thread is already running")
        return

    # Reset stop flag
    _should_stop.clear()

    # Start the thread
    _updater_thread = threading.Thread(
        target=_updater_worker,
        name="BackgroundDataUpdater",
        daemon=True,  # Thread will exit when main process exits
    )
    _updater_thread.start()

    logger.info("✅ Background data updater thread started")


def stop_metagraph_updater(timeout: float = 10.0):
    """
    Stop the background data updater thread gracefully.

    This should be called during application shutdown.

    Args:
        timeout: Maximum time to wait for thread to stop (seconds)
    """
    global _updater_thread

    if _updater_thread is None or not _updater_thread.is_alive():
        logger.info("ℹ️  Background updater thread is not running")
        return

    logger.info("🛑 Stopping background updater thread...")

    # Signal thread to stop
    _should_stop.set()

    # Wait for thread to finish
    _updater_thread.join(timeout=timeout)

    if _updater_thread.is_alive():
        logger.warning("⚠️  Background updater thread did not stop within %ss timeout", timeout)
    else:
        logger.info("✅ Background updater thread stopped gracefully")

    _updater_thread = None


def is_updater_running() -> bool:
    """
    Check if the metagraph updater thread is currently running.

    Returns:
        True if running, False otherwise
    """
    return _is_running


def get_price_from_redis() -> float | None:
    """
    Get cached subnet price from Redis (fast, no blockchain calls).

    Returns:
        Cached price or None if not available
    """
    try:
        value = redis_cache.get(REDIS_KEY_SUBNET_PRICE)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    except Exception:  # noqa: BLE001
        return None


def get_price_last_update() -> float | None:
    """
    Get timestamp of last price update.

    Returns:
        Unix timestamp or None
    """
    try:
        return redis_cache.get(REDIS_KEY_PRICE_LAST_UPDATE)
    except Exception:  # noqa: BLE001
        return None


def get_block_from_redis() -> int | None:
    """
    Get current block from Redis (fast, non-blocking).

    Returns:
        Block number or None
    """
    try:
        from app.services.chain_state import REDIS_KEY_CURRENT_BLOCK

        return redis_cache.get(REDIS_KEY_CURRENT_BLOCK)
    except Exception:  # noqa: BLE001
        return None


def get_block_last_update() -> float | None:
    """
    Get timestamp of last block update.

    Returns:
        Unix timestamp or None
    """
    try:
        from app.services.chain_state import REDIS_KEY_BLOCK_TIMESTAMP

        return redis_cache.get(REDIS_KEY_BLOCK_TIMESTAMP)
    except Exception:  # noqa: BLE001
        return None


def get_updater_status() -> dict:
    """
    Get the current status of the background updater thread.

    Returns:
        Status dictionary with running state and last update info
    """
    from app.services.chain_state import get_current_block_estimate

    status = {
        "running": is_updater_running(),
        "thread_alive": _updater_thread is not None and _updater_thread.is_alive(),
        "metagraph": {
            "last_update": get_last_update_time(),
            "update_status": get_update_status(),
        },
        "price": {
            "current_value": get_price_from_redis(),
            "last_update": get_price_last_update(),
        },
        "block": {
            "current_value": get_block_from_redis(),
            "estimated_value": get_current_block_estimate(),
            "last_update": get_block_last_update(),
        },
    }

    # Add age info
    price_last = get_price_last_update()
    if price_last:
        status["price"]["age_seconds"] = int(time.time() - price_last)

    block_last = get_block_last_update()
    if block_last:
        status["block"]["age_seconds"] = int(time.time() - block_last)

    return status

"""
Metagraph Data Updater - Background Thread

This module provides a background thread that runs within the FastAPI application
process and refreshes metagraph data in Redis every 30 minutes.

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
from typing import Optional

from app.services.metagraph_service import (
    refresh_metagraph_data,
    get_update_status,
    get_last_update_time,
    MetagraphError,
    METAGRAPH_CACHE_TTL,
)
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)

# Update interval: 30 minutes (1800 seconds)
UPDATE_INTERVAL_SECONDS = 30 * 60  # 1800 seconds

# Global state
_updater_thread: Optional[threading.Thread] = None
_should_stop = threading.Event()
_is_running = False


def _updater_worker():
    """
    Background worker function that runs in a separate thread.
    Refreshes metagraph data every 30 minutes.
    """
    global _is_running

    logger.info("=" * 80)
    logger.info("🚀 Metagraph Data Updater Thread Starting")
    logger.info(f"   - Update interval: {UPDATE_INTERVAL_SECONDS / 60:.0f} minutes")
    logger.info(f"   - Cache TTL: {METAGRAPH_CACHE_TTL / 60:.0f} minutes")
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
        logger.warning(
            f"⏳ Waiting for Redis ({retry_count}/{max_retries}), "
            f"retrying in 5 seconds..."
        )
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
        logger.info(
            f"📊 Found existing metagraph data in Redis "
            f"(age: {age_minutes:.1f} minutes)"
        )

        if age_seconds < UPDATE_INTERVAL_SECONDS:
            # Data is fresh, skip initial update
            should_update_immediately = False
            logger.info(
                f"⏭️  Existing data is fresh, next update in "
                f"{(UPDATE_INTERVAL_SECONDS - age_seconds) / 60:.1f} minutes"
            )
        else:
            logger.info("⚠️  Existing data is stale, performing immediate update")
    else:
        logger.info("📭 No existing data in Redis, performing initial update")

    # Perform initial update if needed
    if should_update_immediately:
        _perform_update()

    # Main update loop
    update_count = 0
    last_update_time = time.time()

    while not _should_stop.is_set():
        # Calculate time until next update
        time_since_last_update = time.time() - last_update_time
        time_until_next_update = UPDATE_INTERVAL_SECONDS - time_since_last_update

        if time_until_next_update > 0:
            # Sleep in small intervals to check stop flag frequently
            sleep_interval = min(10, time_until_next_update)
            if _should_stop.wait(timeout=sleep_interval):
                break  # Stop flag was set
            continue

        # Time for an update
        update_count += 1
        _perform_update()
        last_update_time = time.time()

        # Log periodic status
        if update_count % 10 == 0:  # Every 10 updates (5 hours)
            logger.info(
                f"📊 Updater status: {update_count} updates completed, "
                f"running for {time.time() / 3600:.1f} hours"
            )

    logger.info("=" * 80)
    logger.info("🛑 Metagraph Data Updater Thread Stopped")
    logger.info(f"   - Total updates performed: {update_count}")
    logger.info("=" * 80)
    _is_running = False


def _perform_update() -> bool:
    """
    Perform a single metagraph data update.

    Returns:
        True if update succeeded, False otherwise
    """
    try:
        logger.info(f"🔄 Starting metagraph update at {datetime.now().isoformat()}")

        start_time = time.time()
        refresh_metagraph_data()
        elapsed = time.time() - start_time

        # Get status after update
        status = get_update_status()
        validator_count = status.get("validator_count", 0)
        vtrust_source = status.get("vtrust_source", "unknown")

        logger.info(
            f"✅ Update completed in {elapsed:.2f}s - "
            f"{validator_count} validators (vTrust: {vtrust_source})"
        )
        return True

    except MetagraphError as exc:
        logger.error(f"❌ Metagraph update failed: {exc}")
        return False
    except Exception as exc:
        logger.error(f"❌ Unexpected error during update: {exc}", exc_info=True)
        return False


def start_metagraph_updater():
    """
    Start the metagraph updater background thread.

    This should be called during application startup.
    If the thread is already running, this is a no-op.
    """
    global _updater_thread, _should_stop

    if _updater_thread is not None and _updater_thread.is_alive():
        logger.warning("⚠️  Metagraph updater thread is already running")
        return

    # Reset stop flag
    _should_stop.clear()

    # Start the thread
    _updater_thread = threading.Thread(
        target=_updater_worker,
        name="MetagraphUpdater",
        daemon=True,  # Thread will exit when main process exits
    )
    _updater_thread.start()

    logger.info("✅ Metagraph updater thread started")


def stop_metagraph_updater(timeout: float = 10.0):
    """
    Stop the metagraph updater background thread gracefully.

    This should be called during application shutdown.

    Args:
        timeout: Maximum time to wait for thread to stop (seconds)
    """
    global _updater_thread

    if _updater_thread is None or not _updater_thread.is_alive():
        logger.info("ℹ️  Metagraph updater thread is not running")
        return

    logger.info("🛑 Stopping metagraph updater thread...")

    # Signal thread to stop
    _should_stop.set()

    # Wait for thread to finish
    _updater_thread.join(timeout=timeout)

    if _updater_thread.is_alive():
        logger.warning(
            f"⚠️  Metagraph updater thread did not stop within {timeout}s timeout"
        )
    else:
        logger.info("✅ Metagraph updater thread stopped gracefully")

    _updater_thread = None


def is_updater_running() -> bool:
    """
    Check if the metagraph updater thread is currently running.

    Returns:
        True if running, False otherwise
    """
    return _is_running


def get_updater_status() -> dict:
    """
    Get the current status of the metagraph updater thread.

    Returns:
        Status dictionary with running state and last update info
    """
    return {
        "running": is_updater_running(),
        "thread_alive": _updater_thread is not None and _updater_thread.is_alive(),
        "last_update": get_last_update_time(),
        "update_status": get_update_status(),
    }

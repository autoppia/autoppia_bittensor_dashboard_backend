from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.config import settings
from app.services.redis_cache import redis_cache

_logger = logging.getLogger(__name__)

# Redis keys
REDIS_KEY_CURRENT_BLOCK = "chain:current_block"
REDIS_KEY_BLOCK_TIMESTAMP = "chain:block_timestamp"

# Background refresher state
_refresh_thread: Optional[threading.Thread] = None
_refresh_stop: Optional[threading.Event] = None


def _fetch_current_block() -> Optional[int]:
    """Fetch the current chain block from bittensor.

    Tries `subtensor.get_current_block()` first; if unavailable, falls back to
    the metagraph's `block` field. Returns None if bittensor is not available
    or if both calls fail.
    """
    try:
        import bittensor as bt  # type: ignore
    except Exception as exc:
        _logger.warning("Bittensor library not available: %s", exc)
        return None

    kwargs = {}
    if settings.SUBTENSOR_NETWORK:
        # Bittensor accepts URLs directly as the network parameter
        # This works for both URLs (ws://...) and network names (finney, testnet)
        network_value = settings.SUBTENSOR_NETWORK.strip()
        kwargs["network"] = network_value
        _logger.debug("Connecting to Subtensor with network: %s", network_value)

    try:
        subtensor = bt.subtensor(**kwargs)  # type: ignore[attr-defined]
        try:
            # Preferred path
            block = int(subtensor.get_current_block())
            _logger.debug("Fetched current block from subtensor: %s", block)
            return block
        except Exception as exc:
            _logger.warning(
                "subtensor.get_current_block() failed: %s, trying metagraph fallback",
                exc,
            )
            # Fallback path: heavier call
            try:
                mg = subtensor.metagraph(settings.VALIDATOR_NETUID)
                block = int(getattr(mg, "block", 0) or 0)
                _logger.debug("Fetched current block from metagraph: %s", block)
                return block if block > 0 else None
            except Exception as fallback_exc:
                _logger.error(
                    "Both subtensor.get_current_block() and metagraph fallback failed: %s",
                    fallback_exc,
                )
                return None
    except Exception as exc:
        _logger.error("Failed to create subtensor connection: %s", exc)
        return None


def get_current_block_estimate() -> Optional[int]:
    """Fast, non-blocking best-effort current block estimate.

    Reads from Redis and estimates based on 12-second block time.
    Never triggers network fetch - only reads cached data.

    Returns:
        Estimated current block or None if no cached data
    """
    try:
        # Get cached block and timestamp from Redis
        base_block = redis_cache.get(REDIS_KEY_CURRENT_BLOCK)
        base_ts = redis_cache.get(REDIS_KEY_BLOCK_TIMESTAMP)

        if base_block is None or base_ts is None:
            return None

        # Estimate current block based on elapsed time
        block_time = int(getattr(settings, "CHAIN_BLOCK_TIME_SECONDS", 12) or 12)
        now = time.time()
        elapsed = max(0.0, now - base_ts)
        estimated_block = int(base_block) + int(elapsed // block_time)

        return estimated_block

    except Exception:
        return None


def get_current_block() -> Optional[int]:
    """Get current block estimate from Redis (never calls subtensor).

    This is just an alias for get_current_block_estimate() now.
    The background thread updates Redis, GET endpoints only read.

    Returns:
        Estimated current block from Redis or None
    """
    return get_current_block_estimate()


def refresh_block_now() -> Optional[int]:
    """Fetch current block from subtensor and store in Redis.

    Called by background thread only.

    Returns:
        Fetched block or None on failure
    """
    fresh = _fetch_current_block()
    if fresh is not None:
        try:
            # Store in Redis with 30-minute TTL
            redis_cache.set(REDIS_KEY_CURRENT_BLOCK, int(fresh), ttl=1800)
            redis_cache.set(REDIS_KEY_BLOCK_TIMESTAMP, time.time(), ttl=1800)
            _logger.info(f"✅ Current block updated in Redis: {fresh}")
            return int(fresh)
        except Exception as exc:
            _logger.error(f"Failed to store block in Redis: {exc}")
    return None


def start_block_refresher(period_seconds: Optional[int] = None) -> None:
    """Start a background thread that refreshes the block cache on a fixed cadence.

    - Never blocks request/response paths
    - Swallows errors and keeps looping
    """
    global _refresh_thread, _refresh_stop
    if _refresh_thread and _refresh_thread.is_alive():
        return
    if period_seconds is None:
        try:
            period_seconds = int(
                getattr(settings, "CHAIN_BLOCK_REFRESH_PERIOD", 30) or 30
            )
        except Exception:
            period_seconds = 30
    period_seconds = max(5, int(period_seconds))

    _refresh_stop = threading.Event()

    def _worker():
        while _refresh_stop and not _refresh_stop.is_set():
            try:
                refresh_block_now()
            except Exception as exc:
                # Log failures but keep thread alive
                _logger.error(
                    "Background block refresh failed: %s - will retry in %ss",
                    exc,
                    period_seconds,
                    exc_info=False,
                )
            # Sleep in small intervals to allow quick shutdown
            for _ in range(period_seconds):
                if _refresh_stop and _refresh_stop.is_set():
                    break
                time.sleep(1)

    _refresh_thread = threading.Thread(target=_worker, daemon=True)
    _refresh_thread.start()


def stop_block_refresher() -> None:
    global _refresh_stop, _refresh_thread
    if _refresh_stop is not None:
        _refresh_stop.set()
    if _refresh_thread and _refresh_thread.is_alive():
        _refresh_thread.join(timeout=2)
    _refresh_thread = None
    _refresh_stop = None

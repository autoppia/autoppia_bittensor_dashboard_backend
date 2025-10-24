from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.config import settings

_logger = logging.getLogger(__name__)
_cache_lock = threading.Lock()
_cached_block: Optional[int] = None
_cached_at: float = 0.0
_fetch_in_progress = False
_last_fetch_attempt: float = 0.0
_FAILURE_RETRY_SECONDS = 30.0


def _fetch_current_block() -> Optional[int]:
    """Fetch the current chain block from bittensor.

    Tries `subtensor.get_current_block()` first; if unavailable, falls back to
    the metagraph's `block` field. Returns None if bittensor is not available
    or if both calls fail.
    """
    try:
        import bittensor as bt  # type: ignore
    except Exception:
        return None

    kwargs = {}
    if settings.SUBTENSOR_NETWORK:
        kwargs["network"] = settings.SUBTENSOR_NETWORK

    try:
        subtensor = bt.subtensor(**kwargs)  # type: ignore[attr-defined]
        try:
            # Preferred path
            return int(subtensor.get_current_block())
        except Exception:
            # Fallback path: heavier call
            try:
                mg = subtensor.metagraph(settings.VALIDATOR_NETUID)
                return int(getattr(mg, "block", 0) or 0)
            except Exception:
                return None
    except Exception:
        return None


def get_current_block() -> Optional[int]:
    """Get current block height with 15-min cached fetch + time-based estimate.

    - On first call (or after TTL expiry), fetch from bittensor and cache the
      block height and timestamp.
    - Between fetches, return an estimated height by adding one block per
      `settings.CHAIN_BLOCK_TIME_SECONDS` elapsed since the last fetch.
    - When TTL expires, attempt a background refresh; if it fails, keep serving
      the estimated value.
    """
    global _cached_block, _cached_at, _fetch_in_progress, _last_fetch_attempt
    ttl = int(getattr(settings, "CHAIN_BLOCK_CACHE_TTL_SECONDS", 900) or 900)
    block_time = int(getattr(settings, "CHAIN_BLOCK_TIME_SECONDS", 12) or 12)
    now = time.time()

    # Fast path: compute estimate from cached value (if any)
    with _cache_lock:
        base_block = _cached_block
        base_ts = _cached_at
        in_progress = _fetch_in_progress
        last_attempt = _last_fetch_attempt

    estimate: Optional[int] = None
    if base_block is not None and base_ts > 0:
        elapsed = max(0.0, now - base_ts)
        estimate = base_block + int(elapsed // block_time)

    ttl_expired = base_block is not None and (now - base_ts) >= ttl
    retry_delay = min(ttl, _FAILURE_RETRY_SECONDS) if ttl > 0 else _FAILURE_RETRY_SECONDS
    should_retry_failure = base_block is None and (now - last_attempt) >= retry_delay
    need_refresh = ttl_expired or should_retry_failure

    if not need_refresh:
        return estimate

    if in_progress:
        return estimate

    with _cache_lock:
        if _fetch_in_progress:
            return estimate
        # Double-check conditions with the freshest state
        base_block = _cached_block
        base_ts = _cached_at
        last_attempt = _last_fetch_attempt
        ttl_expired = base_block is not None and (now - base_ts) >= ttl
        should_retry_failure = base_block is None and (now - last_attempt) >= retry_delay
        if not ttl_expired and not should_retry_failure:
            return estimate
        reason = "ttl_expired" if ttl_expired else "retry_failure"
        _fetch_in_progress = True
        _last_fetch_attempt = now

    if _logger.isEnabledFor(logging.INFO):
        _logger.info(
            "Refreshing chain block from Subtensor (reason=%s, estimate=%s)",
            reason,
            estimate,
        )

    # Refresh path: attempt to fetch a fresh value
    started_at = time.time()
    fresh = _fetch_current_block()
    elapsed = time.time() - started_at

    with _cache_lock:
        _fetch_in_progress = False
        if fresh is not None:
            _cached_block = fresh
            _cached_at = time.time()
            if _logger.isEnabledFor(logging.INFO):
                _logger.info("Chain block refreshed to %s in %.2fs", fresh, elapsed)
            return fresh
        # Failed refresh: keep previous estimate but avoid hammering the chain
        if _cached_block is None:
            # ensure we have a non-zero timestamp so subsequent retries respect backoff
            _cached_at = time.time()
        if _logger.isEnabledFor(logging.WARNING):
            _logger.warning("Failed to refresh chain block (duration=%.2fs)", elapsed)

    # Fetch failed: keep serving estimate if we have one
    return estimate

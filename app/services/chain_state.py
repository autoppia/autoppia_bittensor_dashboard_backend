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
    if settings.SUBTENSOR_ENDPOINT:
        kwargs["chain_endpoint"] = settings.SUBTENSOR_ENDPOINT

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
    global _cached_block, _cached_at
    ttl = int(getattr(settings, "CHAIN_BLOCK_CACHE_TTL_SECONDS", 900) or 900)
    block_time = int(getattr(settings, "CHAIN_BLOCK_TIME_SECONDS", 12) or 12)
    now = time.time()

    # Fast path: compute estimate from cached value (if any)
    with _cache_lock:
        base_block = _cached_block
        base_ts = _cached_at

    estimate: Optional[int] = None
    if base_block is not None and base_ts > 0:
        elapsed = max(0.0, now - base_ts)
        estimate = base_block + int(elapsed // block_time)

    need_refresh = base_block is None or (now - base_ts) >= ttl
    if not need_refresh:
        return estimate

    # Refresh path: attempt to fetch a fresh value
    fresh = _fetch_current_block()
    if fresh is not None:
        with _cache_lock:
            _cached_block = fresh
            _cached_at = time.time()
        # New estimate from fresh base (usually identical to fresh)
        elapsed = 0.0
        return fresh + int(elapsed // block_time)

    # Fetch failed: keep serving estimate if we have one
    return estimate

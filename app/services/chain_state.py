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


def _estimate_from_cache(now: float, block_time: int) -> Optional[int]:
    """Compute a best-effort block estimate from cached value without side effects."""
    with _cache_lock:
        base_block = _cached_block
        base_ts = _cached_at
    if base_block is None or base_ts <= 0:
        return None
    elapsed = max(0.0, now - base_ts)
    return base_block + int(elapsed // block_time)


def get_current_block_estimate() -> Optional[int]:
    """Fast, non-blocking best-effort current block estimate.

    - Never triggers a network fetch or mutates cache state.
    - Returns None if no prior cached value exists.
    """
    block_time = int(getattr(settings, "CHAIN_BLOCK_TIME_SECONDS", 12) or 12)
    now = time.time()
    return _estimate_from_cache(now, block_time)


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
    # Enforce a minimum 10-minute cache to avoid frequent chain access
    ttl = max(ttl, 600)
    block_time = int(getattr(settings, "CHAIN_BLOCK_TIME_SECONDS", 12) or 12)
    now = time.time()

    # Fast path: compute estimate from cached value (if any)
    with _cache_lock:
        base_block = _cached_block
        base_ts = _cached_at
        in_progress = _fetch_in_progress
        last_attempt = _last_fetch_attempt

    estimate: Optional[int] = _estimate_from_cache(now, block_time)

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


def refresh_block_now() -> Optional[int]:
    """Force a refresh of the cached current block immediately.

    Returns the fetched block or None on failure. This bypasses TTL/backoff
    and updates the in-memory cache if a value is retrieved.
    """
    global _cached_block, _cached_at
    fresh = _fetch_current_block()
    if fresh is not None:
        with _cache_lock:
            _cached_block = int(fresh)
            _cached_at = time.time()
        return int(fresh)
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
            period_seconds = int(getattr(settings, "CHAIN_BLOCK_REFRESH_PERIOD", 30) or 30)
        except Exception:
            period_seconds = 30
    period_seconds = max(5, int(period_seconds))

    _refresh_stop = threading.Event()

    def _worker():
        while _refresh_stop and not _refresh_stop.is_set():
            try:
                refresh_block_now()
            except Exception:
                pass
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

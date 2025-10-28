from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_price_cache_lock = threading.Lock()
_cached_price_value: Optional[float] = None
_cached_price_netuid: Optional[int] = None
_cached_price_at: float = 0.0


def _env_fallback(netuid: int) -> float:
    """Resolve fallback subnet price from environment settings.

    Priority order:
    1) SUBNET_<NETUID>_PRICE (e.g., SUBNET_36_PRICE)
    2) SUBNET_PRICE_FALLBACK
    3) ALPHA_TO_TAO_RATE
    """
    # Per-netuid price (e.g., SUBNET_36_PRICE)
    specific_key = f"SUBNET_{int(netuid)}_PRICE"
    value = getattr(settings, specific_key, None)
    try:
        if value is not None:
            return float(value)
    except (TypeError, ValueError):
        pass

    # Generic fallback
    try:
        v = float(getattr(settings, "SUBNET_PRICE_FALLBACK", 0.0) or 0.0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass

    # Back-compat: use ALPHA_TO_TAO_RATE
    try:
        v = float(getattr(settings, "ALPHA_TO_TAO_RATE", 1.0) or 1.0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass

    return 1.0


def _try_fetch_price_sync(netuid: int) -> Optional[float]:
    """Best-effort subnet price fetch using bittensor (sync path).

    Tries several likely API shapes and falls back gracefully. Returns None on
    failure so caller can use env-based fallback.
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
    except Exception:
        return None

    # Candidate call patterns across bittensor versions
    # 1) get_subnet_hyperparameters(netuid) → may include fields resembling price
    try:
        hp = getattr(subtensor, "get_subnet_hyperparameters", None)
        if callable(hp):
            data = hp(int(netuid))
            # Accept both dict-like and object-like
            candidates = [
                "price",
                "alpha_to_tao_rate",
                "alpha_price",
                "tau_price",
            ]
            for key in candidates:
                try:
                    if isinstance(data, dict) and key in data:
                        val = float(data[key])
                        if val > 0:
                            return val
                    else:
                        val = float(getattr(data, key))  # type: ignore[arg-type]
                        if val > 0:
                            return val
                except Exception:
                    continue
    except Exception:
        pass

    # 2) get_subnet_price(netuid)
    try:
        fn = getattr(subtensor, "get_subnet_price", None)
        if callable(fn):
            val = float(fn(int(netuid)))
            if val > 0:
                return val
    except Exception:
        pass

    # 3) Inspect metagraph for any price-like attribute (very defensive)
    try:
        mg = subtensor.metagraph(int(netuid))
        for key in ("price", "alpha_to_tao_rate", "alpha_price", "tau_price"):
            try:
                val = float(getattr(mg, key))
                if val > 0:
                    return val
            except Exception:
                continue
    except Exception:
        pass

    return None


def get_price(netuid: int = 36, ttl_seconds: int = 300) -> float:
    """Return subnet price for `netuid` with caching and env fallback.

    - Tries bittensor (sync) first.
    - Caches the last successful value for `ttl_seconds`.
    - Falls back to environment when chain fetch fails.
    """
    now = time.time()

    # Serve from cache if valid and matching netuid
    with _price_cache_lock:
        if (
            _cached_price_value is not None
            and _cached_price_netuid == int(netuid)
            and (now - _cached_price_at) < max(60, int(ttl_seconds))
        ):
            return float(_cached_price_value)

    # Try chain
    value = _try_fetch_price_sync(int(netuid))
    if value is None or value <= 0:
        # Prefer stale cache if we have one
        with _price_cache_lock:
            if _cached_price_value is not None and _cached_price_netuid == int(netuid):
                logger.warning(
                    "Using stale cached subnet price for netuid=%s (fetch failed)",
                    netuid,
                )
                return float(_cached_price_value)
        # No cache: fallback to env
        value = _env_fallback(int(netuid))

    # Update cache
    with _price_cache_lock:
        global _cached_price_value, _cached_price_at, _cached_price_netuid
        _cached_price_value = float(value)
        _cached_price_netuid = int(netuid)
        _cached_price_at = now
    return float(value)


async def get_price_async(netuid: int = 36, ttl_seconds: int = 300) -> float:
    """Async variant using AsyncSubtensor when available, with the same fallback rules.

    If AsyncSubtensor is unavailable or fails, falls back to the sync implementation.
    """
    # Try to use AsyncSubtensor first
    try:
        import bittensor as bt  # type: ignore
        AsyncSubtensor = getattr(bt, "AsyncSubtensor", None)
    except Exception:
        AsyncSubtensor = None

    if AsyncSubtensor is not None:
        kwargs = {}
        if settings.SUBTENSOR_NETWORK:
            kwargs["network"] = settings.SUBTENSOR_NETWORK
        try:
            st = AsyncSubtensor(**kwargs)
            # Try candidate async methods
            for method_name in ("get_subnet_price", "get_subnet_hyperparameters"):
                fn = getattr(st, method_name, None)
                if fn is None:
                    continue
                try:
                    data = await fn(int(netuid))
                    if method_name == "get_subnet_price":
                        val = float(data)
                        if val > 0:
                            with _price_cache_lock:
                                global _cached_price_value, _cached_price_netuid, _cached_price_at
                                _cached_price_value = val
                                _cached_price_netuid = int(netuid)
                                _cached_price_at = time.time()
                            return val
                    else:
                        for key in ("price", "alpha_to_tao_rate", "alpha_price", "tau_price"):
                            try:
                                if isinstance(data, dict) and key in data:
                                    val = float(data[key])
                                else:
                                    val = float(getattr(data, key))
                                if val > 0:
                                    with _price_cache_lock:
                                        global _cached_price_value, _cached_price_netuid, _cached_price_at
                                        _cached_price_value = val
                                        _cached_price_netuid = int(netuid)
                                        _cached_price_at = time.time()
                                    return val
                            except Exception:
                                continue
                except Exception:
                    continue
        except Exception:
            pass

    # Fallback to sync path
    return get_price(netuid=netuid, ttl_seconds=ttl_seconds)

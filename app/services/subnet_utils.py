from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_MAX_REASONABLE_PRICE = 0.05  # 1 alpha -> at most 0.05 τ unless configured via env


def _env_fallback(netuid: int) -> float:
    """Resolve fallback subnet price from environment settings.

    Priority order:
    1) SUBNET_<NETUID>_PRICE (e.g., SUBNET_36_PRICE)
    2) SUBNET_PRICE_FALLBACK
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
        # Bittensor accepts URLs directly as the network parameter
        # This works for both URLs (ws://...) and network names (finney, testnet)
        network_value = settings.SUBTENSOR_NETWORK.strip()
        kwargs["network"] = network_value

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
    """Get subnet price from Redis (managed by background thread).

    GET endpoints should use this - it never calls the blockchain.
    Falls back to env if Redis has no data.

    Returns:
        Cached price from Redis or env fallback
    """
    # Try to get from Redis (populated by background thread)
    try:
        from app.services.metagraph_updater_thread import get_price_from_redis

        price = get_price_from_redis()
        if price is not None and price > 0:
            return float(price)
    except Exception:
        pass

    # Fallback to env
    return _env_fallback(int(netuid))


async def get_price_async(netuid: int = 36, ttl_seconds: int = 300) -> float:
    """Async variant - just returns sync version (reads from Redis).

    No async needed since we only read from Redis now.
    """
    return get_price(netuid=netuid, ttl_seconds=ttl_seconds)


def get_price_cached(netuid: int = 36, ttl_seconds: int = 300) -> float:
    """Alias for get_price() - now they both only read from Redis.

    Safe for GET endpoints - never calls blockchain.
    """
    return get_price(netuid=netuid, ttl_seconds=ttl_seconds)

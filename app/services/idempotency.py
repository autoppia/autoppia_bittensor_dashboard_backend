import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory cache for idempotency keys
_cache: dict[str, float] = {}


def get_cache_stats() -> dict:
    """Get statistics about the idempotency cache."""
    now = time.time()
    active_keys = sum(1 for ts in _cache.values() if now - ts <= settings.IDEMPOTENCY_TTL)
    expired_keys = len(_cache) - active_keys

    return {
        "total_keys": len(_cache),
        "active_keys": active_keys,
        "expired_keys": expired_keys,
        "ttl_seconds": settings.IDEMPOTENCY_TTL,
    }

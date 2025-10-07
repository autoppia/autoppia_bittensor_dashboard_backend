import time
from typing import Optional
from fastapi import Header, Request
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# In-memory cache for idempotency keys
_cache: dict[str, float] = {}


async def idempotency_guard(
    request: Request, 
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")
) -> Optional[str]:
    """
    Simple in-memory idempotency guard.
    
    For production use, consider implementing persistent storage with MongoDB
    and TTL indexes for better reliability across restarts.
    """
    now = time.time()
    
    # Clean up expired keys
    expired_keys = [k for k, ts in _cache.items() if now - ts > settings.IDEMPOTENCY_TTL]
    for key in expired_keys:
        _cache.pop(key, None)
    
    if expired_keys:
        logger.debug(f"Cleaned up {len(expired_keys)} expired idempotency keys")
    
    if idempotency_key is None:
        return None
    
    # Check if key already exists
    if idempotency_key in _cache:
        logger.info(f"Idempotency key already processed: {idempotency_key[:8]}...")
        return idempotency_key
    
    # Store the key with current timestamp
    _cache[idempotency_key] = now
    logger.debug(f"Stored new idempotency key: {idempotency_key[:8]}...")
    return idempotency_key


def get_cache_stats() -> dict:
    """Get statistics about the idempotency cache."""
    now = time.time()
    active_keys = sum(1 for ts in _cache.values() if now - ts <= settings.IDEMPOTENCY_TTL)
    expired_keys = len(_cache) - active_keys
    
    return {
        "total_keys": len(_cache),
        "active_keys": active_keys,
        "expired_keys": expired_keys,
        "ttl_seconds": settings.IDEMPOTENCY_TTL
    }

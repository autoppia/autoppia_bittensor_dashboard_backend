"""
Caching service for API endpoints to improve performance and reduce redundant calls.
"""

import time
import hashlib
import json
from typing import Any, Callable, Optional, Dict
from functools import wraps
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class APICache:
    """In-memory cache for API responses with TTL support."""

    def __init__(self, max_size: int = 100):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._default_ttl = 300  # 5 minutes default
        self._disabled = False  # Cache can be disabled for testing
        self._max_size = max_size  # Maximum number of entries (prevents memory leak)
        self._stats = {"hits": 0, "misses": 0, "sets": 0, "evictions": 0}

    def _generate_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate a cache key from function arguments."""
        # Create a hash of the arguments
        key_data = {"args": args, "kwargs": sorted(kwargs.items()) if kwargs else {}}
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        key_hash = hashlib.md5(key_str.encode()).hexdigest()
        return f"{prefix}:{key_hash}"

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """Check if a cache entry has expired."""
        return time.time() > entry["expires_at"]

    def _cleanup_expired(self):
        """Remove expired entries from cache."""
        expired_keys = []
        for key, entry in self._cache.items():
            if self._is_expired(entry):
                expired_keys.append(key)

        for key in expired_keys:
            del self._cache[key]
            self._stats["evictions"] += 1

        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")

    def get(self, key: str, *, force: bool = False) -> Optional[Any]:
        """Get value from cache if not expired."""
        if self._disabled and not force:
            self._stats["misses"] += 1
            logger.debug(f"Cache disabled - miss for key: {key}")
            return None

        self._cleanup_expired()

        if key not in self._cache:
            self._stats["misses"] += 1
            return None

        entry = self._cache[key]
        if self._is_expired(entry):
            del self._cache[key]
            self._stats["evictions"] += 1
            self._stats["misses"] += 1
            return None

        self._stats["hits"] += 1
        return entry["data"]

    def set(
        self, key: str, value: Any, ttl: Optional[int] = None, *, force: bool = False
    ) -> None:
        """Set value in cache with TTL."""
        if ttl is None:
            ttl = self._default_ttl

        try:
            ttl_value = int(ttl)
        except (TypeError, ValueError):
            ttl_value = self._default_ttl

        ttl_value = max(ttl_value, 0)

        if (self._disabled and not force) or (ttl_value == 0 and not force):
            logger.debug(
                "Skipping cache set for key %s (disabled=%s, ttl=%s)",
                key,
                self._disabled,
                ttl_value,
            )
            return

        now = time.time()
        
        # Check if cache is full and evict oldest entries (LRU)
        if len(self._cache) >= self._max_size:
            # Sort by created_at and remove oldest 20%
            entries_by_age = sorted(
                self._cache.items(),
                key=lambda x: x[1].get("created_at", 0)
            )
            num_to_remove = max(int(self._max_size * 0.2), 1)
            for old_key, _ in entries_by_age[:num_to_remove]:
                del self._cache[old_key]
                self._stats["evictions"] += 1
            logger.info(f"Cache size limit reached, evicted {num_to_remove} oldest entries")
        
        self._cache[key] = {
            "data": value,
            "expires_at": now + ttl_value,
            "created_at": now,
        }
        self._stats["sets"] += 1

    def clear(self, pattern: Optional[str] = None) -> int:
        """Clear cache entries, optionally matching a pattern."""
        if pattern is None:
            cleared = len(self._cache)
            self._cache.clear()
        else:
            keys_to_remove = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_remove:
                del self._cache[key]
            cleared = len(keys_to_remove)

        logger.info(f"Cleared {cleared} cache entries")
        return cleared

    def disable(self) -> None:
        """Disable cache for testing purposes."""
        self._disabled = True
        logger.info("Cache disabled for testing")

    def enable(self) -> None:
        """Enable cache."""
        self._disabled = False
        logger.info("Cache enabled")

    def is_disabled(self) -> bool:
        """Check if cache is disabled."""
        return self._disabled

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (
            (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        )

        return {
            **self._stats,
            "total_requests": total_requests,
            "hit_rate_percent": round(hit_rate, 2),
            "cache_size": len(self._cache),
            "active_entries": len(
                [e for e in self._cache.values() if not self._is_expired(e)]
            ),
            "disabled": self._disabled,
        }

    def set_default_ttl(self, ttl: int) -> None:
        """Set the default TTL for the cache."""
        try:
            ttl_value = int(ttl)
        except (TypeError, ValueError):
            ttl_value = 0
        self._default_ttl = max(ttl_value, 0)


# Global cache instance with size limit to prevent memory leaks
# Max 100 entries = ~500MB max (assuming 5MB per large response)
api_cache = APICache(max_size=100)


def cached(prefix: str, ttl: Optional[int] = None, skip_cache: bool = False):
    """
    Decorator to cache function results.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds (default: 5 minutes)
        skip_cache: If True, skip caching (useful for testing)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if skip_cache:
                return await func(*args, **kwargs)

            # Generate cache key
            cache_key = api_cache._generate_key(prefix, *args, **kwargs)

            # Try to get from cache
            cached_result = api_cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {prefix}")
                return cached_result

            # Execute function and cache result
            logger.debug(f"Cache miss for {prefix}, executing function")
            result = await func(*args, **kwargs)
            api_cache.set(cache_key, result, ttl)

            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if skip_cache:
                return func(*args, **kwargs)

            # Generate cache key
            cache_key = api_cache._generate_key(prefix, *args, **kwargs)

            # Try to get from cache
            cached_result = api_cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {prefix}")
                return cached_result

            # Execute function and cache result
            logger.debug(f"Cache miss for {prefix}, executing function")
            result = func(*args, **kwargs)
            api_cache.set(cache_key, result, ttl)

            return result

        # Return appropriate wrapper based on function type
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Cache TTL constants
CACHE_TTL = {
    "overview_metrics": 300,  # 5 minutes - overview data changes slowly
    "validators_list": 600,  # 10 minutes - validator list rarely changes
    "rounds_list": 180,  # 3 minutes - rounds list changes occasionally
    "round_detail": 300,  # 5 minutes - round details are static once created
    "round_miners": 120,  # 2 minutes - miner data changes more frequently
    "round_validators": 300,  # 5 minutes - validator data per round
    "round_statistics": 180,  # 3 minutes - statistics change moderately
    "round_detail_final": 86400,  # 24 hours - finalised rounds are immutable
    "round_miners_final": 86400,  # 24 hours - finalised miner stats are immutable
    "round_validators_final": 86400,  # 24 hours - finalised validator stats are immutable
    "round_statistics_final": 86400,  # 24 hours - finalised round statistics are immutable
    "current_round": 60,  # 1 minute - current round changes more frequently
    "network_status": 120,  # 2 minutes - network status changes moderately
    "leaderboard": 300,  # 5 minutes - leaderboard data changes slowly
    "agents_list": 600,  # 10 minutes - agents list rarely changes
    "miner_list": 180,  # 3 minutes - miner list changes moderately
    "miner_detail": 300,  # 5 minutes - individual miner details change slowly
    # Agent runs cache TTLs
    "agent_run_detail": 60,  # 1 minute - agent run details change frequently
    "agent_run_personas": 300,  # 5 minutes - personas data changes slowly
    "agent_run_stats": 120,  # 2 minutes - statistics change moderately
    "agent_run_summary": 60,  # 1 minute - summary changes frequently
    "agent_run_tasks": 30,  # 30 seconds - tasks data changes frequently
    "agent_runs_by_agent": 60,  # 1 minute - agent runs list changes moderately
    "agent_runs_by_round": 60,  # 1 minute - agent runs list changes moderately
    "agent_runs_by_validator": 60,  # 1 minute - agent runs list changes moderately
    "agent_run_timeline": 0,  # No caching - timeline is real-time
    "agent_run_logs": 0,  # No caching - logs are real-time
    "agent_run_metrics": 30,  # 30 seconds - metrics change frequently
}

# Cache is now controlled by REDIS_ENABLED in settings
# In-memory cache is only used as fallback when Redis is unavailable

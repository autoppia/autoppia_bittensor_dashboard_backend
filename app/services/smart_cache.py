"""
Smart caching helpers for UI endpoints.
Automatically determines appropriate TTL based on data mutability.
"""

import logging
from typing import Any, Callable, Optional
from functools import wraps

from app.services.redis_cache import redis_cache, REDIS_CACHE_TTL
from app.services.chain_state import get_current_block_estimate
from app.services.round_calc import compute_round_number, compute_boundaries_for_round
from app.config import settings

logger = logging.getLogger(__name__)


async def get_current_round_number() -> int:
    """Get the current round number based on chain state."""
    current_block = await get_current_block_estimate()
    return compute_round_number(current_block)


async def is_round_completed(round_number: int) -> bool:
    """
    Check if a round is completed (past round).

    Args:
        round_number: The round number to check

    Returns:
        True if the round is completed, False if it's current or future
    """
    current_block = await get_current_block_estimate()
    current_round = compute_round_number(current_block)
    return round_number < current_round


async def get_smart_ttl_for_round(round_number: int) -> int:
    """
    Calculate appropriate TTL for round data based on completion status.

    - Completed rounds (immutable): 7 days (604,800 seconds)
    - Current round (active): 30 seconds
    - Future rounds: 5 minutes

    Args:
        round_number: The round number

    Returns:
        TTL in seconds
    """
    current_round = await get_current_round_number()

    if round_number < current_round:
        # Completed round - immutable data, cache for 7 days
        return settings.REDIS_FINAL_DATA_TTL
    elif round_number == current_round:
        # Current round - data is changing, cache for 30 seconds
        return 30
    else:
        # Future round - shouldn't have much data, cache for 5 minutes
        return 300


async def cached_round_data(
    round_number: int,
    cache_key_prefix: str,
    fetch_func: Callable,
    force_refresh: bool = False,
) -> Any:
    """
    Smart caching for round-related data.

    Automatically determines appropriate TTL based on round completion status.
    Use this for any endpoint that returns round-specific data.

    Args:
        round_number: The round number
        cache_key_prefix: Prefix for the cache key (e.g., "round_detail")
        fetch_func: Async function that fetches the data from DB
        force_refresh: If True, skip cache and fetch fresh data

    Returns:
        The cached or freshly fetched data

    Example:
        async def get_round_detail(round_number: int):
            return await cached_round_data(
                round_number=round_number,
                cache_key_prefix="round_detail",
                fetch_func=lambda: fetch_from_db(round_number)
            )
    """
    cache_key = f"{cache_key_prefix}:{round_number}"

    # Force refresh if requested
    if force_refresh:
        logger.info(f"Force refresh requested for {cache_key}")
        result = await fetch_func()
        if result is not None:
            ttl = await get_smart_ttl_for_round(round_number)
            redis_cache.set(cache_key, result, ttl)
        return result

    # Try to get from cache
    cached_result = redis_cache.get(cache_key)
    if cached_result is not None:
        is_completed = await is_round_completed(round_number)
        cache_type = "final" if is_completed else "active"
        logger.debug(f"Cache hit for {cache_key} (type: {cache_type})")
        return cached_result

    # Fetch from DB
    logger.debug(f"Cache miss for {cache_key}, fetching from DB")
    result = await fetch_func()

    # Cache the result
    if result is not None:
        ttl = await get_smart_ttl_for_round(round_number)
        is_completed = await is_round_completed(round_number)
        cache_type = "final (7 days)" if is_completed else "active (30s)"
        logger.info(f"Caching {cache_key} with TTL {ttl}s (type: {cache_type})")
        redis_cache.set(cache_key, result, ttl)

    return result


def cached_static_data(
    cache_key: str,
    ttl: int = 300,
):
    """
    Decorator for caching static data (lists, aggregations, etc.).

    Use this for endpoints that don't depend on round completion status.

    Args:
        cache_key: Cache key (should be descriptive)
        ttl: Time to live in seconds (default: 5 minutes)

    Example:
        @cached_static_data("agents_list", ttl=600)
        async def get_agents_list():
            return await fetch_agents_from_db()
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key with args/kwargs
            full_key = redis_cache._generate_key(cache_key, *args, **kwargs)

            # Try to get from cache
            cached_result = redis_cache.get(full_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {full_key}")
                return cached_result

            # Execute function
            logger.debug(f"Cache miss for {full_key}, executing function")
            result = await func(*args, **kwargs)

            # Cache result
            if result is not None:
                redis_cache.set(full_key, result, ttl)

            return result

        return wrapper

    return decorator


async def invalidate_round_cache(round_number: int) -> None:
    """
    Invalidate all cache entries for a specific round.

    Useful when round data is updated (e.g., manual corrections).

    Args:
        round_number: The round number to invalidate
    """
    patterns = [
        f"round_detail:{round_number}",
        f"round_tasks:{round_number}",
        f"round_miners:{round_number}",
        f"round_validators:{round_number}",
        f"round_statistics:{round_number}",
    ]

    for pattern in patterns:
        redis_cache.delete(pattern)
        logger.info(f"Invalidated cache: {pattern}")

    # Also invalidate wildcard patterns
    redis_cache.clear_pattern(f"*round*{round_number}*")


async def get_cache_info() -> dict:
    """
    Get information about cache status and statistics.

    Useful for debugging and monitoring.
    """
    current_round = await get_current_round_number()
    stats = redis_cache.get_stats()

    return {
        "current_round": current_round,
        "redis_available": redis_cache.is_available(),
        "redis_enabled": redis_cache.is_enabled(),
        "statistics": stats,
        "ttl_config": {
            "final_data_ttl": settings.REDIS_FINAL_DATA_TTL,
            "final_data_ttl_days": settings.REDIS_FINAL_DATA_TTL / (24 * 3600),
            "active_data_ttl": 30,
            "static_data_ttl": 300,
        },
    }



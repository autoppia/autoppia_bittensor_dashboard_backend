"""Redis-based caching service for API responses."""

import hashlib
import json
import logging
import pickle
from functools import wraps
from typing import Any, Callable, Dict, Optional

try:
    from redis import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import RedisError

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = None
    RedisError = Exception
    RedisConnectionError = Exception

from app.config import settings

logger = logging.getLogger(__name__)
# Reduce cache hit verbosity - only show warnings/errors
logger.setLevel(logging.WARNING)


class RedisCache:
    """
    Redis-based cache that stores payloads centrally so that multiple processes share a
    single cache. If Redis becomes unavailable we simply return cache misses instead of
    keeping large payloads in-process.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        socket_timeout: int = 2,
        socket_connect_timeout: int = 2,
        enabled: bool = True,
    ):
        self._redis_client: Optional[Redis] = None
        self._redis_available = False
        self._enabled = enabled and REDIS_AVAILABLE
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout

        # Statistics
        self._stats = {
            "redis_hits": 0,
            "redis_misses": 0,
            "redis_sets": 0,
            "redis_errors": 0,
        }

        if not REDIS_AVAILABLE:
            logger.warning("⚠️  Redis library not installed. Install with: pip install redis\n   Falling back to in-memory cache only.")
        elif self._enabled:
            self._connect()
        else:
            logger.info("Redis caching is disabled via configuration")

    def _connect(self):
        """Establish connection to Redis."""
        if not REDIS_AVAILABLE:
            return

        try:
            self._redis_client = Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=False,  # We use pickle for serialization
                socket_connect_timeout=self._socket_connect_timeout,
                socket_timeout=self._socket_timeout,
                socket_keepalive=True,
                health_check_interval=30,
            )
            # Test connection
            self._redis_client.ping()
            self._redis_available = True
            logger.info(f"✅ Redis connected at {self._host}:{self._port} (db={self._db}, timeout={self._socket_timeout}s)")
        except (RedisError, RedisConnectionError, OSError) as e:
            self._redis_available = False
            self._redis_client = None
            logger.warning(f"⚠️  Redis unavailable at {self._host}:{self._port}: {e}\n   Falling back to in-memory cache.")

    def _serialize(self, value: Any) -> bytes:
        """Serialize value for Redis storage using pickle."""
        try:
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            logger.error(f"Failed to serialize value: {e}")
            raise

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize value from Redis using pickle."""
        try:
            return pickle.loads(data)
        except Exception as e:
            logger.error(f"Failed to deserialize value: {e}")
            raise

    def _generate_key(self, prefix: str, *args, **kwargs) -> str:
        """Generate a cache key from prefix and arguments."""
        if not args and not kwargs:
            return prefix

        key_data = {"args": args, "kwargs": sorted(kwargs.items()) if kwargs else {}}
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        key_hash = hashlib.md5(key_str.encode()).hexdigest()[:16]  # Short hash
        return f"{prefix}:{key_hash}"

    def get(self, key: str) -> Optional[Any]:
        """
        Get value from Redis cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if self._redis_available and self._redis_client:
            try:
                data = self._redis_client.get(key)
                if data is not None:
                    self._stats["redis_hits"] += 1
                    logger.debug(f"✅ Redis cache hit: {key}")
                    return self._deserialize(data)
                self._stats["redis_misses"] += 1
                logger.debug(f"❌ Redis cache miss: {key}")
            except (RedisError, RedisConnectionError, OSError) as e:
                self._stats["redis_errors"] += 1
                logger.warning(f"Redis error on get({key}): {e}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Unexpected error on Redis get({key}): {e}")

        return None

    def set(self, key: str, value: Any, ttl: int = 604800) -> bool:  # 7 days default
        """
        Set value in Redis cache with TTL, also update in-memory cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (default: 7 days)

        Returns:
            True if successfully set in Redis, False otherwise
        """
        success = False

        # Try Redis first
        if self._redis_available and self._redis_client:
            try:
                serialized = self._serialize(value)
                self._redis_client.setex(key, ttl, serialized)
                self._stats["redis_sets"] += 1
                logger.debug(f"✅ Redis cache set: {key} (TTL: {ttl}s, size: {len(serialized)} bytes)")
                success = True
            except (RedisError, RedisConnectionError, OSError) as e:
                self._stats["redis_errors"] += 1
                logger.warning(f"Redis error on set({key}): {e}")
            except Exception as e:
                logger.error(f"Unexpected error on Redis set({key}): {e}")

        return success

    def delete(self, key: str) -> bool:
        """
        Delete key from both Redis and in-memory cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted from Redis
        """
        deleted = False

        if self._redis_available and self._redis_client:
            try:
                result = self._redis_client.delete(key)
                deleted = result > 0
                logger.debug(f"Deleted Redis key: {key}")
            except (RedisError, RedisConnectionError, OSError) as e:
                logger.warning(f"Redis error on delete({key}): {e}")

        return deleted

    def clear_pattern(self, pattern: str) -> int:
        """
        Clear all keys matching a pattern (e.g., 'round:*').

        Args:
            pattern: Pattern to match (Redis pattern syntax)

        Returns:
            Number of keys cleared
        """
        cleared = 0

        if self._redis_available and self._redis_client:
            try:
                cursor = 0
                while True:
                    cursor, keys = self._redis_client.scan(cursor=cursor, match=pattern, count=100)
                    if keys:
                        cleared += self._redis_client.delete(*keys)
                    if cursor == 0:
                        break
                logger.info(f"Cleared {cleared} Redis keys matching '{pattern}'")
            except (RedisError, RedisConnectionError, OSError) as e:
                logger.warning(f"Redis error on clear_pattern({pattern}): {e}")

        return cleared

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        stats = {**self._stats}
        stats["redis_available"] = self._redis_available
        stats["redis_enabled"] = self._enabled
        stats["redis_library_installed"] = REDIS_AVAILABLE

        if self._redis_available and self._redis_client:
            try:
                info = self._redis_client.info("stats")
                stats["redis_keys"] = self._redis_client.dbsize()
                stats["redis_memory_used"] = info.get("used_memory_human", "N/A")
                stats["redis_total_connections"] = info.get("total_connections_received", 0)
                stats["redis_keyspace_hits"] = info.get("keyspace_hits", 0)
                stats["redis_keyspace_misses"] = info.get("keyspace_misses", 0)
            except (RedisError, RedisConnectionError, OSError):
                pass

        return stats

    def is_available(self) -> bool:
        """Check if Redis is available."""
        return self._redis_available

    def is_enabled(self) -> bool:
        """Check if Redis is enabled."""
        return self._enabled

    def reconnect(self) -> bool:
        """Attempt to reconnect to Redis."""
        if not self._enabled or not REDIS_AVAILABLE:
            return False

        logger.info("Attempting to reconnect to Redis...")
        self._connect()
        return self._redis_available


# Global Redis cache instance
redis_cache = RedisCache(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
    enabled=settings.REDIS_ENABLED,
)


# Cache TTL constants for Redis (longer TTLs for immutable data)
REDIS_CACHE_TTL = {
    # Completed rounds are IMMUTABLE - cache for 7 days
    "round_detail_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "round_tasks_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "round_miners_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "round_validators_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "round_statistics_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "task_detail_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "task_solutions_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "evaluation_detail_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    # Active rounds - shorter TTL
    "round_detail_active": 60,  # 1 minute
    "round_tasks_active": 30,  # 30 seconds
    # Lists and aggregations - medium TTL
    "rounds_list": 180,  # 3 minutes
    "agents_list": 600,  # 10 minutes
    "leaderboard": 300,  # 5 minutes
    "overview": 300,  # 5 minutes
    # Agent run scoped caches
    "agent_run_statistics_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
    "agent_run_summary_final": settings.REDIS_FINAL_DATA_TTL,  # 7 days
}


def cached_redis(
    prefix: str,
    ttl: Optional[int] = None,
    is_final: bool = False,  # Mark as final/immutable data
):
    """
    Decorator to cache function results in Redis.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds (default: 7 days for final, 5 min for active)
        is_final: If True, data is immutable and cached for 7 days

    Example:
        @cached_redis("round_detail", is_final=True)
        async def get_round_detail(round_id: int):
            return await fetch_from_db(round_id)
    """
    # Determine TTL
    if ttl is None:
        if is_final:
            ttl = settings.REDIS_FINAL_DATA_TTL  # 7 days
        else:
            ttl = 300  # 5 minutes

    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Filter out session/db objects from cache key
            # These change on every request and shouldn't affect caching
            filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ("session", "db") and not str(type(v).__name__).endswith("Session")}

            # Generate cache key (without session)
            cache_key = redis_cache._generate_key(prefix, *args, **filtered_kwargs)

            # Try to get from cache
            cached_result = redis_cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {prefix}")
                return cached_result

            # Execute function
            logger.debug(f"Cache miss for {prefix}, executing function")
            result = await func(*args, **kwargs)

            # Cache result (only if not None)
            if result is not None:
                redis_cache.set(cache_key, result, ttl)

            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Filter out session/db objects from cache key
            filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ("session", "db") and not str(type(v).__name__).endswith("Session")}

            # Generate cache key (without session)
            cache_key = redis_cache._generate_key(prefix, *args, **filtered_kwargs)

            # Try to get from cache
            cached_result = redis_cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for {prefix}")
                return cached_result

            # Execute function
            logger.debug(f"Cache miss for {prefix}, executing function")
            result = func(*args, **kwargs)

            # Cache result (only if not None)
            if result is not None:
                redis_cache.set(cache_key, result, ttl)

            return result

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Simple alias for easier use
def cache(prefix: str, ttl: int = 300):
    """
    Simple cache decorator using Redis.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds (default: 5 minutes)

    Example:
        @cache("rounds_list", ttl=180)
        async def get_rounds():
            return await db.query(...)
    """
    return cached_redis(prefix, ttl=ttl, is_final=False)

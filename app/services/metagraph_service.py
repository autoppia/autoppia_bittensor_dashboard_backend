"""
Service for fetching real-time validator data from the Bittensor metagraph.

CRITICAL: This service uses Redis for caching. GET endpoints should ONLY read
from Redis and NEVER call the subtensor directly (subtensor calls take 2-3 seconds).

A separate background worker updates Redis every 30 minutes.

Architecture:
- get_validator_data() / get_all_validators_data() → Read ONLY from Redis (never call subtensor)
- refresh_metagraph_data() → Called by background worker to fetch from subtensor and update Redis
- Background worker runs every 30 minutes to keep Redis fresh
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from app.config import settings
from app.services.redis_cache import redis_cache

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Reduce verbosity

# Redis keys
REDIS_KEY_ALL_VALIDATORS = "metagraph:validators:all"
REDIS_KEY_VALIDATOR_PREFIX = "metagraph:validator"
REDIS_KEY_LAST_UPDATE = "metagraph:last_update"
REDIS_KEY_UPDATE_STATUS = "metagraph:update_status"

# Cache TTL: 30 minutes (1800 seconds)
# The background worker refreshes every 30 minutes
METAGRAPH_CACHE_TTL = 30 * 60  # 1800 seconds


class MetagraphError(Exception):
    """Raised when metagraph operations fail."""

    pass


def _extract_numeric_value(raw_value: object) -> Optional[float]:
    """
    Safely extract a numeric value from various bittensor tensor types.

    Args:
        raw_value: Raw value from metagraph (could be tensor, float, etc.)

    Returns:
        Float value or None if extraction fails
    """
    if raw_value is None:
        return None

    try:
        # Handle tensor types with .item()
        if hasattr(raw_value, "item"):
            return float(raw_value.item())
        # Handle regular numeric types
        return float(raw_value)
    except (ValueError, TypeError, AttributeError):
        return None


def _convert_to_list(raw_data: object) -> list:
    """
    Convert various collection types to a plain list.

    Args:
        raw_data: Data from metagraph (could be tensor, list, tuple, etc.)

    Returns:
        Plain Python list
    """
    if raw_data is None:
        return []

    # Try tolist() for tensor types
    if hasattr(raw_data, "tolist"):
        return list(raw_data.tolist())

    # Try direct conversion for sequences
    if isinstance(raw_data, (list, tuple)):
        return list(raw_data)

    # Try iteration
    try:
        return list(raw_data)
    except TypeError:
        # Single value
        return [raw_data]


def refresh_metagraph_data() -> Dict[str, Any]:
    """
    Fetch fresh data from the Bittensor metagraph and update Redis.

    **WARNING**: This function calls the subtensor and takes 2-3 seconds.
    It should ONLY be called by the background worker, NEVER by GET endpoints.

    Returns:
        Dictionary with validator data indexed by UID and hotkey

    Raises:
        MetagraphError: If metagraph cannot be loaded
    """
    logger.info("🔄 Starting metagraph data refresh (this will call subtensor)...")
    start_time = time.time()

    try:
        import bittensor as bt  # type: ignore
    except ImportError as exc:
        raise MetagraphError(f"Bittensor library unavailable: {exc}") from exc

    # Connect to subtensor
    subtensor_kwargs: Dict[str, str] = {}
    if settings.SUBTENSOR_NETWORK:
        subtensor_kwargs["network"] = settings.SUBTENSOR_NETWORK
        logger.debug(f"Connecting to Subtensor network: {settings.SUBTENSOR_NETWORK}")

    try:
        subtensor = bt.subtensor(**subtensor_kwargs)  # type: ignore[attr-defined]
        metagraph = subtensor.metagraph(netuid=settings.VALIDATOR_NETUID)
    except Exception as exc:
        raise MetagraphError(f"Unable to fetch metagraph: {exc}") from exc

    # Extract all relevant data
    hotkeys = _convert_to_list(getattr(metagraph, "hotkeys", None))
    uids = _convert_to_list(getattr(metagraph, "uids", None))
    stakes = _convert_to_list(getattr(metagraph, "S", None))

    # Try multiple possible attributes for vtrust
    vtrust_attrs = ["validator_trust", "V", "vtrust", "v_trust"]
    vtrust_raw = None
    vtrust_attr_used = None
    for attr in vtrust_attrs:
        vtrust_raw = getattr(metagraph, attr, None)
        if vtrust_raw is not None:
            vtrust_attr_used = attr
            logger.debug(f"Using '{attr}' for validator trust values")
            break

    vtrustvalues = _convert_to_list(vtrust_raw) if vtrust_raw is not None else []

    # Try to get version information (Bittensor uses 'version', not 'versions')
    version_raw = getattr(metagraph, "version", None)
    versions = _convert_to_list(version_raw) if version_raw is not None else []

    # Build indexed data structure
    validators_by_uid: Dict[int, Dict[str, Any]] = {}
    validators_by_hotkey: Dict[str, Dict[str, Any]] = {}

    max_len = max(len(hotkeys), len(uids), len(stakes))

    for index in range(max_len):
        # Extract UID
        uid = None
        if index < len(uids):
            uid = int(uids[index]) if uids[index] is not None else index
        else:
            uid = index

        # Extract hotkey
        hotkey = None
        if index < len(hotkeys):
            hotkey = str(hotkeys[index]) if hotkeys[index] else None

        # Extract stake (already in TAO, not RAO)
        stake_tao = None
        if index < len(stakes):
            stake_tao = _extract_numeric_value(stakes[index])

        # Extract vtrust
        vtrust = None
        if index < len(vtrustvalues):
            vtrust = _extract_numeric_value(vtrustvalues[index])

        # Extract version (keep as string to preserve "10.1.0" format)
        version = None
        if index < len(versions):
            version_val = versions[index]
            if version_val is not None:
                version = str(version_val)  # Keep as string

        validator_data = {
            "uid": uid,
            "hotkey": hotkey,
            "stake": stake_tao,
            "vtrust": vtrust,
            "version": version,  # string: "10.1.0"
            "fetched_at": time.time(),
        }

        validators_by_uid[uid] = validator_data
        if hotkey:
            validators_by_hotkey[hotkey] = validator_data

            # Also store individual validator in Redis for fast lookup
            individual_key = f"{REDIS_KEY_VALIDATOR_PREFIX}:uid:{uid}"
            redis_cache.set(individual_key, validator_data, ttl=METAGRAPH_CACHE_TTL)

    result = {
        "by_uid": validators_by_uid,
        "by_hotkey": validators_by_hotkey,
        "fetched_at": time.time(),
        "vtrust_source": vtrust_attr_used,
    }

    # Store in Redis
    redis_cache.set(REDIS_KEY_ALL_VALIDATORS, result, ttl=METAGRAPH_CACHE_TTL)
    redis_cache.set(REDIS_KEY_LAST_UPDATE, time.time(), ttl=METAGRAPH_CACHE_TTL)
    redis_cache.set(
        REDIS_KEY_UPDATE_STATUS,
        {
            "status": "success",
            "timestamp": time.time(),
            "validator_count": len(validators_by_uid),
            "vtrust_source": vtrust_attr_used,
        },
        ttl=METAGRAPH_CACHE_TTL,
    )

    elapsed = time.time() - start_time
    logger.info(
        f"✅ Metagraph data refreshed: {len(validators_by_uid)} validators "
        f"(vtrust source: {vtrust_attr_used or 'none'}, took {elapsed:.2f}s)"
    )

    return result


def get_validator_data(
    uid: Optional[int] = None,
    hotkey: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get real-time data for a specific validator FROM REDIS ONLY.

    **CRITICAL**: This function NEVER calls the subtensor. It only reads from Redis.
    If data is not in Redis, it returns None. The background worker is responsible
    for keeping Redis up to date.

    Args:
        uid: Validator UID (optional)
        hotkey: Validator hotkey (optional)

    Returns:
        Validator data dict or None if not found in Redis

    Note:
        At least one of uid or hotkey must be provided.
    """
    if uid is None and hotkey is None:
        raise ValueError("Either uid or hotkey must be provided")

    # Try to get from Redis (individual key first for fast lookup)
    if uid is not None:
        individual_key = f"{REDIS_KEY_VALIDATOR_PREFIX}:uid:{uid}"
        validator = redis_cache.get(individual_key)
        if validator:
            logger.debug(f"Found validator {uid} in Redis (individual key)")
            return validator

    # Fallback: get from the full dataset
    try:
        data = redis_cache.get(REDIS_KEY_ALL_VALIDATORS)
        if data is None:
            logger.warning(
                "Metagraph data not found in Redis. "
                "Background worker may not be running or Redis may be down."
            )
            return None
    except Exception as exc:
        logger.error(f"Failed to fetch metagraph data from Redis: {exc}")
        return None

    # Lookup by UID first
    if uid is not None:
        validator = data["by_uid"].get(uid)
        if validator:
            return validator

    # Fallback to hotkey lookup
    if hotkey is not None:
        validator = data["by_hotkey"].get(hotkey)
        if validator:
            return validator

    return None


def get_all_validators_data() -> Dict[int, Dict[str, Any]]:
    """
    Get real-time data for all validators FROM REDIS ONLY.

    **CRITICAL**: This function NEVER calls the subtensor. It only reads from Redis.
    If data is not in Redis, it returns an empty dict. The background worker is
    responsible for keeping Redis up to date.

    Returns:
        Dictionary mapping UID to validator data (empty dict if Redis has no data)
    """
    try:
        data = redis_cache.get(REDIS_KEY_ALL_VALIDATORS)
        if data is None:
            logger.warning(
                "Metagraph data not found in Redis. "
                "Background worker may not be running or Redis may be down."
            )
            return {}
        return data["by_uid"]
    except Exception as exc:
        logger.error(f"Failed to fetch metagraph data from Redis: {exc}")
        return {}


def get_last_update_time() -> Optional[float]:
    """
    Get the timestamp of the last successful metagraph update.

    Returns:
        Unix timestamp or None if never updated
    """
    try:
        return redis_cache.get(REDIS_KEY_LAST_UPDATE)
    except Exception:
        return None


def get_update_status() -> Dict[str, Any]:
    """
    Get the status of the last metagraph update.

    Returns:
        Status dict with timestamp, validator count, etc.
    """
    try:
        status = redis_cache.get(REDIS_KEY_UPDATE_STATUS)
        if status:
            return status
    except Exception:
        pass

    return {
        "status": "unknown",
        "timestamp": None,
        "validator_count": 0,
        "vtrust_source": None,
    }


def is_data_stale(max_age_seconds: int = 3600) -> bool:
    """
    Check if metagraph data in Redis is stale.

    Args:
        max_age_seconds: Maximum age in seconds before data is considered stale (default: 1 hour)

    Returns:
        True if data is stale or missing, False if fresh
    """
    last_update = get_last_update_time()
    if last_update is None:
        return True

    age = time.time() - last_update
    return age > max_age_seconds


def force_refresh() -> bool:
    """
    Force an immediate refresh of metagraph data (calls subtensor).

    **WARNING**: This calls the subtensor and takes 2-3 seconds.
    Should only be used for manual admin operations, not in GET endpoints.

    Returns:
        True if refresh succeeded, False otherwise
    """
    try:
        refresh_metagraph_data()
        return True
    except MetagraphError as exc:
        logger.error(f"Failed to force refresh metagraph data: {exc}")
        return False

"""
Cache management endpoints for monitoring and controlling API caching.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from app.services.cache import api_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cache", tags=["cache"])

@router.get("/stats")
async def get_cache_stats():
    """Get cache statistics and performance metrics."""
    try:
        stats = api_cache.get_stats()
        return {
            "success": True,
            "data": {
                "cache_stats": stats,
                "cache_ttl_config": {
                    "overview_metrics": "5 minutes",
                    "validators_list": "10 minutes", 
                    "rounds_list": "3 minutes",
                    "round_detail": "5 minutes",
                    "round_miners": "2 minutes",
                    "round_validators": "5 minutes",
                    "round_statistics": "3 minutes",
                    "current_round": "1 minute",
                    "network_status": "2 minutes",
                    "leaderboard": "5 minutes",
                    "agents_list": "10 minutes"
                }
            }
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get cache stats: {str(e)}")

@router.post("/clear")
async def clear_cache(
    pattern: Optional[str] = Query(None, description="Pattern to match cache keys (optional)")
):
    """Clear cache entries, optionally matching a pattern."""
    try:
        cleared_count = api_cache.clear(pattern)
        return {
            "success": True,
            "data": {
                "message": f"Cleared {cleared_count} cache entries",
                "pattern": pattern,
                "cleared_count": cleared_count
            }
        }
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")

@router.post("/clear/overview")
async def clear_overview_cache():
    """Clear all overview-related cache entries."""
    try:
        cleared_count = api_cache.clear("overview")
        return {
            "success": True,
            "data": {
                "message": f"Cleared {cleared_count} overview cache entries",
                "cleared_count": cleared_count
            }
        }
    except Exception as e:
        logger.error(f"Error clearing overview cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear overview cache: {str(e)}")

@router.post("/clear/rounds")
async def clear_rounds_cache():
    """Clear all rounds-related cache entries."""
    try:
        cleared_count = api_cache.clear("rounds")
        return {
            "success": True,
            "data": {
                "message": f"Cleared {cleared_count} rounds cache entries",
                "cleared_count": cleared_count
            }
        }
    except Exception as e:
        logger.error(f"Error clearing rounds cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear rounds cache: {str(e)}")

@router.post("/clear/validators")
async def clear_validators_cache():
    """Clear all validators-related cache entries."""
    try:
        cleared_count = api_cache.clear("validators")
        return {
            "success": True,
            "data": {
                "message": f"Cleared {cleared_count} validators cache entries",
                "cleared_count": cleared_count
            }
        }
    except Exception as e:
        logger.error(f"Error clearing validators cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear validators cache: {str(e)}")

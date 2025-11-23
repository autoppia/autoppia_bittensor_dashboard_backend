# app/main.py
from __future__ import annotations

# ── Configure logging FIRST (before any DB/ORM imports) ────────────────────────
from app.config import settings
from app.logging import init_logging, reapply_handler_filters_after_uvicorn_started

logger, log_level = init_logging(settings)
# ───────────────────────────────────────────────────────────────────────────────

import os
import time
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.middleware.logging_middleware import DetailedLoggingMiddleware
from app.api.ui.agent_runs import router as agent_runs_router
from app.api.ui.agents import router as agents_router
from app.api.ui.evaluations import router as evaluations_router
from app.api.ui.legacy_rounds import legacy_router as legacy_rounds_router
from app.api.ui.miner_list import router as miner_list_router
from app.api.ui.miners import router as miners_router
from app.api.ui.overview import router as overview_router
from app.api.ui.rounds import router as rounds_router
from app.api.ui.subnets import legacy_router as subnets_legacy_router
from app.api.ui.subnets import router as subnets_router
from app.api.ui.tasks import router as tasks_router
from app.api.validator.validator_round import router as validator_rounds_router
from app.db.session import init_db, get_session
from app.services.idempotency import get_cache_stats

# Background updaters are now run as separate PM2 processes
# from app.services.metagraph_updater_thread import (
#     start_metagraph_updater,
#     stop_metagraph_updater,
#     get_updater_status,
# )
# from app.services.overview_cache_updater import (
#     start_overview_updater,
#     stop_overview_updater,
# )
from app.services.ui.agents_service import (
    AgentsService,
    AgentAggregateCacheWarmupRequired,
)
from sqlalchemy.ext.asyncio import AsyncSession


app = FastAPI(
    title=settings.APP_NAME,
    description="FastAPI backend for Autoppia Bittensor Leaderboard",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS - Configuración completa para permitir requests desde todos los subdominios
cors_kwargs = {
    "allow_origins": settings.CORS_ORIGINS,
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    "allow_headers": [
        "*",
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Validator-Hotkey",
        "X-Validator-Signature",
    ],
    "expose_headers": [
        "Content-Type",
        "X-Total-Count",
    ],
    "max_age": 600,  # Cache preflight requests for 10 minutes
}
if settings.CORS_ALLOW_ORIGIN_REGEX:
    cors_kwargs["allow_origin_regex"] = settings.CORS_ALLOW_ORIGIN_REGEX

app.add_middleware(CORSMiddleware, **cors_kwargs)

# Detailed logging middleware (optional, configured via env)
if settings.LOG_REQUEST_BODY or settings.LOG_RESPONSE_BODY:
    app.add_middleware(
        DetailedLoggingMiddleware,
        log_request_body=settings.LOG_REQUEST_BODY,
        log_response_body=settings.LOG_RESPONSE_BODY,
    )

# Static files
images_path = os.path.join(os.path.dirname(__file__), "..", "images")
try:
    os.makedirs(images_path, exist_ok=True)
except OSError as exc:
    logger.warning(f"Unable to prepare images directory at {images_path}: {exc}")
else:
    app.mount("/images", StaticFiles(directory=images_path), name="images")
    logger.info(f"Mounted static files from {images_path}")


# Request logging (compact)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    client = request.client.host if request.client else "unknown"
    logger.info(f"{request.method} {request.url.path} - {client}")
    resp = await call_next(request)
    elapsed = time.time() - start
    logger.info(
        f"{request.method} {request.url.path} - {resp.status_code} - {elapsed:.3f}s"
    )
    return resp


# Routers
app.include_router(validator_rounds_router)
app.include_router(rounds_router)
app.include_router(legacy_rounds_router)
app.include_router(agent_runs_router)
app.include_router(evaluations_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(miners_router)
app.include_router(overview_router)
app.include_router(miner_list_router)
app.include_router(subnets_router)
app.include_router(subnets_legacy_router)


# Health
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time(), "version": "0.1.0"}


# Debug/idempotency
@app.get("/debug/idempotency-stats")
async def idempotency_stats():
    return get_cache_stats()


# Cache debug helpers
@app.get("/debug/cache-stats")
async def cache_stats():
    from app.services.redis_cache import redis_cache

    return redis_cache.get_stats()


@app.post("/debug/cache-disable")
async def disable_cache():
    return {
        "message": "Redis cache cannot be disabled via API. Manage Redis directly instead.",
        "disabled": False,
    }


@app.post("/debug/cache-enable")
async def enable_cache():
    return {
        "message": "Redis cache is always enabled when Redis is available.",
        "disabled": False,
    }


@app.post("/debug/cache-clear")
async def clear_cache():
    from app.services.redis_cache import redis_cache

    cleared = redis_cache.clear_pattern("*")
    return {"message": f"Cleared {cleared} Redis cache entries", "cleared": cleared}


@app.post("/admin/warm/agents")
async def admin_warm_agents(
    session: AsyncSession = Depends(get_session),
):
    """
    DEPRECATED: Agent aggregates are now materialized incrementally when rounds finish.
    This endpoint is no longer needed and does nothing.

    The new system:
    - Snapshots are saved automatically when rounds complete
    - Agent stats are updated incrementally after each round
    - No manual warming is needed
    """
    logger.warning("⚠️  /admin/warm/agents called but is deprecated - doing nothing")
    return {
        "ok": False,
        "deprecated": True,
        "message": "This endpoint is deprecated. Agent stats are now updated incrementally when rounds finish.",
        "info": "No action taken. The new system materializes data automatically.",
    }


@app.post("/admin/materialize-round/{round_number}")
async def materialize_round_snapshot(
    round_number: int,
    session: AsyncSession = Depends(get_session),
):
    """
    Materialize a snapshot for a specific round.

    This endpoint:
    1. Checks if snapshot already exists (returns it if so)
    2. Fetches the round data from DB
    3. Materializes snapshot + agent stats
    4. Returns the snapshot

    Useful for backfilling or re-materializing specific rounds.
    """
    from app.db.models import RoundSnapshotORM, ValidatorRoundORM
    from app.services.snapshot_service import SnapshotService
    from sqlalchemy import select

    # Check if snapshot already exists
    existing_snapshot = await session.get(RoundSnapshotORM, round_number)
    if existing_snapshot:
        return {
            "ok": True,
            "message": f"Snapshot for round {round_number} already exists",
            "round_number": round_number,
            "data_size_kb": (
                existing_snapshot.data_size_bytes / 1024
                if existing_snapshot.data_size_bytes
                else None
            ),
            "created_at": existing_snapshot.created_at.isoformat(),
            "already_existed": True,
        }

    # Find the round
    stmt = (
        select(ValidatorRoundORM)
        .where(ValidatorRoundORM.round_number == round_number)
        .where(ValidatorRoundORM.ended_at != None)
    )
    round_row = await session.scalar(stmt)

    if not round_row:
        raise HTTPException(
            status_code=404, detail=f"Round {round_number} not found or not completed"
        )

    # Create mock payload from round data
    winners = []
    weights = {}

    if round_row.meta:
        if "winners" in round_row.meta:
            winners = round_row.meta["winners"]
        if "weights" in round_row.meta:
            weights = round_row.meta["weights"]

    payload = FinishRoundRequest(
        status=round_row.status or "completed",
        winners=winners,
        winner_scores=[],
        weights=weights,
        ended_at=round_row.ended_at or 0.0,
        summary=round_row.summary or {},
        agent_runs=[],
    )

    # Materialize using SnapshotService
    try:
        snapshot_service = SnapshotService(session)
        await snapshot_service.materialize_round_snapshot(round_number)
        await snapshot_service.update_agent_stats(round_number)
        await session.commit()

        # Get the created snapshot
        snapshot = await session.get(RoundSnapshotORM, round_number)

        return {
            "ok": True,
            "message": f"Snapshot for round {round_number} materialized successfully",
            "round_number": round_number,
            "data_size_kb": (
                snapshot.data_size_bytes / 1024
                if snapshot and snapshot.data_size_bytes
                else None
            ),
            "created_at": snapshot.created_at.isoformat() if snapshot else None,
            "already_existed": False,
        }

    except Exception as e:
        await session.rollback()
        logger.error(f"Failed to materialize round {round_number}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to materialize round {round_number}: {str(e)}",
        )


@app.get("/debug/aggregates-meta")
async def debug_aggregates_meta():
    """Inspect aggregates snapshot metadata in Redis."""
    from app.services.redis_cache import redis_cache

    meta = redis_cache.get("AGGREGATES:meta:v1")
    if not meta:
        return {"ok": False, "meta": None}
    return {"ok": True, "meta": meta}


@app.get("/debug/background-updater-status")
async def background_updater_status():
    """Get the status of the background updater (now runs as separate PM2 process)."""
    from app.services.metagraph_service import get_last_update_time
    from app.services.redis_cache import redis_cache

    last_update = get_last_update_time()
    age_minutes = (time.time() - last_update) / 60 if last_update else None

    return {
        "running": "Check PM2: pm2 list | grep background-updater",
        "last_update": last_update,
        "age_minutes": age_minutes,
        "redis_available": redis_cache.is_available(),
        "note": "Background updater runs as separate PM2 process (see ecosystem.config.js)",
    }


@app.get("/debug/metagraph-status")
async def metagraph_status():
    """Get the status of metagraph data (deprecated, use /debug/background-updater-status)."""
    return await background_updater_status()


@app.post("/debug/metagraph-force-refresh")
async def metagraph_force_refresh():
    """Force an immediate refresh of metagraph data (takes 2-3 seconds)."""
    from app.services.metagraph_service import force_refresh

    success = force_refresh()
    if success:
        return {"ok": True, "message": "Metagraph data refreshed successfully"}
    else:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Failed to refresh metagraph data"},
        )


# Startup / Shutdown
@app.on_event("startup")
async def on_startup():
    logger.info("Starting Autoppia IWA Platform API...")
    # Ensure our SQLA filters are attached to any handlers Uvicorn added
    reapply_handler_filters_after_uvicorn_started()

    try:
        await init_db()
        logger.info("SQL schema ready")
        logger.info(f"API server ready on {settings.HOST}:{settings.PORT}")
        logger.info("API documentation available at /docs")
        # NOTE: Background updaters (metagraph, price, block) are now run as a separate PM2 process
        # See background_updater.py and ecosystem.config.js
        # This prevents multiprocessing workers from saturating the API process
        logger.info("ℹ️  Background updaters disabled (run as separate PM2 process)")

        # Overview cache warmer is also disabled - use external cron/PM2 if needed
        logger.info(
            "ℹ️  Overview cache warmer disabled (use external process if needed)"
        )

    except Exception as e:
        logger.error(f"Failed to initialize application: {e}", exc_info=True)
        raise


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down Autoppia IWA Platform API...")

    # Background updaters are now run as separate PM2 processes
    # No need to stop them here
    logger.info("ℹ️  Background updaters run as separate processes (no cleanup needed)")


# Global exception handler
from fastapi import Request as _Request  # avoid shadowing


@app.exception_handler(Exception)
async def global_exception_handler(request: _Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal server error",
            "detail": "An unexpected error occurred",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.UVICORN_LOG_LEVEL.lower(),
        access_log=settings.UVICORN_ACCESS_LOG,
    )

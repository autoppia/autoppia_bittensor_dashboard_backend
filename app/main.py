# app/main.py
# ruff: noqa: E402
from __future__ import annotations

# ── Configure logging FIRST (before any DB/ORM imports) ────────────────────────
from app.config import settings
from app.logging import init_logging, reapply_handler_filters_after_uvicorn_started

logger, log_level = init_logging(settings)
# ───────────────────────────────────────────────────────────────────────────────

import os
import re
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.external.tasks import router as external_tasks_router
from app.api.ui.agent_runs import router as agent_runs_router
from app.api.ui.agents import router as agents_router
from app.api.ui.evaluations import router as evaluations_router
from app.api.ui.miner_list import router as miner_list_router
from app.api.ui.miners import router as miners_router
from app.api.ui.overview import router as overview_router
from app.api.ui.rounds import router as rounds_router
from app.api.ui.subnets import router as subnets_router
from app.api.ui.tasks import router as tasks_router
from app.api.ui.validators import router as validators_router
from app.api.validator.task_logs import router as task_logs_router
from app.api.validator.validator_round import router as validator_rounds_router
from app.db.session import AsyncSessionLocal, get_session, init_db
from app.middleware.logging_middleware import DetailedLoggingMiddleware
from app.services.idempotency import get_cache_stats

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

# Detailed logging middleware (optional, configured via env) — add before CORS so CORS is last in the chain
if settings.LOG_REQUEST_BODY or settings.LOG_RESPONSE_BODY:
    app.add_middleware(
        DetailedLoggingMiddleware,
        log_request_body=settings.LOG_REQUEST_BODY,
        log_response_body=settings.LOG_RESPONSE_BODY,
    )

app.add_middleware(CORSMiddleware, **cors_kwargs)

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
    logger.info(f"{request.method} {request.url.path} - {resp.status_code} - {elapsed:.3f}s")
    return resp


# Routers
app.include_router(validator_rounds_router)
app.include_router(task_logs_router)
app.include_router(rounds_router)
app.include_router(agent_runs_router)
app.include_router(evaluations_router)
# IMPORTANT: external_tasks_router must be registered BEFORE tasks_router
# because both share the same prefix /api/v1/tasks and tasks_router has a catch-all route /{task_id}
# FastAPI matches routes in registration order, so specific routes must come before generic ones
app.include_router(external_tasks_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(miners_router)
app.include_router(overview_router)
app.include_router(miner_list_router)
app.include_router(subnets_router)
app.include_router(validators_router)


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
    session: Annotated[AsyncSession, Depends(get_session)],
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


# Snapshot functionality removed
# @app.post("/admin/materialize-round/{round_number}")
# Snapshot functionality removed - no longer using round_snapshots table


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

        # Load round config from DB (required; no .env fallback for round timing)
        from app.services.round_config_service import get_config_season_round, refresh_config_season_round_cache, set_config_season_round_cache

        cfg = None
        async with AsyncSessionLocal() as session:
            try:
                await refresh_config_season_round_cache(session)
                cfg = get_config_season_round()
            except RuntimeError as exc:
                # Allow API startup even if config_season_round is not initialized yet.
                # Main validator can bootstrap it through /api/v1/validator-rounds/runtime-config.
                set_config_season_round_cache(None)
                logger.warning("config_season_round not initialized yet: %s", exc)
                logger.warning("Waiting for main validator runtime-config sync. Endpoints requiring round boundaries may fail until config is set.")
        if cfg is not None:
            logger.info("=" * 80)
            logger.info("🔧 ROUND CONFIG (DB source of truth)")
            logger.info("=" * 80)
            logger.info(f"🔢 MINIMUM_START_BLOCK: {cfg.minimum_start_block:,}")
            logger.info(f"⏱️  Round size: {cfg.round_size_epochs} epochs")
            logger.info(f"📦 Blocks per round: {cfg.round_blocks()}")
            logger.info("=" * 80)

        logger.info(f"API server ready on {settings.HOST}:{settings.PORT}")
        logger.info("API documentation available at /docs")
        # NOTE: Background updaters (metagraph, price, block) are now run as a separate PM2 process
        # See background_updater.py and ecosystem.config.js
        # This prevents multiprocessing workers from saturating the API process
        logger.info("ℹ️  Background updaters disabled (run as separate PM2 process)")

        # Overview cache warmer is also disabled - use external cron/PM2 if needed
        logger.info("ℹ️  Overview cache warmer disabled (use external process if needed)")

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
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Get CORS origins from settings
    from app.config import settings

    origin = request.headers.get("origin")
    allowed_origins = settings.CORS_ORIGINS

    # Check if origin is allowed
    headers = {}
    if origin and (origin in allowed_origins or any(re.match(pattern, origin) for pattern in [settings.CORS_ALLOW_ORIGIN_REGEX] if settings.CORS_ALLOW_ORIGIN_REGEX)):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal server error",
            "detail": str(exc) if settings.DEBUG else "An unexpected error occurred",
        },
        headers=headers,
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

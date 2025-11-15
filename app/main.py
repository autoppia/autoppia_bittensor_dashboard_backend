# app/main.py
from __future__ import annotations

# ── Configure logging FIRST (before any DB/ORM imports) ────────────────────────
from app.config import settings
from app.logging import init_logging, reapply_handler_filters_after_uvicorn_started

logger, log_level = init_logging(settings)
# ───────────────────────────────────────────────────────────────────────────────

import os
import time
from fastapi import FastAPI, Request
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
from app.db.session import init_db
from app.services.idempotency import get_cache_stats
from app.services.metagraph_updater_thread import (
    start_metagraph_updater,
    stop_metagraph_updater,
    get_updater_status,
)
from app.services.overview_cache_updater import (
    start_overview_updater,
    stop_overview_updater,
)


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
    from app.services.cache import api_cache

    return api_cache.get_stats()


@app.post("/debug/cache-disable")
async def disable_cache():
    from app.services.cache import api_cache

    api_cache.disable()
    return {"message": "Cache disabled", "disabled": True}


@app.post("/debug/cache-enable")
async def enable_cache():
    from app.services.cache import api_cache

    api_cache.enable()
    return {"message": "Cache enabled", "disabled": False}


@app.post("/debug/cache-clear")
async def clear_cache():
    from app.services.cache import api_cache

    cleared = api_cache.clear()
    return {"message": f"Cleared {cleared} cache entries", "cleared": cleared}


@app.get("/debug/background-updater-status")
async def background_updater_status():
    """Get the status of the background updater thread (metagraph + price)."""
    return get_updater_status()


@app.get("/debug/metagraph-status")
async def metagraph_status():
    """Get the status of metagraph data (deprecated, use /debug/background-updater-status)."""
    return get_updater_status()


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
        # NOTE: Block refresher is now part of the metagraph_updater thread (consolidated)

        # Start background updater threads
        try:
            start_metagraph_updater()
            logger.info(
                "✅ Background data updater thread started (metagraph + price + block)"
            )
        except Exception as exc:
            logger.warning("Could not start background updater: %s", exc)

        # DISABLED: Overview updater causes asyncpg event loop conflicts
        # TODO: Fix asyncpg connection pooling issue before re-enabling
        # try:
        #     start_overview_updater()
        #     logger.info("✅ Overview metrics cache updater thread started")
        # except Exception as exc:
        #     logger.warning("Could not start overview updater: %s", exc)
        logger.info("⚠️  Overview background updater DISABLED (asyncpg conflicts)")

    except Exception as e:
        logger.error(f"Failed to initialize application: {e}", exc_info=True)
        raise


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down Autoppia IWA Platform API...")

    # Stop background updaters
    try:
        stop_metagraph_updater()
    except Exception:
        pass

    # Overview updater disabled (asyncpg conflicts)
    # try:
    #     stop_overview_updater()
    # except Exception:
    #     pass

    # NOTE: Block refresher is part of metagraph_updater (already stopped above)


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

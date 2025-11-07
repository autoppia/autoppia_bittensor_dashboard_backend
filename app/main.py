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


app = FastAPI(
    title=settings.APP_NAME,
    description="FastAPI backend for Autoppia Bittensor Leaderboard",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
cors_kwargs = {
    "allow_origins": settings.CORS_ORIGINS,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
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
        # Start background chain block refresher (non-blocking)
        try:
            from app.services.chain_state import start_block_refresher

            if int(getattr(settings, "CHAIN_BLOCK_REFRESH_PERIOD", 30) or 30) > 0:
                start_block_refresher(settings.CHAIN_BLOCK_REFRESH_PERIOD)
                logger.info(
                    "Chain block refresher started (period=%ss)",
                    settings.CHAIN_BLOCK_REFRESH_PERIOD,
                )
        except Exception as exc:
            logger.warning("Could not start chain block refresher: %s", exc)
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}", exc_info=True)
        raise


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Shutting down Autoppia IWA Platform API...")
    # add cleanup as needed
    try:
        from app.services.chain_state import stop_block_refresher

        stop_block_refresher()
    except Exception:
        pass


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

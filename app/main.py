import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.ui.agent_runs import router as agent_runs_router
from app.api.ui.agents import router as agents_router
from app.api.ui.cache import router as cache_router
from app.api.ui.evaluations import router as evaluations_router
from app.api.ui.legacy_rounds import legacy_router as legacy_rounds_router
from app.api.ui.miner_list import router as miner_list_router
from app.api.ui.miners import router as miners_router
from app.api.ui.overview import router as overview_router
from app.api.ui.rounds import router as rounds_router
from app.api.ui.subnets import legacy_router as subnets_legacy_router
from app.api.ui.subnets import router as subnets_router
from app.api.ui.tasks import router as tasks_router
from app.api.validator.rounds_post import router as rounds_post_router
from app.api.validator.validator_round import router as validator_rounds_router
from app.config import settings
from app.db.session import init_db
from app.services.idempotency import get_cache_stats

# Configure logging
_KNOWN_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _parse_log_level(value: str) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return _KNOWN_LEVELS.get(value.upper(), logging.INFO)
    return numeric if numeric in _KNOWN_LEVELS.values() else logging.INFO


configured_level_name = settings.LOG_LEVEL or "INFO"
if settings.DEBUG and configured_level_name == "INFO":
    configured_level_name = "DEBUG"

log_level = _parse_log_level(configured_level_name)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Align uvicorn/access loggers with the same level.
for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(name).setLevel(log_level)

# Quiet noisy dependencies unless explicitly elevated.
_NOISY_LOGGERS = {
    "btdecode": max(log_level, logging.INFO),
    "aiosqlite": max(log_level, logging.INFO),
    "bittensor": max(log_level, logging.INFO),
    "sqlalchemy": max(log_level, logging.WARNING),
    "sqlalchemy.engine": max(log_level, logging.WARNING),
    "sqlalchemy.engine.Engine": max(log_level, logging.WARNING),
}
for name, level in _NOISY_LOGGERS.items():
    logging.getLogger(name).setLevel(level)


def _configure_bittensor_logging(min_level: int = logging.INFO) -> None:
    """
    Ensure bittensor's logging stays at INFO or higher regardless of app log level.

    Bittensor manages its own logging stack, so we explicitly toggle its state here
    to avoid noisy debug output when the backend runs in DEBUG mode.
    """
    target_level = max(log_level, min_level)
    try:
        import bittensor as bt  # type: ignore
    except ImportError:
        logger.debug("Bittensor not installed; skipping bittensor logging configuration.")
        return
    except Exception:  # pragma: no cover - defensive guard for sandboxed envs
        logger.debug("Unable to import bittensor; skipping logging configuration.", exc_info=True)
        return

    try:
        bt.logging.set_debug(False)
        bt.logging.set_trace(False)
        bt.logging.set_info(True)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Unable to adjust bittensor logging verbosity: %s", exc)

    bt_logger = logging.getLogger("bittensor")
    bt_logger.setLevel(target_level)

    for handler in getattr(bt.logging, "_handlers", []):
        try:
            handler.setLevel(target_level)
        except Exception:  # pragma: no cover - defensive guard
            logger.debug("Failed to update bittensor handler log level.", exc_info=True)

# Apply at import time so early Bittensor usage stays at INFO.
_configure_bittensor_logging()

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="FastAPI backend for Autoppia Bittensor Leaderboard",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for images
images_path = os.path.join(os.path.dirname(__file__), "..", "images")
try:
    os.makedirs(images_path, exist_ok=True)
except OSError as exc:
    logger.warning(f"Unable to prepare images directory at {images_path}: {exc}")
else:
    app.mount("/images", StaticFiles(directory=images_path), name="images")
    logger.info(f"Mounted static files from {images_path}")


# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # Log request
    logger.info(f"{request.method} {request.url.path} - {request.client.host if request.client else 'unknown'}")
    
    # Process request
    response = await call_next(request)
    
    # Log response
    process_time = time.time() - start_time
    logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    
    return response


# Include routers
app.include_router(rounds_post_router)  # POST endpoints for data submission
app.include_router(validator_rounds_router)  # Progressive validator ingestion endpoints
app.include_router(cache_router)  # Cache management endpoints
app.include_router(rounds_router)  # rounds endpoints
app.include_router(legacy_rounds_router)  # legacy /rounds endpoints
app.include_router(agent_runs_router)  # agent run endpoints
app.include_router(evaluations_router)  # evaluation endpoints
app.include_router(tasks_router)  # task endpoints
app.include_router(agents_router)  # agent endpoints
app.include_router(miners_router)  # miner endpoints
app.include_router(overview_router)  # overview endpoints
app.include_router(miner_list_router)  # minimal miner list endpoints
app.include_router(subnets_router)  # subnet timeline endpoints
app.include_router(subnets_legacy_router)  # legacy subnet endpoint compatibility


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "version": "0.1.0"
    }


# Idempotency cache stats endpoint (for debugging)
@app.get("/debug/idempotency-stats")
async def idempotency_stats():
    """Get idempotency cache statistics (debug endpoint)."""
    return get_cache_stats()


# Cache control endpoints (for testing)
@app.get("/debug/cache-stats")
async def cache_stats():
    """Get API cache statistics (debug endpoint)."""
    from app.services.cache import api_cache
    return api_cache.get_stats()


@app.post("/debug/cache-disable")
async def disable_cache():
    """Disable API cache for testing (debug endpoint)."""
    from app.services.cache import api_cache
    api_cache.disable()
    return {"message": "Cache disabled", "disabled": True}


@app.post("/debug/cache-enable")
async def enable_cache():
    """Enable API cache (debug endpoint)."""
    from app.services.cache import api_cache
    api_cache.enable()
    return {"message": "Cache enabled", "disabled": False}


@app.post("/debug/cache-clear")
async def clear_cache():
    """Clear all cache entries (debug endpoint)."""
    from app.services.cache import api_cache
    cleared = api_cache.clear()
    return {"message": f"Cleared {cleared} cache entries", "cleared": cleared}


# Startup event
@app.on_event("startup")
async def on_startup():
    """Initialize the application on startup."""
    logger.info("Starting Autoppia IWA Platform API...")

    _configure_bittensor_logging()
    
    try:
        await init_db()
        logger.info("SQL schema ready")
        logger.info(f"API server ready on {settings.HOST}:{settings.PORT}")
        logger.info(f"API documentation available at /docs")
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise


# Shutdown event
@app.on_event("shutdown")
async def on_shutdown():
    """Clean up resources on shutdown."""
    logger.info("Shutting down Autoppia IWA Platform API...")
    
    try:
        pass
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal server error",
            "detail": "An unexpected error occurred"
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info"
    )

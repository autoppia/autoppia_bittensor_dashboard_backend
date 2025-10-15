from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import logging
import time
import os

from app.config import settings
from app.db.mongo import ensure_indexes, close_client
from app.api.ui.rounds_get import router as rounds_get_router
from app.api.validator.rounds_post import router as rounds_post_router
from app.api.ui.ui_root import router as ui_router
from app.api.ui.overview import router as overview_router
from app.api.ui.rounds_api import router as rounds_api_router
from app.api.ui.cache import router as cache_router
from app.api.ui.agents import router as agents_router
from app.api.ui.agent_runs import router as agent_runs_router
from app.api.ui.tasks import router as tasks_router
from app.api.ui.miners import router as miners_router
from app.api.ui.miner_list import router as miner_list_router
# Optimized routes
from app.api.ui.optimized_ui import router as optimized_ui_router
from app.api.ui.optimized_rounds_post import router as optimized_rounds_post_router
from app.api.validator.validator_round import router as validator_rounds_router
from app.api.ui.subnets import router as subnets_router
from app.services.idempotency import get_cache_stats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
if os.path.exists(images_path):
    app.mount("/images", StaticFiles(directory=images_path), name="images")
    logger.info(f"Mounted static files from {images_path}")
else:
    logger.warning(f"Images directory not found at {images_path}")


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
app.include_router(rounds_get_router)  # GET endpoints for data retrieval
app.include_router(rounds_post_router)  # POST endpoints for data submission
app.include_router(ui_router)  # UI endpoints for dashboard
app.include_router(overview_router, prefix="/api")  # Overview section endpoints
app.include_router(rounds_api_router, prefix="/api")  # Rounds section API endpoints
app.include_router(cache_router)  # Cache management endpoints
app.include_router(agents_router)  # Agents API endpoints
app.include_router(agent_runs_router, prefix="/api")  # Agent runs API endpoints
app.include_router(tasks_router)  # Tasks API endpoints
app.include_router(miners_router)  # Miners API endpoints
app.include_router(miner_list_router)  # Optimized miner list endpoints
# Optimized routers
app.include_router(optimized_ui_router)  # Optimized UI endpoints
app.include_router(optimized_rounds_post_router)  # Optimized POST endpoints
app.include_router(validator_rounds_router)  # Progressive validator ingestion endpoints
app.include_router(subnets_router)  # Subnet timeline endpoints


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
    logger.info("Starting Autoppia Leaderboard API...")
    
    try:
        # Skip MongoDB initialization for development with mock data
        # await ensure_indexes()
        logger.info("Skipping MongoDB initialization - using mock data")
        
        logger.info(f"API server ready on {settings.HOST}:{settings.PORT}")
        logger.info(f"API documentation available at /docs")
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {e}")
        raise


# Shutdown event
@app.on_event("shutdown")
async def on_shutdown():
    """Clean up resources on shutdown."""
    logger.info("Shutting down Autoppia Leaderboard API...")
    
    try:
        # Skip MongoDB cleanup for development with mock data
        # await close_client()
        logger.info("Skipping MongoDB cleanup - using mock data")
        
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

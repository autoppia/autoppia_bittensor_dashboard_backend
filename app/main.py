from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import time

from app.config import settings
from app.db.mongo import ensure_indexes, close_client
from app.api.routes.rounds_get import router as rounds_get_router
from app.api.routes.rounds_post import router as rounds_post_router
from app.api.routes.ui import router as ui_router
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


# Startup event
@app.on_event("startup")
async def on_startup():
    """Initialize the application on startup."""
    logger.info("Starting Autoppia Leaderboard API...")
    
    try:
        # Ensure MongoDB indexes are created
        await ensure_indexes()
        logger.info("MongoDB indexes ensured successfully")
        
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
        # Close MongoDB connection
        await close_client()
        logger.info("MongoDB connection closed")
        
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

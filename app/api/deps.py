from fastapi import Header, HTTPException, status
from app.config import settings
import logging

logger = logging.getLogger(__name__)


async def api_key_auth(authorization: str = Header(default="")):
    """Validate API key from Authorization header."""
    if not authorization.startswith("Bearer "):
        logger.warning("Invalid auth header format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid auth header format. Expected 'Bearer <token>'"
        )
    
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        logger.warning("Empty token in auth header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Empty token"
        )
    
    if token not in settings.API_KEYS:
        logger.warning(f"Invalid API key attempted: {token[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid API key"
        )
    
    logger.debug(f"Valid API key authenticated: {token[:8]}...")
    return token

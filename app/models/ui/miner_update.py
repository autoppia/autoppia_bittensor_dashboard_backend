"""
Models for miner update operations.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional
from app.utils.validation import validate_miner_image_url


class MinerImageUpdateRequest(BaseModel):
    """Request model for updating miner image."""
    imageUrl: str = Field(..., description="Miner image URL (must be valid URL or empty string)")

    @validator('imageUrl')
    def validate_image_url(cls, v):
        """Validate that imageUrl is a valid URL or empty string."""
        return validate_miner_image_url(v)


class MinerImageUpdateResponse(BaseModel):
    """Response model for miner image update."""
    success: bool = Field(True, description="Success status")
    message: str = Field(..., description="Response message")
    miner: Optional[dict] = Field(None, description="Updated miner data")
    error: Optional[str] = Field(None, description="Error message if failed")

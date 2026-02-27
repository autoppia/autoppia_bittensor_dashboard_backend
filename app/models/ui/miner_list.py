from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.utils.validation import validate_miner_image_url


class MinerListItem(BaseModel):
    """Minimal miner data for listing."""

    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    ranking: int = Field(..., description="Current ranking")
    score: float = Field(..., description="Score")
    isSota: bool = Field(..., description="Whether miner is SOTA (company agent)")
    imageUrl: str = Field(..., description="Miner image URL (must be valid URL or empty string)")

    @field_validator("imageUrl")
    @classmethod
    def validate_image_url(cls, value: str) -> str:
        """Validate that imageUrl is a valid URL or empty string."""
        return validate_miner_image_url(value)


class MinerListResponse(BaseModel):
    """Response model for miner list endpoint."""

    miners: List[MinerListItem] = Field(..., description="List of miners with minimal data")
    total: int = Field(..., description="Total number of miners")
    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Items per page")
    round: Optional[int] = Field(
        default=None,
        description="Round number used to build this list (None when using global aggregates)",
    )


class MinerDetail(BaseModel):
    """Complete miner data for detailed view."""

    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    hotkey: str = Field(..., description="Miner hotkey")
    imageUrl: str = Field(..., description="Miner image URL (must be valid URL or empty string)")
    githubUrl: Optional[str] = Field(None, description="GitHub repository URL")
    taostatsUrl: str = Field(..., description="Taostats URL")
    isSota: bool = Field(..., description="Whether miner is SOTA")
    status: str = Field(..., description="Miner status")
    description: Optional[str] = Field(None, description="Miner description")
    totalRuns: int = Field(..., description="Total number of runs")
    successfulRuns: int = Field(..., description="Number of successful runs")
    bestScore: float = Field(..., description="Best score achieved")
    successRate: float = Field(..., description="Success rate percentage")
    averageResponseTime: float = Field(..., description="Average response time in seconds")
    totalTasks: int = Field(..., description="Total number of tasks")
    completedTasks: int = Field(..., description="Number of completed tasks")
    lastSeen: str = Field(..., description="Last seen timestamp (ISO 8601)")
    createdAt: str = Field(..., description="Creation timestamp (ISO 8601)")
    updatedAt: str = Field(..., description="Last update timestamp (ISO 8601)")

    @field_validator("imageUrl")
    @classmethod
    def validate_detail_image_url(cls, value: str) -> str:
        """Validate that imageUrl is a valid URL or empty string."""
        return validate_miner_image_url(value)


class MinerDetailResponse(BaseModel):
    """Response model for miner detail endpoint."""

    miner: MinerDetail = Field(..., description="Complete miner details")

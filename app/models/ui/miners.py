from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.utils.validation import validate_miner_image_url


class MinerStatus(str, Enum):
    """Miner status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class RunStatus(str, Enum):
    """Run status enumeration."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskStatus(str, Enum):
    """Task status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityType(str, Enum):
    """Activity type enumeration."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    MINER_CREATED = "miner_created"
    MINER_UPDATED = "miner_updated"
    MINER_DEACTIVATED = "miner_deactivated"


class TimeRange(str, Enum):
    """Time range enumeration."""

    ONE_HOUR = "1h"
    TWENTY_FOUR_HOURS = "24h"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"
    ONE_YEAR = "1y"
    ALL = "all"


class Granularity(str, Enum):
    """Data granularity enumeration."""

    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


# Base Models
class Miner(BaseModel):
    """Miner model."""

    id: str = Field(..., description="Unique identifier (string representation of UID)")
    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    hotkey: str = Field(..., description="Miner hotkey")
    imageUrl: str = Field(..., description="Miner image URL (must be valid URL or empty string)")
    githubUrl: str | None = Field(None, description="GitHub repository URL")
    taostatsUrl: str = Field(..., description="Taostats URL")
    isSota: bool = Field(..., description="Whether miner is SOTA")
    status: MinerStatus = Field(..., description="Miner status")
    description: str | None = Field(None, description="Miner description")
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
    def validate_image_url(cls, v):
        """Validate that imageUrl is a valid URL or empty string."""
        return validate_miner_image_url(v)


class Task(BaseModel):
    """Task model."""

    taskId: str = Field(..., description="Task ID")
    website: str = Field(..., description="Website name")
    useCase: str = Field(..., description="Use case description")
    status: TaskStatus = Field(..., description="Task status")
    score: float = Field(..., description="Task score")
    duration: int = Field(..., description="Task duration in seconds")
    startTime: datetime = Field(..., description="Task start time")
    endTime: datetime | None = Field(None, description="Task end time")
    error: str | None = Field(None, description="Error message if failed")


class MinerRun(BaseModel):
    """Miner run model."""

    runId: str = Field(..., description="Run ID")
    agentId: str = Field(..., description="Agent/Miner ID")
    validatorId: str = Field(..., description="Validator ID")
    roundId: int = Field(..., description="Round ID")
    score: float = Field(..., description="Run score")
    ranking: int = Field(..., description="Rank in the round")
    status: RunStatus = Field(..., description="Run status")
    duration: int = Field(..., description="Duration in seconds")
    completedTasks: int = Field(..., description="Number of completed tasks")
    totalTasks: int = Field(..., description="Total tasks in the run")
    startTime: str = Field(..., description="Start time (ISO 8601)")
    endTime: str | None = Field(None, description="End time (ISO 8601)")
    createdAt: str = Field(..., description="Creation timestamp (ISO 8601)")


class MinerActivity(BaseModel):
    """Miner activity model."""

    id: str = Field(..., description="Activity ID")
    type: ActivityType = Field(..., description="Activity type")
    uid: int = Field(..., description="Miner UID")
    minerName: str = Field(..., description="Miner name")
    message: str = Field(..., description="Activity message")
    timestamp: datetime = Field(..., description="Activity timestamp")
    metadata: dict[str, Any] = Field(..., description="Activity metadata")


class ScoreDistribution(BaseModel):
    """Score distribution model."""

    excellent: int = Field(..., description="Number of excellent scores (>=0.9)")
    good: int = Field(..., description="Number of good scores (0.7-0.89)")
    average: int = Field(..., description="Number of average scores (0.5-0.69)")
    poor: int = Field(..., description="Number of poor scores (<0.5)")


class PerformanceTrend(BaseModel):
    """Performance trend model."""

    period: str = Field(..., description="Time period")
    score: float = Field(..., description="Average score for period (0-1)")
    successRate: float = Field(..., description="Success rate for period (0-100)")
    responseTime: float = Field(..., description="Average response time for period (seconds)")


class MinerPerformanceMetrics(BaseModel):
    """Miner performance metrics model."""

    uid: int = Field(..., description="Miner UID")
    timeRange: dict[str, str] = Field(..., description="Time range")
    totalRuns: int = Field(..., description="Total runs in period")
    successfulRuns: int = Field(..., description="Successful runs in period")
    failedRuns: int = Field(..., description="Failed runs in period")
    bestScore: float = Field(..., description="Best score in period")
    worstScore: float = Field(..., description="Worst score in period")
    successRate: float = Field(..., description="Success rate in period")
    averageResponseTime: float = Field(..., description="Average response time in period")
    totalTasks: int = Field(..., description="Total tasks in period")
    completedTasks: int = Field(..., description="Completed tasks in period")
    taskCompletionRate: float = Field(..., description="Task completion rate")
    scoreDistribution: ScoreDistribution = Field(..., description="Score distribution")
    performanceTrend: list[PerformanceTrend] = Field(..., description="Performance trend")


class TopMiner(BaseModel):
    """Top performing miner model."""

    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    score: float = Field(..., description="Miner score")


class MostActiveMiner(BaseModel):
    """Most active miner model."""

    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    runs: int = Field(..., description="Number of runs")


class PerformanceDistribution(BaseModel):
    """Performance distribution model."""

    excellent: int = Field(..., description="Number of excellent miners")
    good: int = Field(..., description="Number of good miners")
    average: int = Field(..., description="Number of average miners")
    poor: int = Field(..., description="Number of poor miners")


class MinerStatistics(BaseModel):
    """Miner statistics model."""

    totalMiners: int = Field(..., description="Total number of miners")
    activeMiners: int = Field(..., description="Number of active miners")
    inactiveMiners: int = Field(..., description="Number of inactive miners")
    sotaMiners: int = Field(..., description="Number of SOTA miners")
    regularMiners: int = Field(..., description="Number of regular miners")
    totalRuns: int = Field(..., description="Total number of runs")
    successfulRuns: int = Field(..., description="Number of successful runs")
    averageSuccessRate: float = Field(..., description="Average success rate")
    topPerformingMiner: TopMiner = Field(..., description="Top performing miner")
    mostActiveMiner: MostActiveMiner = Field(..., description="Most active miner")
    performanceDistribution: PerformanceDistribution = Field(..., description="Performance distribution")
    lastUpdated: datetime = Field(..., description="Last update timestamp")


class MinerComparisonMetrics(BaseModel):
    """Miner comparison metrics model."""

    successRate: float = Field(..., description="Success rate")
    averageResponseTime: float = Field(..., description="Average response time")
    totalRuns: int = Field(..., description="Total runs")
    ranking: int = Field(..., description="Ranking")


class MinerComparison(BaseModel):
    """Miner comparison model."""

    uid: int = Field(..., description="Miner UID")
    name: str = Field(..., description="Miner name")
    metrics: MinerComparisonMetrics = Field(..., description="Comparison metrics")


class ComparisonMetrics(BaseModel):
    """Comparison metrics model."""

    bestPerformer: int = Field(..., description="Best performing miner UID")
    mostReliable: int = Field(..., description="Most reliable miner UID")
    fastest: int = Field(..., description="Fastest miner UID")
    mostActive: int = Field(..., description="Most active miner UID")


class MinerComparisonResponse(BaseModel):
    """Miner comparison response model."""

    miners: list[MinerComparison] = Field(..., description="List of compared miners")
    comparisonMetrics: ComparisonMetrics = Field(..., description="Comparison metrics")
    timeRange: dict[str, str] = Field(..., description="Time range")


# Query Models
class MinerListQuery(BaseModel):
    """Miner list query model."""

    page: int = Field(1, ge=1, description="Page number")
    limit: int = Field(50, ge=1, le=100, description="Items per page")
    isSota: bool | None = Field(None, description="Filter by SOTA status")
    status: MinerStatus | None = Field(None, description="Filter by status")
    sortBy: str = Field("name", description="Sort field")
    sortOrder: str = Field("desc", description="Sort order")
    search: str | None = Field(None, description="Search term")


class MinerPerformanceQuery(BaseModel):
    """Miner performance query model."""

    timeRange: TimeRange = Field(TimeRange.SEVEN_DAYS, description="Time range")
    startDate: datetime | None = Field(None, description="Start date")
    endDate: datetime | None = Field(None, description="End date")
    granularity: Granularity = Field(Granularity.DAY, description="Data granularity")


class MinerRunsQuery(BaseModel):
    """Miner runs query model."""

    page: int = Field(1, ge=1, description="Page number")
    limit: int = Field(20, ge=1, le=100, description="Items per page")
    roundId: int | None = Field(None, description="Filter by round ID")
    validatorId: str | None = Field(None, description="Filter by validator ID")
    status: RunStatus | None = Field(None, description="Filter by status")
    sortBy: str = Field("startTime", description="Sort field")
    sortOrder: str = Field("desc", description="Sort order")
    startDate: datetime | None = Field(None, description="Filter runs after this date")
    endDate: datetime | None = Field(None, description="Filter runs before this date")


class MinerActivityQuery(BaseModel):
    """Miner activity query model."""

    limit: int = Field(20, ge=1, le=100, description="Number of activities")
    offset: int = Field(0, ge=0, description="Number of activities to skip")
    type: ActivityType | None = Field(None, description="Filter by activity type")
    since: datetime | None = Field(None, description="Filter activities after timestamp")


class AllMinerActivityQuery(BaseModel):
    """All miner activity query model."""

    limit: int = Field(20, ge=1, le=100, description="Number of activities")
    offset: int = Field(0, ge=0, description="Number of activities to skip")
    type: ActivityType | None = Field(None, description="Filter by activity type")
    since: datetime | None = Field(None, description="Filter activities after timestamp")
    uid: int | None = Field(None, description="Filter by specific miner UID")


class MinerCompareRequest(BaseModel):
    """Miner compare request model."""

    uids: list[int] = Field(..., description="List of miner UIDs to compare")
    timeRange: TimeRange | None = Field(TimeRange.SEVEN_DAYS, description="Time range")
    startDate: datetime | None = Field(None, description="Start date")
    endDate: datetime | None = Field(None, description="End date")
    metrics: list[str] = Field(["score", "successRate", "duration", "runs"], description="Metrics to compare")


# Response Models
class Pagination(BaseModel):
    """Pagination model."""

    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Items per page")
    total: int = Field(..., description="Total number of items")
    totalPages: int = Field(..., description="Total number of pages")


class MinerListResponse(BaseModel):
    """Miner list response model."""

    miners: list[Miner] = Field(..., description="List of miners")
    pagination: Pagination = Field(..., description="Pagination information")


class MinerDetailResponse(BaseModel):
    """Miner detail response model."""

    miner: Miner = Field(..., description="Miner details")


class MinerPerformanceResponse(BaseModel):
    """Miner performance response model."""

    performanceTrend: list[PerformanceTrend] = Field(..., description="Performance trend data")


class MinerRunsResponse(BaseModel):
    """Miner runs response model."""

    runs: list[MinerRun] = Field(..., description="List of runs")
    pagination: Pagination = Field(..., description="Pagination information")


class MinerRunDetailResponse(BaseModel):
    """Miner run detail response model."""

    run: MinerRun = Field(..., description="Run details")


class MinerActivityResponse(BaseModel):
    """Miner activity response model."""

    activities: list[MinerActivity] = Field(..., description="List of activities")
    total: int = Field(..., description="Total number of activities")


class MinerStatisticsResponse(BaseModel):
    """Miner statistics response model."""

    statistics: MinerStatistics = Field(..., description="Miner statistics")


class ErrorDetail(BaseModel):
    """Error detail model."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: dict[str, Any] = Field(default_factory=dict, description="Error details")


class APIResponse(BaseModel):
    """Standard API response model."""

    success: bool = Field(True, description="Success status")
    data: Any | None = Field(None, description="Response data")
    error: ErrorDetail | None = Field(None, description="Error information")

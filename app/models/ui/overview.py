"""
Overview section UI models for the AutoPPIA Bittensor Dashboard.
These models match the API specifications provided by the frontend team.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


# --- Base Response Models ---
class BaseResponse(BaseModel):
    """Base response model with success flag."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    code: Optional[str] = None


# --- Overview Metrics Models ---
class OverviewMetrics(BaseModel):
    """Overview dashboard metrics."""

    model_config = {"extra": "allow"}

    topMinerUid: Optional[int] = None
    topMinerName: Optional[str] = None
    totalWebsites: int
    totalValidators: int
    totalMiners: int
    currentRound: int
    metricsRound: int
    subnetVersion: str
    lastUpdated: str  # ISO timestamp


class OverviewMetricsResponse(BaseResponse):
    """Response model for overview metrics endpoint."""

    data: Optional[Dict[str, OverviewMetrics]] = None


# --- Validator Models ---
class ValidatorInfo(BaseModel):
    """Validator information for overview section."""

    id: str
    validatorUid: Optional[int] = None
    name: str
    hotkey: str
    icon: str
    currentTask: str
    currentWebsite: Optional[str] = None
    currentUseCase: Optional[str] = None
    status: str  # "Sending Tasks", "Evaluating", "Waiting", "Offline"
    totalTasks: int
    weight: float
    trust: float
    version: Optional[str] = None  # String to preserve full version like "10.1.0"
    lastSeen: str  # ISO timestamp
    uptime: float
    stake: int
    emission: int
    validatorRoundId: Optional[str] = None
    roundNumber: Optional[int] = None


class ValidatorsListResponse(BaseResponse):
    """Response model for validators list endpoint."""

    data: Optional[Dict[str, Any]] = (
        None  # Contains validators list, total, page, limit
    )


class ValidatorDetailResponse(BaseResponse):
    """Response model for validator detail endpoint."""

    data: Optional[Dict[str, ValidatorInfo]] = None


class ValidatorFilterItem(BaseModel):
    """Simplified validator info for dropdown filters."""

    id: str
    name: str
    hotkey: Optional[str] = None
    icon: Optional[str] = None
    status: Optional[str] = None


class ValidatorsFilterResponse(BaseResponse):
    """Response model for validator filter endpoint."""

    data: Optional[Dict[str, List[ValidatorFilterItem]]] = None


# --- Round Models ---
class RoundInfo(BaseModel):
    """Round information for overview section."""

    model_config = {"extra": "allow"}

    id: int
    startBlock: int
    endBlock: int
    current: bool
    startTime: str  # ISO timestamp
    endTime: Optional[str] = None  # ISO timestamp
    status: str  # "active", "finished", "pending", "evaluating_finished"
    totalTasks: int
    completedTasks: int


class CurrentRoundResponse(BaseResponse):
    """Response model for current round endpoint."""

    data: Optional[Dict[str, RoundInfo]] = None


class RoundsListResponse(BaseResponse):
    """Response model for rounds list endpoint."""

    data: Optional[Dict[str, Any]] = None  # Contains rounds list, currentRound, total


class RoundDetailResponse(BaseResponse):
    """Response model for round detail endpoint."""

    data: Optional[Dict[str, RoundInfo]] = None


# --- Leaderboard Models ---
class LeaderboardEntry(BaseModel):
    """Leaderboard entry for performance comparison."""

    round: int
    subnet36: float  # post_consensus_avg_reward (mantener por compatibilidad)
    post_consensus_reward: float  # post_consensus_avg_reward
    winnerUid: Optional[int] = None
    winnerName: Optional[str] = None
    openai_cua: Optional[float] = None
    anthropic_cua: Optional[float] = None
    browser_use: Optional[float] = None
    timestamp: str  # ISO timestamp
    post_consensus_eval_score: Optional[float] = None  # post_consensus_avg_eval_score
    post_consensus_eval_time: Optional[float] = None  # post_consensus_avg_eval_time
    # Campos legacy (mantener por compatibilidad)
    score: Optional[float] = None  # post_consensus_avg_eval_score (alias)
    time: Optional[float] = None  # post_consensus_avg_eval_time (alias)


class LeaderboardResponse(BaseResponse):
    """Response model for leaderboard endpoint."""

    data: Optional[Dict[str, Any]] = None  # Contains leaderboard list, total, timeRange


# --- Statistics Models ---
class SubnetStatistics(BaseModel):
    """Subnet statistics and network health metrics."""

    totalStake: int
    totalEmission: int
    averageTrust: float
    networkUptime: float
    activeValidators: int
    registeredMiners: int
    totalTasksCompleted: int
    averageTaskScore: float
    lastUpdated: str  # ISO timestamp


class StatisticsResponse(BaseResponse):
    """Response model for statistics endpoint."""

    data: Optional[Dict[str, SubnetStatistics]] = None


# --- Network Status Models ---
class NetworkStatus(BaseModel):
    """Network status information."""

    status: str  # "healthy", "degraded", "down"
    message: str
    lastChecked: str  # ISO timestamp
    activeValidators: int
    networkLatency: int


class NetworkStatusResponse(BaseResponse):
    """Response model for network status endpoint."""

    data: Optional[NetworkStatus] = None


# --- Recent Activity Models ---
class ActivityMetadata(BaseModel):
    """Activity metadata for recent activity feed."""

    validatorId: Optional[str] = None
    taskId: Optional[str] = None
    score: Optional[float] = None
    roundId: Optional[str] = None
    startBlock: Optional[int] = None


class RecentActivity(BaseModel):
    """Recent activity entry."""

    id: str
    type: str  # "task_completed", "validator_joined", "round_started", "round_ended", "miner_registered"
    message: str
    timestamp: str  # ISO timestamp
    metadata: ActivityMetadata


class RecentActivityResponse(BaseResponse):
    """Response model for recent activity endpoint."""

    data: Optional[Dict[str, Any]] = None  # Contains activities list, total


# --- Performance Trends Models ---
class PerformanceTrend(BaseModel):
    """Performance trend data point."""

    model_config = {"extra": "allow"}

    date: str
    totalTasks: int
    activeValidators: int


class PerformanceTrendsResponse(BaseResponse):
    """Response model for performance trends endpoint."""

    data: Optional[Dict[str, Any]] = None  # Contains trends list, period

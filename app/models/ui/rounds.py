from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class BaseResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    code: Optional[str] = None


# --- Round Models ---
class ValidatorRoundSummary(BaseModel):
    """Summary of a single validator's round contribution."""

    validatorRoundId: str
    validatorUid: Optional[int] = None
    validatorName: Optional[str] = None
    validatorHotkey: Optional[str] = None
    status: str
    startTime: str
    endTime: Optional[str] = None
    totalTasks: int
    completedTasks: int
    icon: Optional[str] = None
    agentEvaluationRuns: Optional[List[Dict[str, Any]]] = None
    roundData: Optional[Dict[str, Any]] = None


class RoundInfo(BaseModel):
    """Round information model."""

    id: int
    round: Optional[int] = None
    roundNumber: Optional[int] = None
    roundKey: Optional[str] = None
    startBlock: int
    endBlock: int
    current: bool
    startTime: str  # ISO timestamp
    endTime: Optional[str] = None  # ISO timestamp
    status: str  # active, finished, pending, evaluating_finished
    totalTasks: int
    completedTasks: int
    currentBlock: int
    blocksRemaining: int
    progress: float
    validatorRounds: List[ValidatorRoundSummary] = Field(default_factory=list)


class RoundStatistics(BaseModel):
    """Round statistics model."""

    roundId: int
    totalMiners: int
    activeMiners: int
    totalTasks: int
    completedTasks: int
    totalValidators: int = Field(default=0)
    averageTasksPerValidator: float = Field(default=0.0)
    winnerMinerUid: Optional[int] = Field(default=None)
    successRate: float
    totalStake: int
    totalEmission: int
    lastUpdated: str  # ISO timestamp


class MinerPerformance(BaseModel):
    """Miner performance in a round."""

    uid: int
    hotkey: str
    success: bool
    score: float
    duration: float
    ranking: int
    tasksCompleted: int
    tasksTotal: int
    stake: int
    emission: int
    lastSeen: str  # ISO timestamp
    validatorId: str


class ValidatorPerformance(BaseModel):
    """Validator performance in a round."""

    id: str
    name: str
    hotkey: str
    icon: str
    status: str
    totalTasks: int
    completedTasks: int
    totalMiners: int = Field(default=0)
    activeMiners: int = Field(default=0)
    weight: int
    trust: float
    version: int
    stake: int
    emission: int
    lastSeen: str  # ISO timestamp
    uptime: float


class ActivityItem(BaseModel):
    """Activity feed item."""

    id: str
    type: str
    message: str
    timestamp: str  # ISO timestamp
    metadata: Dict[str, Any]


class TimeRemaining(BaseModel):
    """Time remaining breakdown."""

    days: int
    hours: int
    minutes: int
    seconds: int


class RoundProgress(BaseModel):
    """Round progress information."""

    roundId: int
    currentBlock: int
    startBlock: int
    endBlock: int
    # Chain-derived epoch fields (optional in legacy responses)
    startEpoch: float | None = None
    endEpoch: float | None = None
    currentEpoch: float | None = None
    blocksRemaining: int
    progress: float
    estimatedTimeRemaining: TimeRemaining
    lastUpdated: str  # ISO timestamp
    status: str  # active, finished, pending, evaluating_finished
    nextRound: int | None = None  # Número del siguiente round
    previousRound: int | None = None  # Número del round anterior


class RoundSummary(BaseModel):
    """Quick round summary."""

    roundId: int
    status: str
    progress: float
    totalMiners: int
    timeRemaining: str


class TimelinePoint(BaseModel):
    """Timeline data point."""

    model_config = {"extra": "allow"}

    timestamp: str  # ISO timestamp
    block: int
    completedTasks: int
    activeMiners: int


class TopMiner(BaseModel):
    """Top miner in comparison."""

    uid: int
    score: float
    ranking: int


class RoundComparison(BaseModel):
    """Round comparison data."""

    roundId: int
    statistics: RoundStatistics
    topMiners: List[TopMiner]


# --- Response Models ---
class RoundsListResponse(BaseResponse):
    """Response model for rounds list endpoint."""

    data: Optional[Dict[str, Any]] = None


class RoundDetailResponse(BaseResponse):
    """Response model for round detail endpoint."""

    data: Optional[Dict[str, RoundInfo]] = None


class RoundStatisticsResponse(BaseResponse):
    """Response model for round statistics endpoint."""

    data: Optional[Dict[str, RoundStatistics]] = None


class RoundMinersResponse(BaseResponse):
    """Response model for round miners endpoint."""

    data: Optional[Dict[str, Any]] = None


class RoundValidatorsResponse(BaseResponse):
    """Response model for round validators endpoint."""

    data: Optional[Dict[str, Any]] = None


class RoundActivityResponse(BaseResponse):
    """Response model for round activity endpoint."""

    data: Optional[Dict[str, Any]] = None


class RoundProgressResponse(BaseResponse):
    """Response model for round progress endpoint."""

    data: Optional[Dict[str, RoundProgress]] = None


class RoundSummaryResponse(BaseResponse):
    """Response model for round summary endpoint."""

    data: Optional[RoundSummary] = None


class RoundComparisonRequest(BaseModel):
    """Request model for round comparison."""

    roundIds: List[int]


class RoundComparisonResponse(BaseResponse):
    """Response model for round comparison endpoint."""

    data: Optional[Dict[str, List[RoundComparison]]] = None


class RoundTimelineResponse(BaseResponse):
    """Response model for round timeline endpoint."""

    data: Optional[Dict[str, List[TimelinePoint]]] = None

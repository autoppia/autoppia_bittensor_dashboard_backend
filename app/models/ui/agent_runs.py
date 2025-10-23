"""
Agent Runs API models for the AutoPPIA Bittensor Dashboard.
These models define the data structures for agent evaluation runs.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from enum import Enum


# --- Enums ---
class RunStatus(str, Enum):
    """Agent run status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    """Task status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class LogLevel(str, Enum):
    """Log level enumeration."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class EventType(str, Enum):
    """Event type enumeration."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"


# --- Core Models ---
class Website(BaseModel):
    """Website performance data."""

    website: str
    tasks: int
    successful: int
    failed: int
    score: float


class Action(BaseModel):
    """Action performed during task execution."""

    id: str
    type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    timestamp: str
    duration: float
    success: bool


class Task(BaseModel):
    """Task execution details."""

    taskId: str
    website: str
    useCase: str
    prompt: str
    status: TaskStatus
    score: float
    duration: int
    startTime: str
    endTime: Optional[str] = None
    actions: List[Action] = Field(default_factory=list)
    screenshots: List[str] = Field(default_factory=list)
    logs: List[str] = Field(default_factory=list)


class AgentRun(BaseModel):
    """Agent evaluation run details."""

    runId: str
    agentId: str
    agentUid: Optional[int] = None
    agentHotkey: Optional[str] = None
    agentName: Optional[str] = None
    roundId: int
    validatorId: str
    validatorName: str
    validatorImage: str
    startTime: str
    endTime: str
    status: RunStatus
    totalTasks: int
    completedTasks: int
    successfulTasks: int
    failedTasks: int
    score: float
    ranking: int
    duration: int
    overallScore: int
    averageEvaluationTime: Optional[float] = Field(
        default=None,
        description="Average evaluation duration recorded for the run (seconds)",
    )
    websites: List[Website] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RoundInfo(BaseModel):
    """Round information for personas."""

    id: int
    name: str
    status: str
    startTime: str
    endTime: Optional[str] = None


class ValidatorInfo(BaseModel):
    """Validator information for personas."""

    id: str
    name: str
    image: str
    description: str
    website: str
    github: str


class AgentInfo(BaseModel):
    """Agent information for personas."""

    id: str
    uid: Optional[int] = None
    hotkey: Optional[str] = None
    name: str
    type: str
    image: str
    description: str


class Personas(BaseModel):
    """Personas data (round, validator, agent information)."""

    round: RoundInfo
    validator: ValidatorInfo
    agent: AgentInfo


class ScoreDistribution(BaseModel):
    """Score distribution data."""

    excellent: int
    good: int
    average: int
    poor: int


class PerformanceByWebsite(BaseModel):
    """Performance data by website."""

    website: str
    tasks: int
    successful: int
    failed: int
    averageScore: float
    averageDuration: float


class PerformanceByUseCase(BaseModel):
    """Performance data by use case."""

    useCase: str
    tasks: int
    successful: int
    failed: int
    averageScore: float
    averageDuration: float


class Statistics(BaseModel):
    """Detailed statistics for an agent run."""

    runId: str
    overallScore: int
    totalTasks: int
    successfulTasks: int
    failedTasks: int
    websites: int
    averageTaskDuration: float
    successRate: float
    scoreDistribution: ScoreDistribution
    performanceByWebsite: List[PerformanceByWebsite]
    performanceByUseCase: List[PerformanceByUseCase]


class TopPerformingWebsite(BaseModel):
    """Top performing website data."""

    website: str
    score: float
    tasks: int


class TopPerformingUseCase(BaseModel):
    """Top performing use case data."""

    useCase: str
    score: float
    tasks: int


class RecentActivity(BaseModel):
    """Recent activity data."""

    timestamp: str
    action: str
    details: str


class Summary(BaseModel):
    """Summary information for an agent run."""

    runId: str
    agentId: str
    agentUid: Optional[int] = None
    agentHotkey: Optional[str] = None
    agentName: Optional[str] = None
    roundId: int
    validatorId: str
    startTime: str
    endTime: Optional[str] = None
    status: RunStatus
    overallScore: int
    totalTasks: int
    successfulTasks: int
    failedTasks: int
    duration: int
    ranking: int
    topPerformingWebsite: TopPerformingWebsite
    topPerformingUseCase: TopPerformingUseCase
    recentActivity: List[RecentActivity]


class Event(BaseModel):
    """Timeline event data."""

    timestamp: str
    type: EventType
    message: str
    taskId: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Log(BaseModel):
    """Log entry data."""

    timestamp: str
    level: LogLevel
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Metric(BaseModel):
    """Performance metric data."""

    timestamp: str
    value: float


class Metrics(BaseModel):
    """Performance metrics for an agent run."""

    cpu: List[Metric]
    memory: List[Metric]
    network: List[Metric]
    duration: int
    peakCpu: float
    peakMemory: float
    totalNetworkTraffic: int


class Comparison(BaseModel):
    """Agent run comparison data."""

    bestScore: str
    fastest: str
    mostTasks: str
    bestSuccessRate: str


# --- Request/Response Models ---
class AgentRunDetailResponse(BaseModel):
    """Response model for agent run details."""

    success: bool
    data: Optional[Dict[str, AgentRun]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class PersonasResponse(BaseModel):
    """Response model for personas data."""

    success: bool
    data: Optional[Dict[str, Personas]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class StatisticsResponse(BaseModel):
    """Response model for statistics data."""

    success: bool
    data: Optional[Dict[str, Statistics]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class SummaryResponse(BaseModel):
    """Response model for summary data."""

    success: bool
    data: Optional[Dict[str, Summary]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class TasksResponse(BaseModel):
    """Response model for tasks data."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class AgentRunsListResponse(BaseModel):
    """Response model for agent runs list."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class ComparisonRequest(BaseModel):
    """Request model for comparing agent runs."""

    runIds: List[str]


class ComparisonResponse(BaseModel):
    """Response model for agent run comparison."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class TimelineResponse(BaseModel):
    """Response model for timeline data."""

    success: bool
    data: Optional[Dict[str, List[Event]]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class LogsResponse(BaseModel):
    """Response model for logs data."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    code: Optional[str] = None


class MetricsResponse(BaseModel):
    """Response model for metrics data."""

    success: bool
    data: Optional[Dict[str, Metrics]] = None
    error: Optional[str] = None
    code: Optional[str] = None

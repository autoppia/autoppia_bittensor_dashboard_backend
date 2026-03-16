"""
Agent Runs API models for the AutoPPIA Bittensor Dashboard.
These models define the data structures for agent evaluation runs.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


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
    reward: float


class Action(BaseModel):
    """Action performed during task execution."""

    id: str
    type: str
    # Accept legacy string selectors and new structured selector objects
    selector: Optional[Union[str, Dict[str, Any]]] = None
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
    duration: float
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
    endTime: Optional[str] = ""
    status: RunStatus
    totalTasks: int
    tasksAttempted: Optional[int] = None
    completedTasks: int
    successfulTasks: int
    failedTasks: int
    score: float
    duration: int
    overallReward: float
    averageEvaluationTime: Optional[float] = Field(
        default=None,
        description="Average evaluation duration recorded for the run (seconds)",
    )
    avgCostPerTask: Optional[float] = Field(
        default=None,
        description="Average LLM cost per evaluated task (USD)",
    )
    totalWebsites: int = Field(default=0, description="Total number of unique websites in this run")
    websites: List[Website] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    zeroReason: Optional[str] = Field(
        default=None,
        description="Reason for reward 0 when applicable (e.g. over_cost_limit, deploy_failed, task_failed)",
    )
    earlyStopReason: Optional[str] = Field(default=None)
    earlyStopMessage: Optional[str] = Field(default=None)


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
    averageDuration: float
    useCases: List["PerformanceByUseCase"] = Field(default_factory=list)


class PerformanceByUseCase(BaseModel):
    """Performance data by use case."""

    useCase: str
    tasks: int
    successful: int
    failed: int
    averageDuration: float


class Statistics(BaseModel):
    """Detailed statistics for an agent run."""

    runId: str
    overallReward: float
    totalTasks: int
    tasksAttempted: Optional[int] = None
    successfulTasks: int
    failedTasks: int
    websites: int
    averageTaskDuration: float
    successRate: float
    earlyStopReason: Optional[str] = None
    earlyStopMessage: Optional[str] = None
    scoreDistribution: ScoreDistribution
    performanceByWebsite: List[PerformanceByWebsite]


class TopPerformingWebsite(BaseModel):
    """Top performing website data."""

    website: str
    averageEvalScore: float
    tasks: int


class TopPerformingUseCase(BaseModel):
    """Top performing use case data."""

    useCase: str
    averageEvalScore: float
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
    overallReward: float
    totalTasks: int
    successfulTasks: int
    failedTasks: int
    duration: int
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

    bestReward: str
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


AgentRunsListResponse.model_rebuild()
AgentRunDetailResponse.model_rebuild()
PersonasResponse.model_rebuild()
StatisticsResponse.model_rebuild()
SummaryResponse.model_rebuild()
TasksResponse.model_rebuild()
ComparisonResponse.model_rebuild()
TimelineResponse.model_rebuild()
LogsResponse.model_rebuild()
MetricsResponse.model_rebuild()

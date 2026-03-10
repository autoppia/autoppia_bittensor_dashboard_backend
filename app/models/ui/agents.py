from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Sonar S1192: shared Field description literals
DESC_AGENT_NAME = "Agent name"
DESC_TOTAL_NUMBER_OF_RUNS = "Total number of runs"
DESC_AGENT_IDENTIFIER = "Agent identifier"
DESC_ROUND_IDENTIFIER = "Round identifier"
DESC_ITEMS_PER_PAGE = "Items per page"


# --- Enums ---
class AgentType(str, Enum):
    """Agent type enumeration."""

    AUTOPPIA = "autoppia"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    BROWSER_USE = "browser-use"
    CUSTOM = "custom"


class AgentStatus(str, Enum):
    """Agent status enumeration."""

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
    AGENT_CREATED = "agent_created"
    AGENT_UPDATED = "agent_updated"
    AGENT_DEACTIVATED = "agent_deactivated"


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


# --- Core Models ---
class Agent(BaseModel):
    """Agent model representing an AI agent in the system."""

    id: str = Field(..., description="Unique agent identifier")
    uid: int | None = Field(None, description="Miner UID")
    name: str = Field(..., description=DESC_AGENT_NAME)
    hotkey: str | None = Field(None, description="Miner hotkey")
    type: AgentType = Field(..., description="Agent type")
    imageUrl: str = Field(..., description="URL to agent image/icon")
    githubUrl: str | None = Field(None, description="GitHub repository URL")
    taostatsUrl: str | None = Field(None, description="Taostats URL")
    isSota: bool | None = Field(None, description="Whether agent is SOTA")
    description: str | None = Field(None, description="Agent description")
    version: str | None = Field(None, description="Agent version")
    status: AgentStatus = Field(..., description="Agent status")
    totalRuns: int = Field(default=0, description=DESC_TOTAL_NUMBER_OF_RUNS)
    successfulRuns: int = Field(default=0, description="Number of successful runs")
    currentScore: float = Field(default=0.0, description="Current score (renamed from average score)")
    currentTopScore: float = Field(default=0.0, description="Current top score (renamed from best score)")
    currentRank: int = Field(default=0, description="Current rank")
    bestRankEver: int = Field(default=0, description="Best rank ever achieved")
    bestRankRoundId: int = Field(default=0, description="Round where best rank occurred")
    roundsParticipated: int = Field(default=0, description="Number of rounds participated")
    roundsWon: int = Field(default=0, description="Number of rounds won (global winner)")
    alphaWonInPrizes: float = Field(default=0.0, description="Alpha won in prizes")
    taoWonInPrizes: float = Field(default=0.0, description="TAO won in prizes (derived)")
    bestRoundScore: float = Field(default=0.0, description="Best average score achieved across all rounds")
    bestRoundId: int = Field(default=0, description="Round number where best score was achieved")
    averageResponseTime: float = Field(default=0.0, description="Average response time in seconds")
    totalTasks: int = Field(default=0, description="Total number of tasks")
    completedTasks: int = Field(default=0, description="Number of completed tasks")
    lastSeen: datetime = Field(..., description="Last seen timestamp")
    createdAt: datetime = Field(..., description="Creation timestamp")
    updatedAt: datetime = Field(..., description="Last update timestamp")


class Task(BaseModel):
    """Task model representing a single task within a run."""

    taskId: str = Field(..., description="Unique task identifier")
    website: str = Field(..., description="Target website")
    useCase: str = Field(..., description="Use case description")
    status: TaskStatus = Field(..., description="Task status")
    score: float = Field(default=0.0, description="Task score")
    duration: int = Field(default=0, description="Task duration in seconds")
    startTime: datetime = Field(..., description="Task start time")
    endTime: datetime | None = Field(None, description="Task end time")
    error: str | None = Field(None, description="Error message if failed")


class AgentRun(BaseModel):
    """Agent run model representing a single execution of an agent."""

    runId: str = Field(..., description="Unique run identifier")
    agentId: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    roundId: int = Field(..., description=DESC_ROUND_IDENTIFIER)
    validatorId: str = Field(..., description="Validator identifier")
    startTime: datetime = Field(..., description="Run start time")
    endTime: datetime | None = Field(None, description="Run end time")
    status: RunStatus = Field(..., description="Run status")
    totalTasks: int = Field(default=0, description="Total number of tasks")
    completedTasks: int = Field(default=0, description="Number of completed tasks")
    score: float = Field(default=0.0, description="Run score")
    duration: int = Field(default=0, description="Run duration in seconds")
    ranking: int | None = Field(None, description="Run ranking")
    tasks: list[Task] = Field(default_factory=list, description="List of tasks")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Run metadata")


class AgentActivity(BaseModel):
    """Agent activity model representing system events."""

    id: str = Field(..., description="Unique activity identifier")
    type: ActivityType = Field(..., description="Activity type")
    agentId: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    agentName: str = Field(..., description=DESC_AGENT_NAME)
    message: str = Field(..., description="Activity message")
    timestamp: datetime = Field(..., description="Activity timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Activity metadata")


# --- Performance Metrics Models ---
class ScoreDistribution(BaseModel):
    """Score distribution model."""

    excellent: int = Field(default=0, description="Number of excellent scores (0.9-1.0)")
    good: int = Field(default=0, description="Number of good scores (0.7-0.89)")
    average: int = Field(default=0, description="Number of average scores (0.5-0.69)")
    poor: int = Field(default=0, description="Number of poor scores (0.0-0.49)")


class PerformanceTrend(BaseModel):
    """Performance trend data point keyed by round number."""

    round: int = Field(..., description="Round number represented by this data point")
    score: float = Field(..., description="Average score for the round")
    responseTime: float | None = Field(default=None, description="Average response time recorded for the round")
    successRate: float | None = Field(default=None, description="Success rate percentage recorded for the round")


class ScoreRoundDataPoint(BaseModel):
    """Score vs round data point."""

    round_id: int = Field(..., description=DESC_ROUND_IDENTIFIER)
    score: float = Field(..., description="Score achieved in this round")
    rank: int | None = Field(None, description="Rank in this round")
    reward: float | None = Field(None, description="Reward received in this round")
    timestamp: datetime = Field(..., description="Round timestamp")
    benchmarks: list[dict[str, Any]] | None = Field(default=None, description="Benchmark scores recorded during this round")


class AgentPerformanceMetrics(BaseModel):
    """Agent performance metrics model."""

    agentId: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    timeRange: dict[str, str] = Field(..., description="Time range with start and end")
    totalRuns: int = Field(default=0, description="Total runs in time range")
    successfulRuns: int = Field(default=0, description="Successful runs in time range")
    failedRuns: int = Field(default=0, description="Failed runs in time range")
    successRate: float = Field(default=0.0, description="Success rate percentage")
    currentScore: float = Field(default=0.0, description="Current score")
    worstScore: float = Field(default=0.0, description="Worst score")
    averageResponseTime: float = Field(default=0.0, description="Average response time")
    totalTasks: int = Field(default=0, description="Total tasks")
    completedTasks: int = Field(default=0, description="Completed tasks")
    taskCompletionRate: float = Field(default=0.0, description="Task completion rate")
    scoreDistribution: ScoreDistribution = Field(default_factory=ScoreDistribution, description="Score distribution")
    performanceTrend: list[PerformanceTrend] = Field(default_factory=list, description="Performance trend data")


class AgentRoundMetrics(BaseModel):
    """Round-specific metrics for an agent."""

    roundId: int = Field(..., description=DESC_ROUND_IDENTIFIER)
    score: float = Field(..., description="Average score achieved in the round")
    rank: int | None = Field(None, description="Agent rank within the round leaderboard")
    totalRuns: int = Field(default=0, description="Number of validator runs for the agent in the round")
    totalValidators: int = Field(default=0, description="Number of validators that evaluated the agent in the round")
    validatorUids: list[int] = Field(default_factory=list, description="Validator UIDs that evaluated the agent")
    validators: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Validator metadata (uid, hotkey, name) for the round",
    )
    totalTasks: int = Field(default=0, description="Total tasks attempted in the round")
    completedTasks: int = Field(default=0, description="Tasks completed successfully in the round")
    failedTasks: int = Field(default=0, description="Tasks failed in the round")
    successRate: float = Field(default=0.0, description="Success rate (0-1) for the round")
    averageResponseTime: float = Field(
        default=0.0,
        description="Average response time across validator runs in the round (seconds)",
    )


# --- Comparison Models ---
class AgentComparisonMetrics(BaseModel):
    """Agent comparison metrics model."""

    currentScore: float = Field(default=0.0, description="Average score")
    currentTopScore: float = Field(default=0.0, description="Top benchmark score")
    successRate: float = Field(default=0.0, description="Success rate percentage")
    averageResponseTime: float = Field(default=0.0, description="Average response time")
    totalRuns: int = Field(default=0, description="Total runs")
    currentRank: int = Field(default=0, description="Overall ranking")


class AgentComparison(BaseModel):
    """Agent comparison model."""

    agentId: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    name: str = Field(..., description=DESC_AGENT_NAME)
    metrics: AgentComparisonMetrics = Field(..., description="Agent metrics")


class ComparisonMetrics(BaseModel):
    """Comparison summary metrics."""

    bestPerformer: str = Field(..., description="Best performing agent ID")
    mostReliable: str = Field(..., description="Most reliable agent ID")
    fastest: str = Field(..., description="Fastest agent ID")
    mostActive: str = Field(..., description="Most active agent ID")


class AgentComparisonResponse(BaseModel):
    """Agent comparison response model."""

    agents: list[AgentComparison] = Field(..., description="List of compared agents")
    comparisonMetrics: ComparisonMetrics = Field(..., description="Comparison summary")
    timeRange: dict[str, str] = Field(..., description="Time range")


# --- Statistics Models ---
class TopAgent(BaseModel):
    """Top agent model."""

    id: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    name: str = Field(..., description=DESC_AGENT_NAME)
    score: float = Field(..., description="Agent score")


class MostActiveAgent(BaseModel):
    """Most active agent model."""

    id: str = Field(..., description=DESC_AGENT_IDENTIFIER)
    name: str = Field(..., description=DESC_AGENT_NAME)
    runs: int = Field(..., description="Number of runs")


class PerformanceDistribution(BaseModel):
    """Performance distribution model."""

    excellent: int = Field(default=0, description="Number of excellent agents")
    good: int = Field(default=0, description="Number of good agents")
    average: int = Field(default=0, description="Number of average agents")
    poor: int = Field(default=0, description="Number of poor agents")


class AgentStatistics(BaseModel):
    """Agent statistics model."""

    totalAgents: int = Field(default=0, description="Total number of agents")
    activeAgents: int = Field(default=0, description="Number of active agents")
    inactiveAgents: int = Field(default=0, description="Number of inactive agents")
    totalRuns: int = Field(default=0, description=DESC_TOTAL_NUMBER_OF_RUNS)
    successfulRuns: int = Field(default=0, description="Number of successful runs")
    averageSuccessRate: float = Field(default=0.0, description="Average success rate")
    averageCurrentScore: float = Field(default=0.0, description="Average current score")
    topPerformingAgent: TopAgent = Field(..., description="Top performing agent")
    mostActiveAgent: MostActiveAgent = Field(..., description="Most active agent")
    performanceDistribution: PerformanceDistribution = Field(default_factory=PerformanceDistribution, description="Performance distribution")
    lastUpdated: datetime = Field(..., description="Last update timestamp")


# --- Request/Response Models ---
class AgentListResponse(BaseModel):
    """Agent list response model."""

    agents: list[Agent] = Field(..., description="List of agents")
    total: int = Field(..., description="Total number of agents")
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description=DESC_ITEMS_PER_PAGE)


class AgentDetailResponse(BaseModel):
    """Agent detail response model."""

    agent: Agent = Field(..., description="Agent details")
    scoreRoundData: list[ScoreRoundDataPoint] = Field(default_factory=list, description="Score vs round data points")
    availableRounds: list[int] = Field(
        default_factory=list,
        description="Rounds where the agent participated",
    )
    roundMetrics: AgentRoundMetrics | None = Field(
        default=None,
        description="Round-specific metrics for the selected round",
    )


class AgentPerformanceResponse(BaseModel):
    """Agent performance response model."""

    metrics: AgentPerformanceMetrics = Field(..., description="Performance metrics")


class AgentRunsResponse(BaseModel):
    """Agent runs response model."""

    runs: list[AgentRun] = Field(..., description="List of runs")
    total: int = Field(..., description=DESC_TOTAL_NUMBER_OF_RUNS)
    page: int = Field(..., description="Current page number")
    limit: int = Field(..., description=DESC_ITEMS_PER_PAGE)
    availableRounds: list[int] = Field(default_factory=list, description="Rounds that include runs for the agent")
    selectedRound: int | None = Field(default=None, description="Round currently selected")


class AgentRunDetailResponse(BaseModel):
    """Agent run detail response model."""

    run: AgentRun = Field(..., description="Run details")


class AgentActivityResponse(BaseModel):
    """Agent activity response model."""

    activities: list[AgentActivity] = Field(..., description="List of activities")
    total: int = Field(..., description="Total number of activities")


class AgentStatisticsResponse(BaseModel):
    """Agent statistics response model."""

    statistics: AgentStatistics = Field(..., description="Agent statistics")


class AgentCompareRequest(BaseModel):
    """Agent comparison request model."""

    agentIds: list[str] = Field(..., description="List of agent IDs to compare")
    timeRange: TimeRange | None = Field(None, description="Time range for comparison")
    startDate: datetime | None = Field(None, description="Start date for comparison")
    endDate: datetime | None = Field(None, description="End date for comparison")
    metrics: list[str] = Field(default_factory=list, description="Metrics to compare")


# --- Standard API Response Model ---
class APIResponse(BaseModel):
    """Standard API response model."""

    data: Any | None = Field(None, description="Response data")
    success: bool = Field(..., description="Success status")
    message: str | None = Field(None, description="Success message")
    error: str | None = Field(None, description="Error message")


# --- Query Parameter Models ---
class AgentListQuery(BaseModel):
    """Agent list query parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    limit: int = Field(default=20, ge=1, le=100, description=DESC_ITEMS_PER_PAGE)
    type: AgentType | None = Field(None, description="Filter by agent type")
    status: AgentStatus | None = Field(None, description="Filter by status")
    sortBy: str = Field(default="name", description="Sort field")
    sortOrder: str = Field(default="asc", description="Sort order")
    search: str | None = Field(None, description="Search term")


class AgentPerformanceQuery(BaseModel):
    """Agent performance query parameters."""

    timeRange: TimeRange = Field(default=TimeRange.SEVEN_DAYS, description="Time range")
    startDate: datetime | None = Field(None, description="Start date")
    endDate: datetime | None = Field(None, description="End date")
    granularity: Granularity = Field(default=Granularity.DAY, description="Data granularity")


class AgentRunsQuery(BaseModel):
    """Agent runs query parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    limit: int = Field(default=20, ge=1, le=100, description=DESC_ITEMS_PER_PAGE)
    roundId: int | None = Field(None, description="Filter by round ID")
    validatorId: str | None = Field(None, description="Filter by validator ID")
    status: RunStatus | None = Field(None, description="Filter by status")
    sortBy: str = Field(default="startTime", description="Sort field")
    sortOrder: str = Field(default="desc", description="Sort order")
    startDate: datetime | None = Field(None, description="Start date filter")
    endDate: datetime | None = Field(None, description="End date filter")


class AgentActivityQuery(BaseModel):
    """Agent activity query parameters."""

    limit: int = Field(default=20, ge=1, le=100, description="Number of activities")
    offset: int = Field(default=0, ge=0, description="Number of activities to skip")
    type: ActivityType | None = Field(None, description="Filter by activity type")
    since: datetime | None = Field(None, description="Filter activities after timestamp")


class AllAgentActivityQuery(BaseModel):
    """All agent activity query parameters."""

    limit: int = Field(default=20, ge=1, le=100, description="Number of activities")
    offset: int = Field(default=0, ge=0, description="Number of activities to skip")
    type: ActivityType | None = Field(None, description="Filter by activity type")
    since: datetime | None = Field(None, description="Filter activities after timestamp")
    agentId: str | None = Field(None, description="Filter by specific agent ID")

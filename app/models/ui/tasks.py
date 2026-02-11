"""
Task models for AutoPPIA Bittensor Dashboard
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    INPUT = "input"
    SEARCH = "search"
    EXTRACT = "extract"
    SUBMIT = "submit"
    OPEN_TAB = "open_tab"
    CLOSE_TAB = "close_tab"
    WAIT = "wait"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    OTHER = "other"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class TaskAction(BaseModel):
    """Model for task actions"""

    id: str = Field(..., description="Unique action identifier")
    type: ActionType = Field(..., description="Type of action")
    # Accept legacy string selectors and new structured selector objects
    selector: Optional[Union[str, Dict[str, Any]]] = Field(
        None, description="CSS selector (string) or structured selector object"
    )
    value: Optional[str] = Field(None, description="Value to input or navigate to")
    timestamp: datetime = Field(..., description="When the action was performed")
    duration: float = Field(..., description="Duration of the action in seconds")
    success: bool = Field(..., description="Whether the action was successful")
    error: Optional[str] = Field(None, description="Error message if action failed")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional action metadata"
    )
    screenshot: Optional[str] = Field(
        None, description="Screenshot filename if available"
    )


class TaskScreenshot(BaseModel):
    """Model for task screenshots"""

    id: str = Field(..., description="Unique screenshot identifier")
    url: str = Field(..., description="URL to access the screenshot")
    timestamp: datetime = Field(..., description="When the screenshot was taken")
    actionId: Optional[str] = Field(None, description="Associated action ID")
    description: Optional[str] = Field(
        None, description="Description of the screenshot"
    )


class TaskLog(BaseModel):
    """Model for task logs"""

    timestamp: datetime = Field(..., description="When the log entry was created")
    level: LogLevel = Field(..., description="Log level")
    message: str = Field(..., description="Log message")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional log metadata"
    )


class Viewport(BaseModel):
    """Model for browser viewport"""

    width: int = Field(..., description="Viewport width in pixels")
    height: int = Field(..., description="Viewport height in pixels")


class Resources(BaseModel):
    """Model for system resources"""

    cpu: float = Field(..., description="CPU usage percentage")
    memory: int = Field(..., description="Memory usage in MB")
    network: int = Field(..., description="Network usage in KB")


class TaskMetadata(BaseModel):
    """Model for task metadata"""

    environment: str = Field(..., description="Environment (production, staging, etc.)")
    browser: str = Field(..., description="Browser type and version")
    viewport: Viewport = Field(..., description="Browser viewport dimensions")
    userAgent: str = Field(..., description="User agent string")
    resources: Optional[Resources] = Field(None, description="System resource usage")


class TaskPerformance(BaseModel):
    """Model for task performance metrics"""

    totalActions: int = Field(..., description="Total number of actions")
    successfulActions: int = Field(..., description="Number of successful actions")
    failedActions: int = Field(..., description="Number of failed actions")
    averageActionDuration: float = Field(
        ..., description="Average action duration in seconds"
    )
    totalWaitTime: float = Field(..., description="Total wait time in seconds")
    totalNavigationTime: float = Field(
        ..., description="Total navigation time in seconds"
    )


class Task(BaseModel):
    """Main task model"""

    taskId: str = Field(..., description="Unique task identifier")
    evaluationId: Optional[str] = Field(
        None, description="Unique evaluation identifier for this task+miner combination"
    )
    agentRunId: str = Field(..., description="Associated agent run ID")
    roundNumber: Optional[int] = Field(
        None, description="Round number this task belongs to"
    )
    season: Optional[int] = Field(
        None, description="Season number this task belongs to"
    )
    website: str = Field(..., description="Target website")
    seed: Optional[str] = Field(
        None, description="Seed parameter extracted from website URL"
    )
    webVersion: Optional[str] = Field(
        None, description="Version of the web application used for this task"
    )
    useCase: str = Field(..., description="Use case or scenario")
    prompt: str = Field(..., description="Task prompt or description")
    status: TaskStatus = Field(..., description="Current task status")
    score: float = Field(..., ge=0.0, le=1.0, description="Task completion score")
    successRate: int = Field(..., ge=0, le=100, description="Success rate percentage")
    duration: int = Field(..., description="Total task duration in seconds")
    startTime: datetime = Field(..., description="Task start time")
    endTime: Optional[datetime] = Field(None, description="Task end time")
    createdAt: datetime = Field(..., description="Task creation time")
    updatedAt: datetime = Field(..., description="Last update time")
    actions: Optional[List[TaskAction]] = Field(
        None, description="List of task actions"
    )
    screenshots: Optional[List[str]] = Field(
        None, description="List of screenshot filenames"
    )
    logs: Optional[List[str]] = Field(None, description="List of log messages")
    metadata: Optional[TaskMetadata] = Field(None, description="Task metadata")
    validatorName: Optional[str] = Field(None, description="Validator name for display")
    validatorImage: Optional[str] = Field(None, description="Validator image URL")
    minerName: Optional[str] = Field(None, description="Miner/agent name for display")
    minerImage: Optional[str] = Field(None, description="Miner/agent image URL")


class TaskRoundSummary(BaseModel):
    """Summary describing the round that owns this task."""

    validatorRoundId: str = Field(..., description="Validator round identifier")
    roundNumber: Optional[int] = Field(None, description="Logical round number")
    status: str = Field(..., description="Round lifecycle status")
    startedAt: datetime = Field(..., description="Round start time")
    endedAt: Optional[datetime] = Field(None, description="Round end time")
    startEpoch: Optional[int] = Field(None, description="Starting epoch for the round")
    endEpoch: Optional[int] = Field(None, description="Ending epoch for the round")


class TaskValidatorSummary(BaseModel):
    """Summary describing the validator that evaluated this task."""

    uid: int = Field(..., description="Validator UID that produced the evaluation")
    hotkey: str = Field(..., description="Validator hotkey")
    coldkey: Optional[str] = Field(None, description="Validator coldkey")
    name: Optional[str] = Field(None, description="Validator display name")
    stake: float = Field(..., description="Validator stake at evaluation time")
    vtrust: float = Field(..., description="Validator vtrust score")
    version: Optional[str] = Field(None, description="Validator software version")
    image: Optional[str] = Field(None, description="Avatar or logo for the validator")


class TaskMinerSummary(BaseModel):
    """Summary describing the miner/agent that attempted the task."""

    uid: Optional[int] = Field(None, description="Miner UID (None if SOTA)")
    hotkey: Optional[str] = Field(None, description="Miner hotkey")
    name: str = Field(..., description="Display name for the agent/miner")
    github: Optional[str] = Field(None, description="Repository or profile URL")
    image: Optional[str] = Field(None, description="Avatar or logo for the miner")
    isSota: bool = Field(
        False, description="Whether the run corresponds to a SOTA benchmark"
    )


class TaskAgentRunSummary(BaseModel):
    """Summary describing the agent run that generated this task solution."""

    agentRunId: str = Field(..., description="Associated agent run identifier")
    validatorUid: int = Field(..., description="Validator UID overseeing the run")
    minerUid: Optional[int] = Field(None, description="Miner UID executed in the run")
    isSota: bool = Field(False, description="Indicates if the run is a SOTA benchmark")
    startedAt: Optional[datetime] = Field(None, description="Agent run start time")
    endedAt: Optional[datetime] = Field(None, description="Agent run end time")
    duration: Optional[int] = Field(None, description="Duration of the run in seconds")
    taskCount: Optional[int] = Field(
        None, description="Number of tasks executed in the run"
    )
    completedTasks: Optional[int] = Field(None, description="Number of tasks completed")
    failedTasks: Optional[int] = Field(None, description="Number of tasks failed")


class TaskEvaluationSummary(BaseModel):
    """Summary describing the evaluation generated for this task."""

    evaluationId: str = Field(..., description="Evaluation identifier")
    finalScore: float = Field(..., description="Final score issued by the validator")
    rawScore: float = Field(..., description="Raw score before adjustments")
    evaluationTime: float = Field(..., description="Evaluation duration in seconds")
    status: TaskStatus = Field(..., description="Outcome status for this evaluation")
    validatorUid: int = Field(
        ..., description="Validator UID that produced the evaluation"
    )
    minerUid: Optional[int] = Field(None, description="Miner UID evaluated")
    webAgentId: Optional[str] = Field(
        None, description="Web agent identifier used during execution"
    )
    hasFeedback: bool = Field(
        False, description="Indicates if rich feedback is available"
    )
    hasRecording: bool = Field(
        False, description="Indicates if a recording artifact is available"
    )
    reward: Optional[float] = Field(
        None, description="Reward value for the evaluation (alpha units)"
    )
    llmModel: Optional[str] = Field(
        None, description="LLM model used during evaluation"
    )
    # LLM usage tracking
    llmCost: Optional[float] = Field(
        None, description="Total cost in USD for LLM usage during evaluation"
    )
    llmTokens: Optional[int] = Field(
        None, description="Total tokens used by LLM during evaluation"
    )
    llmProvider: Optional[str] = Field(
        None, description="LLM provider used (e.g., 'openai', 'chutes')"
    )


class TaskSolutionSummary(BaseModel):
    """Summary describing the submitted solution for this task."""

    solutionId: str = Field(..., description="Task solution identifier")
    agentRunId: str = Field(..., description="Associated agent run identifier")
    minerUid: Optional[int] = Field(
        None, description="Miner UID that submitted the solution"
    )
    validatorUid: int = Field(..., description="Validator UID overseeing the solution")
    actionsCount: int = Field(..., description="Number of actions in the solution")
    webAgentId: Optional[str] = Field(
        None, description="Web agent identifier used during execution"
    )


class TaskRelationships(BaseModel):
    """Aggregated relationships for the task detail view."""

    round: TaskRoundSummary = Field(..., description="Round that owns this task")
    validator: TaskValidatorSummary = Field(
        ..., description="Validator responsible for the evaluation"
    )
    miner: TaskMinerSummary = Field(
        ..., description="Miner/agent that executed the task"
    )
    agentRun: TaskAgentRunSummary = Field(
        ..., description="Agent run context for the task"
    )
    evaluation: Optional[TaskEvaluationSummary] = Field(
        None, description="Evaluation summary if the task has been scored"
    )
    solution: Optional[TaskSolutionSummary] = Field(
        None, description="Solution summary if the miner submitted one"
    )


class TaskDetails(Task):
    """Extended task model with performance metrics and relationships"""

    performance: Optional[TaskPerformance] = Field(
        None, description="Performance metrics"
    )
    relationships: TaskRelationships = Field(
        ..., description="Related entities for the task"
    )


class TaskSummary(BaseModel):
    """Model for task summary statistics"""

    totalActions: int = Field(..., description="Total number of actions")
    successfulActions: int = Field(..., description="Number of successful actions")
    failedActions: int = Field(..., description="Number of failed actions")
    actionTypes: Dict[str, int] = Field(..., description="Count of each action type")


class TaskTimeline(BaseModel):
    """Model for task timeline events"""

    timestamp: datetime = Field(..., description="Event timestamp")
    action: str = Field(..., description="Action name")
    duration: float = Field(..., description="Action duration")
    success: bool = Field(..., description="Whether action was successful")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Additional event metadata"
    )


class TaskResults(BaseModel):
    """Model for task results"""

    taskId: str = Field(..., description="Task identifier")
    status: TaskStatus = Field(..., description="Task status")
    score: float = Field(..., description="Task score")
    duration: int = Field(..., description="Task duration")
    actions: List[TaskAction] = Field(..., description="Task actions")
    screenshots: List[TaskScreenshot] = Field(..., description="Task screenshots")
    logs: List[TaskLog] = Field(..., description="Task logs")
    summary: TaskSummary = Field(..., description="Task summary")
    timeline: List[TaskTimeline] = Field(..., description="Task timeline")


class TaskMetrics(BaseModel):
    """Model for task performance metrics"""

    duration: int = Field(..., description="Total duration in seconds")
    actionsPerSecond: float = Field(..., description="Actions per second")
    averageActionDuration: float = Field(..., description="Average action duration")
    totalWaitTime: float = Field(..., description="Total wait time")
    totalNavigationTime: float = Field(..., description="Total navigation time")
    memoryUsage: List[Dict[str, Any]] = Field(..., description="Memory usage over time")
    cpuUsage: List[Dict[str, Any]] = Field(..., description="CPU usage over time")


class WebsitePerformance(BaseModel):
    """Model for website performance statistics"""

    website: str = Field(..., description="Website name")
    tasks: int = Field(..., description="Total number of tasks")
    successful: int = Field(..., description="Number of successful tasks")
    failed: int = Field(..., description="Number of failed tasks")
    averageDuration: float = Field(..., description="Average duration")


class UseCasePerformance(BaseModel):
    """Model for use case performance statistics"""

    useCase: str = Field(..., description="Use case name")
    tasks: int = Field(..., description="Total number of tasks")
    successful: int = Field(..., description="Number of successful tasks")
    failed: int = Field(..., description="Number of failed tasks")
    averageDuration: float = Field(..., description="Average duration")


class RecentActivity(BaseModel):
    """Model for recent activity"""

    timestamp: datetime = Field(..., description="Activity timestamp")
    action: str = Field(..., description="Activity type")
    details: str = Field(..., description="Activity details")


class TaskStatistics(BaseModel):
    """Model for task statistics"""

    totalTasks: int = Field(..., description="Total number of tasks")
    completedTasks: int = Field(..., description="Number of completed tasks")
    failedTasks: int = Field(..., description="Number of failed tasks")
    runningTasks: int = Field(..., description="Number of running tasks")
    averageDuration: float = Field(..., description="Average duration across all tasks")
    successRate: float = Field(..., description="Overall success rate")
    performanceByWebsite: List[WebsitePerformance] = Field(
        ..., description="Performance by website"
    )
    performanceByUseCase: List[UseCasePerformance] = Field(
        ..., description="Performance by use case"
    )
    recentActivity: List[RecentActivity] = Field(..., description="Recent activity")


class RoundInfo(BaseModel):
    """Model for round information"""

    id: int = Field(..., description="Round ID")
    name: str = Field(..., description="Round name")
    status: str = Field(..., description="Round status")
    startTime: datetime = Field(..., description="Round start time")
    endTime: Optional[datetime] = Field(None, description="Round end time")


class ValidatorInfo(BaseModel):
    """Model for validator information"""

    id: str = Field(..., description="Validator ID")
    name: str = Field(..., description="Validator name")
    image: str = Field(..., description="Validator image URL")
    description: str = Field(..., description="Validator description")
    website: str = Field(..., description="Validator website")
    github: str = Field(..., description="Validator GitHub URL")


class AgentInfo(BaseModel):
    """Model for agent information"""

    id: str = Field(..., description="Agent ID")
    name: str = Field(..., description="Agent name")
    type: str = Field(..., description="Agent type")
    image: str = Field(..., description="Agent image URL")
    description: str = Field(..., description="Agent description")


class TaskInfo(BaseModel):
    """Model for basic task information"""

    id: str = Field(..., description="Task ID")
    website: str = Field(..., description="Website")
    useCase: str = Field(..., description="Use case")
    status: TaskStatus = Field(..., description="Task status")
    score: float = Field(..., description="Task score")


class PersonasData(BaseModel):
    """Model for personas data"""

    round: RoundInfo = Field(..., description="Round information")
    validator: ValidatorInfo = Field(..., description="Validator information")
    agent: AgentInfo = Field(..., description="Agent information")
    task: TaskInfo = Field(..., description="Task information")


class CompareTasksRequest(BaseModel):
    """Model for task comparison request"""

    taskIds: List[str] = Field(..., description="List of task IDs to compare")


class TaskComparison(BaseModel):
    """Model for task comparison results"""

    bestScore: str = Field(..., description="Task ID with best score")
    fastest: str = Field(..., description="Task ID that was fastest")
    mostActions: str = Field(..., description="Task ID with most actions")
    bestSuccessRate: str = Field(..., description="Task ID with best success rate")


class CompareTasksResponse(BaseModel):
    """Model for task comparison response"""

    tasks: List[Task] = Field(..., description="List of compared tasks")
    comparison: TaskComparison = Field(..., description="Comparison results")


class PerformanceOverTime(BaseModel):
    """Model for performance over time"""

    timestamp: datetime = Field(..., description="Timestamp")
    tasks: int = Field(..., description="Number of tasks")
    successRate: float = Field(..., description="Success rate")


class TaskAnalytics(BaseModel):
    """Model for task analytics"""

    totalTasks: int = Field(..., description="Total number of tasks")
    completedTasks: int = Field(..., description="Number of completed tasks")
    failedTasks: int = Field(..., description="Number of failed tasks")
    averageDuration: float = Field(..., description="Average duration")
    successRate: float = Field(..., description="Success rate")
    performanceByWebsite: List[WebsitePerformance] = Field(
        ..., description="Performance by website"
    )
    performanceByUseCase: List[UseCasePerformance] = Field(
        ..., description="Performance by use case"
    )
    performanceOverTime: List[PerformanceOverTime] = Field(
        ..., description="Performance over time"
    )


class FacetItem(BaseModel):
    """Model for facet items in search results"""

    name: str = Field(..., description="Facet name")
    count: int = Field(..., description="Number of items")


class SearchFacets(BaseModel):
    """Model for search facets"""

    websites: List[FacetItem] = Field(..., description="Website facets")
    useCases: List[FacetItem] = Field(..., description="Use case facets")
    statuses: List[FacetItem] = Field(..., description="Status facets")
    scoreRanges: List[FacetItem] = Field(..., description="Score range facets")


class TaskSearchResponse(BaseModel):
    """Model for task search response"""

    tasks: List[Task] = Field(..., description="List of tasks")
    total: int = Field(..., description="Total number of tasks")
    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Items per page")
    facets: SearchFacets = Field(..., description="Search facets")


class TaskListResponse(BaseModel):
    """Model for task list response"""

    tasks: List[Task] = Field(..., description="List of tasks")
    total: int = Field(..., description="Total number of tasks")
    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Items per page")


class TaskActionsResponse(BaseModel):
    """Model for task actions response"""

    actions: List[TaskAction] = Field(..., description="List of actions")
    total: int = Field(..., description="Total number of actions")
    page: int = Field(..., description="Current page")
    limit: int = Field(..., description="Items per page")


class TaskLogsResponse(BaseModel):
    """Model for task logs response"""

    logs: List[TaskLog] = Field(..., description="List of logs")
    total: int = Field(..., description="Total number of logs")


class TaskScreenshotsResponse(BaseModel):
    """Model for task screenshots response"""

    screenshots: List[TaskScreenshot] = Field(..., description="List of screenshots")


class TaskTimelineResponse(BaseModel):
    """Model for task timeline response"""

    timeline: List[TaskTimeline] = Field(..., description="List of timeline events")

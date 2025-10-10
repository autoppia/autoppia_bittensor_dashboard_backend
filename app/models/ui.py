from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from .schemas import ValidatorInfo, MinerInfo


# --- Overview Dashboard Models ---
class ChartDataPoint(BaseModel):
    """Single data point for chart visualization."""
    day: int
    score: float
    timestamp: float
    date: str
    formatted_date: str


class ValidatorCard(BaseModel):
    """Validator card information for overview dashboard."""
    validator_uid: int
    name: str
    hotkey: str
    logo_url: Optional[str] = None
    status_label: str
    status_color: str
    current_task: Dict[str, Any]
    metrics: Dict[str, Any]
    stake: Dict[str, Any]
    vtrust: float
    version: int
    last_activity: float
    uptime: float


class LiveEvent(BaseModel):
    """Live event update for overview dashboard."""
    type: str
    round_id: str
    top_miner_uid: int
    top_score: float
    timestamp: float
    validator_uid: int
    message: str


class OverviewMetrics(BaseModel):
    """Overview dashboard metrics."""
    main_chart_data: Dict[str, List[ChartDataPoint]]
    current_top_score: float
    target_score: float
    active_validators: int
    registered_miners: int
    available_websites: int
    score_to_win: float
    live_events: List[LiveEvent]
    validator_cards: List[ValidatorCard]
    last_updated: float
    time_range: str


class OverviewResponse(BaseModel):
    """Response model for overview endpoint."""
    overview: OverviewMetrics


# --- Leaderboard Models ---
class LeaderboardQuery(BaseModel):
    """Query parameters for leaderboard data."""
    validator_uid: Optional[int] = None
    round_ids: Optional[List[str]] = None
    miner_uids: Optional[List[int]] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0
    sort_by: Optional[str] = "avg_score"  # avg_score, total_reward, rank, etc.
    sort_order: Optional[str] = "desc"  # asc, desc
    time_range: Optional[tuple[float, float]] = None  # (start_ts, end_ts)


class RoundSummary(BaseModel):
    """Summary of a round for leaderboard display."""
    round_id: str
    validator_info: ValidatorInfo
    started_at: float
    ended_at: Optional[float]
    elapsed_sec: Optional[float]
    n_tasks: int
    n_miners: int
    n_winners: int
    winners: Optional[List[Dict[str, Any]]] = None
    top_performers: Optional[List[Dict[str, Any]]] = None  # Top K performers
    stats: Dict[str, Any] = Field(default_factory=dict)


class MinerPerformance(BaseModel):
    """Miner performance summary for leaderboard."""
    miner_info: MinerInfo
    rounds_participated: int
    total_tasks: int
    completed_tasks: int
    avg_score: float
    avg_execution_time: float
    total_reward: float
    wins: int  # Number of times in top K
    best_rank: Optional[int] = None
    recent_performance: List[Dict[str, Any]] = Field(default_factory=list)  # Last N rounds


class MinerLeaderboardEntry(BaseModel):
    """Miner leaderboard entry."""
    rank: int
    miner_uid: int
    miner_hotkey: str
    rounds_participated: int
    total_tasks: int
    completed_tasks: int
    avg_score: float
    avg_execution_time: float
    total_reward: float
    wins: int
    best_rank: int
    recent_performance: List[Dict[str, Any]]


class ValidatorLeaderboardEntry(BaseModel):
    """Validator leaderboard entry."""
    rank: int
    validator_uid: int
    validator_hotkey: str
    rounds_conducted: int
    total_miners_evaluated: int
    total_tasks_generated: int
    avg_round_duration: float
    completed_rounds: int
    completion_rate: float
    recent_activity: List[Dict[str, Any]]


class RoundLeaderboardEntry(BaseModel):
    """Round leaderboard entry."""
    rank: int
    round_id: str
    validator_uid: int
    started_at: float
    ended_at: Optional[float]
    elapsed_sec: Optional[float]
    n_tasks: int
    n_miners: int
    n_winners: int
    top_score: float
    avg_score: float
    total_participants: int


class LeaderboardData(BaseModel):
    """Leaderboard data container."""
    type: str
    data: List[Dict[str, Any]]  # Can be miners, validators, or rounds
    limit: int
    offset: int
    sort_by: str
    sort_order: str


class LeaderboardResponse(BaseModel):
    """Response model for leaderboard endpoint."""
    leaderboard: LeaderboardData


# --- Agents Models ---
class AgentInfo(BaseModel):
    """Agent/miner basic information."""
    miner_uid: int
    name: str
    hotkey: str
    current_rank: int
    current_score: float
    all_time_best: float
    rounds_completed: int
    last_activity: float


class AgentsListData(BaseModel):
    """Agents list data container."""
    list: List[AgentInfo]
    total_count: int
    limit: int
    offset: int
    sort_by: str
    sort_order: str


class AgentsListResponse(BaseModel):
    """Response model for agents list endpoint."""
    agents: AgentsListData


class MinerDetails(BaseModel):
    """Detailed miner information."""
    miner_uid: int
    name: str
    hotkey: str
    current_rank: int
    all_time_best_score: float
    rounds_completed: int
    current_score: float
    round_best_score: float
    joined_at: float
    last_activity: float


class MinerValidatorCard(BaseModel):
    """Validator card for specific miner."""
    validator_uid: int
    validator_name: str
    validator_image: str
    validator_hotkey: str
    agent_run_id: str
    score: float
    stake: int
    stake_display: str
    vtrust: float
    miner_uid: int
    is_winner: bool
    round_id: str
    completed_at: float
    rank: int


class MinerDetailsData(BaseModel):
    """Miner details data container."""
    miner_info: MinerDetails
    score_trend: List[ChartDataPoint]
    validator_cards: List[MinerValidatorCard]
    time_range: str
    last_updated: float


class MinerDetailsResponse(BaseModel):
    """Response model for miner details endpoint."""
    miner_details: MinerDetailsData


# --- Agent Runs Models ---
class AgentRunInfo(BaseModel):
    """Agent run basic information."""
    agent_run_id: str
    round_id: str
    round_number: int
    started_at: float
    completed_at: float
    elapsed_time: float


class UIValidatorInfo(BaseModel):
    """Validator information for UI display."""
    validator_uid: int
    validator_name: str
    validator_hotkey: str
    validator_image: str
    version: str
    stake: int
    stake_display: str
    vtrust: float


class UIMinerInfo(BaseModel):
    """Miner information for UI display."""
    miner_uid: int
    miner_name: str
    miner_hotkey: str
    miner_image: str
    rank: int


class OverallMetrics(BaseModel):
    """Overall performance metrics."""
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    overall_score: float
    overall_score_percentage: int
    average_solution_time: float
    total_websites: int
    success_rate: float


class WebsiteScore(BaseModel):
    """Website performance score."""
    website_name: str
    website_display_name: str
    description: str
    success_rate: float
    success_rate_percentage: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    average_score: float
    difficulty_breakdown: Dict[str, int]
    color: str


class TaskSummary(BaseModel):
    """Task execution summary."""
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    average_score: float
    average_solution_time: float


class TaskPagination(BaseModel):
    """Task pagination information."""
    total_tasks: int
    page_size: int
    current_page: int
    total_pages: int
    has_next: bool
    has_previous: bool


class UITaskExecution(BaseModel):
    """Individual task execution for UI display."""
    task_id: str
    prompt: str
    website: str
    use_case: str
    score: float
    solution_time: int
    difficulty: str
    started_at: float
    completed_at: float


class TasksData(BaseModel):
    """Tasks data container."""
    tasks: List[UITaskExecution]
    pagination: TaskPagination
    summary: TaskSummary


class AgentRunDetailsData(BaseModel):
    """Agent run details data container."""
    run_info: AgentRunInfo
    validator_info: UIValidatorInfo
    miner_info: UIMinerInfo
    overall_metrics: OverallMetrics
    website_scores: List[WebsiteScore]
    tasks: TasksData
    last_updated: float


class AgentRunDetailsResponse(BaseModel):
    """Response model for agent run details endpoint."""
    agent_run_details: AgentRunDetailsData


# --- Tasks Models ---
class TaskInfo(BaseModel):
    """Task basic information."""
    task_id: str
    task_prompt: str
    website_name: str
    use_case: str
    score: float
    response_time_seconds: int
    started_at: float
    completed_at: float
    difficulty: str


class RoundInfo(BaseModel):
    """Round information for tasks."""
    round_number: int
    round_id: str
    started_at: float
    ended_at: float


class TaskAction(BaseModel):
    """Individual action performed during task execution."""
    action_id: int
    action_type: str
    action_name: str
    details: Dict[str, Any]
    timestamp: float
    duration_ms: int
    order: int


class GeneratedGif(BaseModel):
    """Generated GIF information."""
    gif_url: Optional[str]
    estimated_completion: float
    placeholder_text: str


class TaskDetailsData(BaseModel):
    """Task details data container."""
    task_info: TaskInfo
    round_info: RoundInfo
    validator_info: UIValidatorInfo
    miner_info: UIMinerInfo
    actions_performed: List[TaskAction]
    generated_gif: GeneratedGif
    last_updated: float


class TaskDetailsResponse(BaseModel):
    """Response model for task details endpoint."""
    task_details: TaskDetailsData


# --- Analytics Models ---
class ScoreDistributionPoint(BaseModel):
    """Score distribution data point."""
    date: str
    avg_score: float
    max_score: float
    min_score: float
    count: int


class PerformanceAnalytics(BaseModel):
    """Performance analytics data."""
    score_distribution: List[ScoreDistributionPoint]


class ParticipationPoint(BaseModel):
    """Participation data point."""
    date: str
    validators: int
    miners: int
    tasks: int
    rounds: int


class ParticipationAnalytics(BaseModel):
    """Participation analytics data."""
    participation: List[ParticipationPoint]


class TrendsAnalytics(BaseModel):
    """Trends analytics data."""
    total_rounds: int
    total_miners: int
    total_validators: int
    avg_score: float
    total_reward: float
    avg_execution_time: float


class AnalyticsData(BaseModel):
    """Analytics data container."""
    metric: str
    time_range: str
    data: Dict[str, Any]  # Can be performance, participation, or trends
    generated_at: float


class AnalyticsResponse(BaseModel):
    """Response model for analytics endpoint."""
    analytics: AnalyticsData

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum
import time


class RoundStatus(str, Enum):
    """Round status enumeration."""
    initializing = "initializing"
    task_generation = "task_generation"
    task_distribution = "task_distribution"
    evaluation = "evaluation"
    scoring = "scoring"
    weight_assignment = "weight_assignment"
    completed = "completed"
    failed = "failed"


class TaskStatus(str, Enum):
    """Task status enumeration."""
    pending = "pending"
    sent = "sent"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"


class EvaluationStatus(str, Enum):
    """Evaluation status enumeration."""
    pending = "pending"
    evaluating = "evaluating"
    completed = "completed"
    failed = "failed"


# --- Common utilities ---
def now_ts() -> float:
    """Get current timestamp."""
    return time.time()


# --- Validator Information ---
class ValidatorInfo(BaseModel):
    """Validator information."""
    validator_uid: int
    validator_hotkey: str
    validator_coldkey: Optional[str] = None


# --- Miner Information ---
class MinerInfo(BaseModel):
    """Miner information."""
    miner_uid: int
    miner_hotkey: str
    miner_coldkey: Optional[str] = None


# --- Task Definition ---
class Task(BaseModel):
    """Individual task definition."""
    task_id: str  # Unique task ID within the round
    prompt: str
    website: str
    web_project: str
    use_case: str
    expected_actions: Optional[List[Dict[str, Any]]] = None  # Expected web actions
    max_execution_time: Optional[float] = None  # Max time in seconds
    difficulty: Optional[float] = None  # Task difficulty score
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Round Definition ---
class Round(BaseModel):
    """Round definition with all necessary information."""
    round_id: str  # Ordinal round ID (incremental)
    validator_info: ValidatorInfo
    status: RoundStatus = RoundStatus.initializing
    
    # Bittensor timing information
    start_block: int
    start_epoch: int
    end_block: Optional[int] = None
    end_epoch: Optional[int] = None
    
    # Round timing
    started_at: float = Field(default_factory=now_ts)
    ended_at: Optional[float] = None
    elapsed_sec: Optional[float] = None
    
    # Round configuration
    max_epochs: int = 20  # Default 20 epochs per round
    max_blocks: int = 360  # Default 360 blocks per epoch
    n_tasks: int  # Number of tasks to generate (N)
    n_miners: int  # Number of miners to evaluate (M)
    n_winners: int  # Number of winners to select (K)
    
    # Participants
    miners: List[MinerInfo] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    
    # Results
    agent_evaluation_runs: List[str] = Field(default_factory=list)  # List of agent_run_ids
    winners: Optional[List[Dict[str, Any]]] = None  # Final winners with rankings
    weights: Optional[Dict[int, float]] = None  # Final weights assigned to miners
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Agent Evaluation Run ---
class AgentEvaluationRun(BaseModel):
    """Agent evaluation run - all tasks and results for one miner in one round."""
    agent_run_id: str  # Unique run ID
    round_id: str
    validator_info: ValidatorInfo
    miner_info: MinerInfo
    
    # Run timing
    started_at: float = Field(default_factory=now_ts)
    ended_at: Optional[float] = None
    elapsed_sec: Optional[float] = None
    
    # Tasks in this run
    task_ids: List[str] = Field(default_factory=list)
    n_tasks_total: int
    n_tasks_completed: int = 0
    n_tasks_failed: int = 0
    
    # Aggregated scores
    avg_eval_score: Optional[float] = None
    avg_execution_time: Optional[float] = None
    total_reward: Optional[float] = None
    
    # Final ranking
    rank: Optional[int] = None  # Final rank in the round (1-based)
    weight: Optional[float] = None  # Final weight assigned
    
    # Status
    status: EvaluationStatus = EvaluationStatus.pending
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Task Execution ---
class TaskExecution(BaseModel):
    """Individual task execution by a miner."""
    task_id: str
    agent_run_id: str
    round_id: str
    validator_info: ValidatorInfo
    miner_info: MinerInfo
    
    # Task details
    task: Task
    
    # Execution timing
    sent_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    execution_time: Optional[float] = None
    
    # Response from miner
    miner_response: Optional[Dict[str, Any]] = None  # Raw response from miner
    web_actions: Optional[List[Dict[str, Any]]] = None  # Parsed web actions
    
    # Evaluation results
    eval_score: Optional[float] = None  # Score for correctness (0.0 - 1.0)
    time_score: Optional[float] = None  # Score for execution time (0.0 - 1.0)
    total_score: Optional[float] = None  # Combined score
    reward: Optional[float] = None  # Final reward for this task
    
    # Evaluation details
    evaluation_result: Optional[Dict[str, Any]] = None  # Detailed evaluation
    test_results: Optional[Dict[str, Any]] = None  # Test execution results
    
    # Status
    status: TaskStatus = TaskStatus.pending
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Round Start Request ---
class RoundStartRequest(BaseModel):
    """Request to start a new round."""
    round_id: str
    validator_info: ValidatorInfo
    start_block: int
    start_epoch: int
    n_tasks: int
    n_miners: int
    n_winners: int
    miners: List[MinerInfo]
    max_epochs: int = 20
    max_blocks: int = 360
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Task Generation Request ---
class TaskGenerationRequest(BaseModel):
    """Request to generate tasks for a round."""
    round_id: str
    validator_info: ValidatorInfo
    n_tasks: int
    web_projects: Optional[List[str]] = None
    use_cases: Optional[List[str]] = None
    difficulty_range: Optional[tuple[float, float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Task Distribution Request ---
class TaskDistributionRequest(BaseModel):
    """Request to distribute tasks to miners."""
    round_id: str
    validator_info: ValidatorInfo
    task_ids: List[str]
    miner_uids: List[int]
    batch_size: Optional[int] = None  # Number of tasks to send per batch
    timeout_seconds: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Task Response ---
class TaskResponse(BaseModel):
    """Response from miner for a specific task."""
    task_id: str
    agent_run_id: str
    round_id: str
    validator_info: ValidatorInfo
    miner_info: MinerInfo
    response: Dict[str, Any]  # Raw response from miner
    received_at: float = Field(default_factory=now_ts)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Evaluation Request ---
class EvaluationRequest(BaseModel):
    """Request to evaluate task responses."""
    round_id: str
    validator_info: ValidatorInfo
    task_execution_ids: List[str]  # IDs of task executions to evaluate
    evaluation_criteria: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Scoring Request ---
class ScoringRequest(BaseModel):
    """Request to calculate final scores and rankings."""
    round_id: str
    validator_info: ValidatorInfo
    scoring_method: Optional[str] = "weighted_average"  # weighted_average, winner_take_all, etc.
    weight_distribution: Optional[Dict[int, float]] = None  # Custom weight distribution
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Weight Assignment Request ---
class WeightAssignmentRequest(BaseModel):
    """Request to assign final weights to miners."""
    round_id: str
    validator_info: ValidatorInfo
    winners: List[Dict[str, Any]]  # Final rankings
    weight_distribution: Optional[Dict[int, float]] = None  # Custom distribution
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Round Completion Request ---
class RoundCompletionRequest(BaseModel):
    """Request to complete a round."""
    round_id: str
    validator_info: ValidatorInfo
    final_stats: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --- Leaderboard Query Models ---
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
    status: RoundStatus
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


# --- Response Models ---
class SuccessResponse(BaseModel):
    """Standard success response."""
    ok: bool = True
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    ok: bool = False
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# --- Batch Operations ---
class BatchTaskResponse(BaseModel):
    """Batch response for multiple task responses."""
    round_id: str
    validator_info: ValidatorInfo
    responses: List[TaskResponse]
    batch_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BatchEvaluationRequest(BaseModel):
    """Batch request for evaluating multiple tasks."""
    round_id: str
    validator_info: ValidatorInfo
    task_executions: List[TaskExecution]
    evaluation_criteria: Optional[Dict[str, Any]] = None
    batch_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
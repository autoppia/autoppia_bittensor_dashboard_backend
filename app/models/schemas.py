from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum
import time


class Phase(str, Enum):
    initializing = "initializing"
    generating_tasks = "generating_tasks"
    sending_tasks = "sending_tasks"
    evaluating_tasks = "evaluating_tasks"
    calculating_metrics = "calculating_metrics"
    sending_feedback = "sending_feedback"
    updating_weights = "updating_weights"
    round_start = "round_start"
    round_end = "round_end"
    done = "done"
    error = "error"


# --- Common utilities ---
def now_ts() -> float:
    """Get current timestamp."""
    return time.time()


# --- Task Information ---
class TaskInfo(BaseModel):
    task_id: str = ""
    prompt: str = ""
    website: str = ""
    web_project: str = ""
    use_case: str = ""


# --- Round Header ---
class RoundHeader(BaseModel):
    validator_uid: int
    round_id: str
    version: str
    max_epochs: int
    max_blocks: int
    started_at: float
    start_block: int
    n_total_miners: int
    task_set: List[TaskInfo] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


# --- Event Record ---
class EventRecord(BaseModel):
    validator_uid: int
    round_id: str
    phase: Phase
    message: str = ""
    ts: float = Field(default_factory=now_ts)
    extra: Dict[str, Any] = Field(default_factory=dict)


# --- Task Run ---
class TaskRun(BaseModel):
    validator_uid: int
    round_id: str
    task_id: str
    miner_uid: int
    miner_hotkey: str
    miner_coldkey: str
    eval_score: float
    time_score: float
    execution_time: float
    reward: float
    solution: Dict[str, Any] = Field(default_factory=dict)
    test_results: Dict[str, Any] = Field(default_factory=dict)
    evaluation_result: Dict[str, Any] = Field(default_factory=dict)


# --- Task Run Batch ---
class TaskRunBatch(BaseModel):
    validator_uid: int
    round_id: str
    task_runs: List[TaskRun]


# --- Agent Run ---
class AgentRun(BaseModel):
    validator_uid: int
    round_id: str
    miner_uid: int
    miner_hotkey: str
    miner_coldkey: str
    reward: float
    eval_score: float
    time_score: float
    execution_time: float
    tasks_count: Optional[int] = None


# --- Agent Run Upsert ---
class AgentRunUpsert(BaseModel):
    validator_uid: int
    round_id: str
    agent_runs: List[AgentRun]


# --- Progress Payload ---
class ProgressPayload(BaseModel):
    validator_uid: int
    round_id: str
    tasks_total: int
    tasks_completed: int
    extra: Dict[str, Any] = Field(default_factory=dict)


# --- Weights Snapshot ---
class WeightsSnapshot(BaseModel):
    full_uids: List[int]
    rewards_full_avg: List[float]
    rewards_full_wta: List[float]
    winner_uid: Optional[int] = None


# --- Weights Put ---
class WeightsPut(BaseModel):
    validator_uid: int
    round_id: str
    weights: WeightsSnapshot


# --- Round Summary ---
class RoundSummary(BaseModel):
    validator_uid: int
    round_id: str
    ended_at: float
    elapsed_sec: float
    n_active_miners: int
    n_total_miners: int
    stats: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


# --- Round Results ---
class RoundResults(BaseModel):
    validator_uid: int
    round_id: str
    version: str
    started_at: float
    ended_at: float
    elapsed_sec: float
    n_active_miners: int
    n_total_miners: int
    tasks: List[TaskInfo]
    agent_runs: List[Dict[str, Any]]  # store raw dicts to keep payloads flexible
    weights: Optional[WeightsSnapshot] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


# --- Response Models ---
class SuccessResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: Optional[str] = None

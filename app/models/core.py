"""
Core Pydantic models for the Autoppia Bittensor Dashboard backend.

These models mirror the relational schema we aim to maintain in the SQL layer.
Every entity that is persisted now has a 1:1 representation between the
Pydantic definitions and the tables they map to (identities, snapshots, runs,
tasks, solutions, evaluations, and their artifacts).
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def now_ts() -> float:
    """Return the current UNIX timestamp."""
    return time.time()


def _require_non_empty(value: str, field_name: str) -> str:
    """Ensure a non-empty string value is provided."""
    if value is None:
        raise ValueError(f"{field_name} is required")
    value_str = str(value).strip()
    if not value_str:
        raise ValueError(f"{field_name} cannot be blank")
    return value_str


# ---------------------------------------------------------------------------
# Legacy info models (used in UI/compat layers)
# ---------------------------------------------------------------------------


class ValidatorInfo(BaseModel):
    uid: int
    hotkey: str
    coldkey: Optional[str] = None
    stake: float = 0.0
    vtrust: float = 0.0
    name: Optional[str] = None
    version: Optional[str] = None
    image_url: Optional[str] = None


class MinerInfo(BaseModel):
    uid: Optional[int] = Field(default=None, description="Bittensor miner UID")
    hotkey: Optional[str] = Field(default=None, description="Bittensor miner hotkey")
    coldkey: Optional[str] = Field(default=None, description="Bittensor miner coldkey")
    agent_name: str = Field(default="", description="Display name for the agent")
    agent_image: str = Field(default="", description="Image URL for the agent")
    github: str = Field(default="", description="Repository URL for the agent")
    is_sota: bool = Field(
        default=False, description="Whether this agent is a SOTA benchmark agent"
    )
    description: Optional[str] = Field(
        default=None, description="Optional description for the agent"
    )

    @field_validator("agent_image")
    @classmethod
    def validate_agent_image(cls, v):
        if not v or not isinstance(v, str):
            return ""
        if v.strip() == "":
            return ""
        normalized = v.strip()
        if normalized.startswith("/"):
            return normalized
        try:
            from urllib.parse import urlparse

            result = urlparse(normalized)
            if not all([result.scheme in ["http", "https"], result.netloc]):
                if not normalized.lower().startswith("data:image/"):
                    raise ValueError(
                        f"Invalid image URL: {v}. Must be a valid URL or empty string."
                    )
        except Exception:
            raise ValueError(
                f"Invalid image URL: {v}. Must be a valid URL or empty string."
            )
        return normalized

    @field_validator("hotkey")
    @classmethod
    def _normalize_hotkey(cls, v):
        if v is None:
            return None
        return v.strip() or None

    @field_validator("uid")
    @classmethod
    def _normalize_uid(cls, v):
        return v if v is not None else None

    @model_validator(mode="after")  # type: ignore[misc]
    def _enforce_identity(  # type: ignore[override]
        cls, values: "MinerInfo"
    ) -> "MinerInfo":
        if not values.is_sota:
            if values.uid is None:
                raise ValueError("uid is required for non-SOTA miners")
            if not values.hotkey:
                raise ValueError("hotkey is required for non-SOTA miners")
        return values


# ---------------------------------------------------------------------------
# Identity models
# ---------------------------------------------------------------------------


class Validator(BaseModel):
    """Immutable validator identity (UID + hotkey)."""

    uid: int = Field(..., description="Unique validator UID on-chain")
    hotkey: str = Field(..., description="Validator hotkey corresponding to the UID")
    coldkey: Optional[str] = Field(
        default=None, description="Optional coldkey recorded for the validator"
    )

    @field_validator("hotkey")
    @classmethod
    def _normalize_hotkey(cls, value: str) -> str:
        return _require_non_empty(value, "hotkey")


class Miner(BaseModel):
    """Immutable miner identity."""

    uid: Optional[int] = Field(default=None, description="Miner UID")
    hotkey: Optional[str] = Field(
        default=None, description="Miner hotkey for on-chain miners"
    )
    coldkey: Optional[str] = Field(default=None, description="Optional miner coldkey")

    @model_validator(mode="after")  # type: ignore[misc]
    def _validate_identity(cls, values: "Miner") -> "Miner":  # type: ignore[override]
        if values.uid is not None:
            if not values.hotkey:
                raise ValueError("hotkey is required when uid is provided")
        return values

    @field_validator("hotkey")
    @classmethod
    def _normalize_hotkey(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


# ---------------------------------------------------------------------------
# Validator round and snapshots
# ---------------------------------------------------------------------------


class ValidatorRound(BaseModel):
    """Canonical record for a validator round executed on a specific day."""

    model_config = {"extra": "allow"}

    validator_round_id: str = Field(
        ..., description="Primary identifier for the validator round (UUID/string)"
    )
    round_number: Optional[int] = Field(
        default=None,
        description=(
            "Global round index shared by all validators for a specific day "
            "(autoincrementing integer)."
        ),
    )
    validator_uid: int = Field(
        ..., description="UID of the validator executing the round"
    )
    validator_hotkey: str = Field(
        ..., description="Hotkey of the validator executing the round"
    )
    validator_coldkey: Optional[str] = Field(
        default=None, description="Optional coldkey snapshot for the validator"
    )

    # Bittensor chain metadata
    start_block: int = Field(..., description="Chain block when the round started")
    end_block: Optional[int] = Field(
        default=None, description="Chain block when the round ended"
    )
    start_epoch: int = Field(..., description="Epoch at which the round started")
    end_epoch: Optional[int] = Field(
        default=None, description="Epoch at which the round ended"
    )

    # Timing metadata
    started_at: float = Field(default_factory=now_ts, description="Start timestamp")
    ended_at: Optional[float] = Field(
        default=None, description="End timestamp for the round"
    )
    elapsed_sec: Optional[float] = Field(
        default=None, description="Total elapsed time in seconds"
    )

    # Round configuration
    max_epochs: int = Field(
        default=20, description="Maximum number of epochs configured for the round"
    )
    max_blocks: int = Field(
        default=360, description="Maximum number of blocks configured per epoch"
    )
    n_tasks: int = Field(..., description="Total number of tasks issued in the round")
    n_miners: int = Field(..., description="Total number of miners evaluated")
    n_winners: int = Field(..., description="Number of winners selected")

    # Summary metrics
    status: Literal["active", "finished", "pending", "evaluating_finished"] = Field(
        default="active", description="Lifecycle status for the validator round"
    )
    average_score: Optional[float] = Field(
        default=None, description="Average score across all evaluations"
    )
    top_score: Optional[float] = Field(
        default=None, description="Highest score achieved in the round"
    )
    summary: Dict[str, int] = Field(
        default_factory=dict, description="Computed summary counts (tasks, runs, etc.)"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible metadata produced during the round execution",
    )


class ValidatorRoundValidator(BaseModel):
    """Mutable validator information captured for a specific validator round."""

    validator_round_id: str = Field(..., description="Validator round identifier")
    validator_uid: int = Field(..., description="Validator UID for the snapshot")
    validator_hotkey: str = Field(..., description="Validator hotkey for the snapshot")

    name: Optional[str] = Field(default=None, description="Validator display name")
    stake: Optional[float] = Field(default=None, description="Recorded stake")
    vtrust: Optional[float] = Field(default=None, description="Recorded vTrust metric")
    image_url: Optional[str] = Field(default=None, description="Avatar URL")
    version: Optional[str] = Field(
        default=None, description="Validator software version during the round"
    )
    role: Literal["primary", "observer"] = Field(
        default="primary", description="Role of the validator in the round"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Extensible metadata for the snapshot"
    )


class ValidatorRoundMiner(BaseModel):
    """
    Mutable miner information captured for a validator round.
    """

    validator_round_id: str = Field(..., description="Validator round identifier")
    miner_uid: Optional[int] = Field(
        default=None, description="Miner UID if the agent is on-chain"
    )
    miner_hotkey: Optional[str] = Field(
        default=None, description="Miner hotkey if applicable"
    )
    miner_coldkey: Optional[str] = Field(
        default=None, description="Miner coldkey if applicable"
    )

    agent_name: str = Field(..., description="Display name for the agent")
    image_url: Optional[str] = Field(
        default=None, description="Image URL associated with the agent"
    )
    github_url: Optional[str] = Field(
        default=None, description="Repository URL or source code reference"
    )
    description: Optional[str] = Field(
        default=None, description="Free-form agent description"
    )
    is_sota: bool = Field(
        default=False,
        description="Whether the agent is a benchmark/SOTA rather than a miner",
    )
    first_seen_at: Optional[float] = Field(
        default=None, description="Timestamp when the miner first appeared"
    )
    last_seen_at: Optional[float] = Field(
        default=None, description="Timestamp when the miner was last observed"
    )

    @model_validator(mode="after")  # type: ignore[misc]
    def _validate_identity(  # type: ignore[override]
        cls, values: "ValidatorRoundMiner"
    ) -> "ValidatorRoundMiner":
        if values.miner_uid is not None:
            if not values.miner_hotkey:
                raise ValueError("miner_hotkey is required when miner_uid is provided")
        return values


# ---------------------------------------------------------------------------
# Agent evaluation runs
# ---------------------------------------------------------------------------


class AgentEvaluationRun(BaseModel):
    """Execution record for a single agent (miner) in a validator round."""

    model_config = {"extra": "allow"}

    agent_run_id: str = Field(..., description="Primary identifier for the agent run")
    validator_round_id: str = Field(
        ..., description="Foreign key to the validator round"
    )
    validator_uid: int = Field(..., description="Validator UID that produced the run")
    validator_hotkey: str = Field(
        ..., description="Validator hotkey recorded for the run"
    )

    miner_uid: Optional[int] = Field(default=None, description="Miner UID")
    miner_hotkey: Optional[str] = Field(default=None, description="Miner hotkey")
    is_sota: bool = Field(
        default=False, description="Whether this run corresponds to a benchmark agent"
    )
    version: Optional[str] = Field(
        default=None, description="Version or build identifier for the agent"
    )

    started_at: float = Field(
        default_factory=now_ts, description="Start timestamp for the evaluation run"
    )
    ended_at: Optional[float] = Field(
        default=None, description="End timestamp for the evaluation run"
    )
    elapsed_sec: Optional[float] = Field(
        default=None, description="Elapsed time in seconds"
    )

    # Aggregated metrics
    average_score: Optional[float] = Field(
        default=None, description="Average evaluation score across tasks"
    )
    average_execution_time: Optional[float] = Field(
        default=None, description="Average execution time per task"
    )
    average_reward: Optional[float] = Field(
        default=None, description="Average reward produced across tasks"
    )
    total_reward: Optional[float] = Field(
        default=None, description="Total reward accumulated in the run"
    )
    total_tasks: int = Field(default=0, description="Total tasks attempted")
    completed_tasks: int = Field(default=0, description="Tasks completed successfully")
    failed_tasks: int = Field(default=0, description="Tasks that failed")

    rank: Optional[int] = Field(
        default=None, description="Final rank assigned to the agent in the round"
    )
    weight: Optional[float] = Field(
        default=None, description="Weight applied to the agent after the round"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Extensible metadata for the run"
    )

    @model_validator(mode="after")  # type: ignore[misc]
    def _validate_identity(  # type: ignore[override]
        cls, values: "AgentEvaluationRun"
    ) -> "AgentEvaluationRun":
        if not values.is_sota and values.miner_uid is None:
            raise ValueError("miner_uid is required for non-SOTA runs")
        return values


# ---------------------------------------------------------------------------
# Tasks and task solutions
# ---------------------------------------------------------------------------


class BaseTaskTest(BaseModel):
    type: str
    description: str = ""


class CheckUrlTest(BaseTaskTest):
    type: Literal["CheckUrlTest"] = "CheckUrlTest"
    url: str
    match_type: Literal["exact", "contains", "regex"] = "contains"
    description: str = Field(default="Check if browser navigated to URL")


class FindInHtmlTest(BaseTaskTest):
    type: Literal["FindInHtmlTest"] = "FindInHtmlTest"
    content: str
    match_type: Literal["exact", "contains", "regex"] = "contains"
    description: str = Field(
        default="Find content in HTML using specified matching strategy"
    )


class CheckEventTest(BaseTaskTest):
    type: Literal["CheckEventTest"] = "CheckEventTest"
    event_name: str
    event_criteria: dict = Field(default_factory=dict)
    description: str = Field(default="Check if specific event was triggered")


class JudgeBaseOnHTML(BaseTaskTest):
    type: Literal["JudgeBaseOnHTML"] = "JudgeBaseOnHTML"
    success_criteria: str
    description: str = Field(default="Judge based on HTML changes")


class JudgeBaseOnScreenshot(BaseTaskTest):
    type: Literal["JudgeBaseOnScreenshot"] = "JudgeBaseOnScreenshot"
    success_criteria: str
    description: str = Field(default="Judge based on screenshot changes")


TestUnion = Annotated[
    CheckUrlTest
    | FindInHtmlTest
    | CheckEventTest
    | JudgeBaseOnHTML
    | JudgeBaseOnScreenshot,
    Field(discriminator="type"),
]


class Task(BaseModel):
    """Represents a prompt sent to miners within a validator round."""

    task_id: str = Field(..., description="Primary identifier for the task")
    validator_round_id: str = Field(
        ..., description="Validator round that owns this task"
    )
    is_web_real: bool = Field(
        default=False,
        description="Whether the task operates on a real web environment",
    )
    web_project_id: Optional[str] = Field(
        default=None, description="Web project identifier if applicable"
    )
    url: str = Field(..., description="Target URL where the task must be executed")
    prompt: str = Field(
        ..., description="Natural language description of the task objectives"
    )
    specifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Browser configuration and additional task requirements",
    )
    tests: List[TestUnion] = Field(
        default_factory=list,
        description="Collection of validation tests associated with the task",
    )
    relevant_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional contextual data the agent may need to solve the task",
    )
    use_case: Any = Field(
        default=None, description="Associated use case metadata for the task"
    )

    _original_prompt: str = PrivateAttr(default="")

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}

    def __init__(self, **data: Any):
        original_prompt = data.get("original_prompt", data.get("prompt", ""))
        super().__init__(**data)
        object.__setattr__(self, "_original_prompt", original_prompt)

    @property
    def prompt_with_relevant_data(self) -> str:
        if self.relevant_data:
            return f"{self.prompt}\n Relevant data you may need: {self.relevant_data}"
        return self.prompt

    @property
    def original_prompt(self) -> str:
        return self._original_prompt

    @field_validator("task_id")
    @classmethod
    def _validate_task_id(cls, value: str) -> str:
        return _require_non_empty(value, "task_id")


class Action(BaseModel):
    """Single action executed by an agent while solving a task."""

    type: str = Field(..., description="Action type identifier")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Serialized action attributes"
    )


class TaskSolution(BaseModel):
    """Agent response to a specific task within a validator round."""

    solution_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Primary identifier for the task solution",
    )
    task_id: str = Field(..., description="Foreign key to the related task")
    agent_run_id: str = Field(..., description="Foreign key to the agent run")
    validator_round_id: str = Field(
        ..., description="Validator round that owns this solution"
    )
    validator_uid: int = Field(..., description="Validator UID that evaluated the task")
    validator_hotkey: str = Field(
        ..., description="Validator hotkey that evaluated the task"
    )
    miner_uid: Optional[int] = Field(
        default=None, description="Miner UID if the agent is on-chain"
    )
    miner_hotkey: Optional[str] = Field(
        default=None, description="Miner hotkey if the agent is on-chain"
    )

    actions: List[Action] = Field(
        default_factory=list,
        description="Ordered list of actions executed by the agent",
    )
    web_agent_id: Optional[str] = Field(
        default=None, description="Identifier for the web agent instance"
    )

    model_config = {"extra": "allow"}

    def nested_model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        base_dump = super().model_dump(*args, **kwargs)
        base_dump["actions"] = [action.model_dump() for action in self.actions]
        return base_dump

    def validate_relationships(self, agent_run: AgentEvaluationRun, task: Task) -> bool:
        return (
            self.task_id == task.task_id
            and self.agent_run_id == agent_run.agent_run_id
            and self.validator_round_id == agent_run.validator_round_id
        )


# ---------------------------------------------------------------------------
# Evaluations and detailed results
# ---------------------------------------------------------------------------


class Evaluation(BaseModel):
    """Record that links a task, its solution, and evaluation metadata."""

    evaluation_id: str = Field(
        ..., description="Primary identifier for the evaluation record"
    )
    validator_round_id: str = Field(
        ..., description="Validator round that owns the evaluation"
    )
    task_id: str = Field(..., description="Foreign key to the evaluated task")
    task_solution_id: str = Field(
        ..., description="Foreign key to the evaluated task solution"
    )
    agent_run_id: str = Field(..., description="Foreign key to the agent run")
    validator_uid: int = Field(
        ..., description="Validator UID performing the evaluation"
    )
    validator_hotkey: str = Field(
        ..., description="Validator hotkey performing the evaluation"
    )
    miner_uid: Optional[int] = Field(
        default=None, description="Miner UID associated with the evaluation"
    )
    miner_hotkey: Optional[str] = Field(
        default=None, description="Miner hotkey associated with the evaluation"
    )

    final_score: float = Field(
        default=0.0, description="Final score assigned to the task solution"
    )
    raw_score: float = Field(
        default=0.0, description="Raw score prior to normalisation"
    )
    evaluation_time: float = Field(
        default=0.0, description="Time taken to compute the evaluation (seconds)"
    )
    summary: Dict[str, Any] = Field(
        default_factory=dict, description="Any additional summary metrics"
    )

    def validate_relationships(
        self,
        agent_run: AgentEvaluationRun,
        task: Task,
        task_solution: TaskSolution,
    ) -> bool:
        return (
            self.task_id == task.task_id
            and self.task_solution_id == task_solution.solution_id
            and self.agent_run_id == agent_run.agent_run_id
            and self.validator_round_id == agent_run.validator_round_id
        )


class TestResult(BaseModel):
    """Represents the evaluation result of a single test."""

    success: bool
    extra_data: Optional[dict] = None


class Feedback(BaseModel):
    task_prompt: str
    final_score: float
    executed_actions: int
    failed_actions: int
    passed_tests: int
    failed_tests: int
    total_execution_time: float
    time_penalty: float
    critical_test_penalty: int
    test_results: List[TestResult] = Field(default_factory=list)
    execution_history: List[Any] = Field(default_factory=list)

    def to_text(self) -> str:
        feedback = f"Task: '{self.task_prompt}'\n"
        feedback += f"Final Score: {self.final_score}/10\n"
        feedback += f"Executed Actions: {self.executed_actions}, Failed Actions: {self.failed_actions}\n"
        feedback += (
            f"Tests Passed: {self.passed_tests}, Tests Failed: {self.failed_tests}\n"
        )
        feedback += f"Total Execution Time: {self.total_execution_time:.2f}s\n"
        feedback += f"Time Penalty: {self.time_penalty:.1f} points\n"
        feedback += f"Critical Test Penalty: {self.critical_test_penalty} points\n"
        feedback += "\nTest Results:\n"
        for test in self.test_results:
            feedback += f"  - Test: {'PASSED' if test.success else 'FAILED'}\n"
            if test.extra_data:
                feedback += f"      Extra Data: {test.extra_data}\n"

        feedback += "\nExecution History:\n"
        for record in self.execution_history:
            feedback += f"  - Action: {record}\n"

        return feedback


class EvaluationStats(BaseModel):
    """Statistics captured for a specific evaluation execution."""

    web_agent_id: str
    task_id: str
    action_count: int
    action_types: Dict[str, int] = Field(default_factory=dict)

    start_time: float
    total_time: float = 0.0
    browser_setup_time: float = 0.0
    action_execution_times: List[float] = Field(default_factory=list)
    test_execution_time: float = 0.0
    random_clicker_time: float = 0.0

    raw_score: float = 0.0
    final_score: float = 0.0
    tests_passed: int = 0
    total_tests: int = 0

    had_errors: bool = False
    error_message: str = ""

    def get_summary_dict(self) -> Dict[str, Any]:
        action_time = (
            sum(self.action_execution_times) if self.action_execution_times else 0.0
        )
        return {
            "agent_id": self.web_agent_id,
            "task_id": self.task_id,
            "actions": self.action_count,
            "score": self.final_score,
            "time_total": round(self.total_time, 2),
            "time_browser_setup": round(self.browser_setup_time, 2),
            "time_actions": round(action_time, 2),
            "time_avg_per_action": round(
                action_time / max(1, len(self.action_execution_times)), 3
            ),
            "time_random": round(self.random_clicker_time, 2),
            "tests_passed": f"{self.tests_passed}/{self.total_tests}",
            "success": not self.had_errors,
        }


class EvaluationResult(BaseModel):
    """Detailed output artefact produced during evaluation."""

    result_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Primary identifier for the evaluation result artefact",
    )
    evaluation_id: str = Field(
        ..., description="Foreign key to the parent evaluation record"
    )
    validator_round_id: str = Field(
        ..., description="Validator round that owns the evaluation"
    )
    agent_run_id: str = Field(
        ..., description="Agent run associated with the evaluation"
    )
    task_id: str = Field(..., description="Task evaluated in this artefact")
    task_solution_id: str = Field(
        ..., description="Task solution evaluated in this artefact"
    )
    miner_uid: Optional[int] = Field(
        default=None, description="Miner UID associated with the evaluation"
    )
    validator_uid: int = Field(
        ..., description="Validator UID that produced the artefact"
    )

    final_score: float = Field(
        default=0.0, description="Final score recorded for the evaluation"
    )
    test_results_matrix: List[List[TestResult]] = Field(
        default_factory=list,
        description="Detailed matrix of test results (grouped per stage/attempt)",
    )
    execution_history: List[Any] = Field(
        default_factory=list,
        description="Ordered history of execution steps captured during evaluation",
    )
    feedback: Optional[Feedback] = Field(
        default=None, description="Optional human-readable feedback summary"
    )
    web_agent_id: Optional[str] = Field(
        default=None, description="Web agent identifier used during evaluation"
    )
    raw_score: float = Field(default=0.0, description="Raw score before normalisation")
    evaluation_time: float = Field(
        default=0.0, description="Time taken to evaluate the solution (seconds)"
    )
    stats: Optional[EvaluationStats] = Field(
        default=None, description="Structured statistics collected during evaluation"
    )
    gif_recording: Optional[str] = Field(
        default=None,
        description="Optional base64-encoded GIF recording of the browser state",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Extensible metadata for the artefact"
    )

    def model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        base_dump = super().model_dump(*args, **kwargs)

        def _serialize_action(action: Any) -> Any:
            return action.model_dump() if hasattr(action, "model_dump") else action

        base_dump["execution_history"] = [
            _serialize_action(action) for action in self.execution_history
        ]
        return base_dump


# ---------------------------------------------------------------------------
# Composite DTOs
# ---------------------------------------------------------------------------


class AgentEvaluationRunWithDetails(AgentEvaluationRun):
    """AgentEvaluationRun enriched with related entities."""

    tasks: List[Task] = Field(
        default_factory=list, description="Tasks relevant to the agent run"
    )
    task_solutions: List[TaskSolution] = Field(
        default_factory=list, description="Solutions submitted by the agent"
    )
    evaluations: List[Evaluation] = Field(
        default_factory=list, description="Evaluations produced for the agent"
    )
    evaluation_results: List[EvaluationResult] = Field(
        default_factory=list,
        description="Detailed evaluation artefacts produced for the agent",
    )


class ValidatorRoundWithDetails(ValidatorRound):
    """Validator round enriched with identity snapshots and run details."""

    validator_snapshots: List[ValidatorRoundValidator] = Field(
        default_factory=list,
        description="Validator snapshots captured during the round",
    )
    miner_snapshots: List[ValidatorRoundMiner] = Field(
        default_factory=list, description="Miner snapshots captured during the round"
    )
    agent_evaluation_runs: List[AgentEvaluationRunWithDetails] = Field(
        default_factory=list,
        description="All agent evaluation runs with their related data",
    )


class ValidatorRoundSubmissionRequest(BaseModel):
    """Request payload for persisting a complete validator round."""

    validator_identities: List[Validator]
    miner_identities: List[Miner]
    validator_round: ValidatorRound
    validator_snapshots: List[ValidatorRoundValidator]
    miner_snapshots: List[ValidatorRoundMiner]
    agent_evaluation_runs: List[AgentEvaluationRun]
    tasks: List[Task]
    task_solutions: List[TaskSolution]
    evaluations: List[Evaluation]
    evaluation_results: List[EvaluationResult]


class ValidatorRoundSubmissionResponse(BaseModel):
    """Response payload emitted after a validator round is persisted."""

    success: bool
    message: str
    validator_round_id: str
    validator_uid: int
    processing_time_seconds: float
    entities_saved: Dict[str, Any]
    summary: Dict[str, int]


__all__ = [
    "now_ts",
    "ValidatorInfo",
    "MinerInfo",
    "Validator",
    "Miner",
    "ValidatorRound",
    "ValidatorRoundValidator",
    "ValidatorRoundMiner",
    "AgentEvaluationRun",
    "BaseTaskTest",
    "CheckUrlTest",
    "FindInHtmlTest",
    "CheckEventTest",
    "JudgeBaseOnHTML",
    "JudgeBaseOnScreenshot",
    "TestUnion",
    "Task",
    "Action",
    "TaskSolution",
    "Evaluation",
    "TestResult",
    "Feedback",
    "EvaluationStats",
    "EvaluationResult",
    "AgentEvaluationRunWithDetails",
    "ValidatorRoundWithDetails",
    "ValidatorRoundSubmissionRequest",
    "ValidatorRoundSubmissionResponse",
]

# Backwards compatibility aliases
ValidatorIdentity = Validator
MinerIdentity = Miner
ValidatorSnapshot = ValidatorRoundValidator
MinerSnapshot = ValidatorRoundMiner

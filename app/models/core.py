"""
Core model definitions for the AutoPPIA Bittensor Dashboard Backend.

This module contains all the Pydantic models used throughout the application,
including core entities like ValidatorRound, AgentEvaluationRun, Task, etc.
"""

from typing import Any, Dict, List, Optional, Annotated, Literal
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)
from enum import Enum
import time
import uuid

# Import moved to avoid OpenAPI generation issues
# from app.utils.validation import validate_miner_image_url


# --- Common utilities ---
def now_ts() -> float:
    """Get current timestamp."""
    return time.time()


def _require_non_empty(value: str, field_name: str) -> str:
    """Ensure the provided value is a non-empty string."""
    if value is None:
        raise ValueError(f"{field_name} is required")
    value_str = str(value).strip()
    if not value_str:
        raise ValueError(f"{field_name} cannot be blank")
    return value_str


# --- Validator Information ---
class ValidatorInfo(BaseModel):
    """Validator information."""

    uid: int
    hotkey: str
    coldkey: Optional[str] = None
    stake: float = 0.0
    vtrust: float = 0.0
    name: Optional[str] = None  # Validator name
    version: Optional[str] = None  # Validator version


# --- Miner Information ---
class MinerInfo(BaseModel):
    """Miner information."""

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
    provider: Optional[str] = Field(
        default=None, description="Company or provider for the agent"
    )

    @field_validator("agent_image")
    @classmethod
    def validate_agent_image(cls, v):
        """Validate that agent_image is a valid URL or empty string."""
        # Inline validation to avoid import issues during OpenAPI generation
        if not v or not isinstance(v, str):
            return ""

        # Allow empty string
        if v.strip() == "":
            return ""

        normalized = v.strip()

        # Allow root-relative paths (served by frontend assets)
        if normalized.startswith("/"):
            return normalized

        # Basic URL validation
        try:
            from urllib.parse import urlparse

            result = urlparse(normalized)
            if not all([result.scheme in ["http", "https"], result.netloc]):
                # Allow data URLs for base64 images
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

    @model_validator(mode="after")
    def _enforce_identity(cls, values: "MinerInfo") -> "MinerInfo":  # type: ignore[override]
        if not values.is_sota:
            if values.uid is None:
                raise ValueError("uid is required for non-SOTA miners")
            if not values.hotkey:
                raise ValueError("hotkey is required for non-SOTA miners")
        return values


# --- Validator Round Definition ---
class ValidatorRound(BaseModel):
    """Validator round definition with all necessary information."""

    validator_round_id: str  # Unique validator round identifier (UUID)
    round_number: Optional[int] = Field(
        default=None,
        alias="roundNumber",
        validation_alias=AliasChoices("roundNumber", "round", "round_number"),
        description="Logical round index shared across validator rounds",
    )
    validators: List[ValidatorInfo] = Field(
        default_factory=list, description="Validators participating in this round"
    )
    validator_info: Optional[ValidatorInfo] = Field(
        default=None,
        description="Primary validator information (for backwards compatibility)",
    )

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

    # Participants - full miner info embedded
    miners: List[MinerInfo] = Field(default_factory=list)
    sota_agents: List[MinerInfo] = Field(
        default_factory=list, description="Benchmark agents evaluated alongside miners"
    )

    # Results
    winners: Optional[List[Dict[str, Any]]] = None  # Final winners with rankings
    winner_scores: List[float] = Field(default_factory=list)  # Scores of each winner
    weights: Optional[Dict[int, float]] = None  # Final weights assigned to miners

    # Calculated statistics
    average_score: Optional[float] = None  # Average score across all miners
    top_score: Optional[float] = None  # Highest score achieved
    status: str = "active"  # Round status: active, completed, pending

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def _synchronize_validator_info(cls, values: "ValidatorRound") -> "ValidatorRound":  # type: ignore[override]
        if values.validator_info and not values.validators:
            values.validators = [values.validator_info]
        elif values.validators and not values.validator_info:
            values.validator_info = values.validators[0]
        return values


# --- Agent Evaluation Run ---
class AgentEvaluationRun(BaseModel):
    """
    Agent evaluation run - all tasks and results for one miner in one round.

    This model represents a single agent's evaluation run within a validator round.
    It includes proper relationships to link it to the validator round context.

    Key Relationships:
    - validator_round_id: Links to ValidatorRound.validator_round_id (UUID identifier)
    - validator_uid: Validator UID for additional context
    - agent_run_id: Unique identifier for this specific run

    Note: This is the primary model used throughout the application.
    """

    agent_run_id: str  # Unique run ID
    validator_round_id: str
    validator_uid: int  # Validator UID (validator info available in ValidatorRound)
    miner_uid: Optional[int] = Field(
        default=None, description="Miner UID (None for SOTA agents)"
    )
    miner_info: Optional[MinerInfo] = Field(
        default=None, description="Embedded miner/agent information"
    )
    is_sota: bool = Field(
        default=False, description="Whether this run belongs to a SOTA benchmark agent"
    )
    version: str = "1.0"  # Version of the evaluation run
    task_ids: List[str] = Field(
        default_factory=list, description="Tasks executed during the run"
    )

    # Run timing
    started_at: float = Field(default_factory=now_ts)
    ended_at: Optional[float] = None
    elapsed_sec: Optional[float] = None

    # Aggregated scores
    avg_eval_score: Optional[float] = None
    avg_execution_time: Optional[float] = None
    avg_reward: Optional[float] = None
    total_reward: Optional[float] = None
    n_tasks_total: Optional[int] = None
    n_tasks_completed: Optional[int] = None
    n_tasks_failed: Optional[int] = None

    # Final ranking
    rank: Optional[int] = None  # Final rank in the round (1-based)
    weight: Optional[float] = None  # Final weight assigned
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional execution metadata"
    )

    def validate_task_relationships(self, tasks: List["Task"]) -> bool:
        """Validate that all task relationships are properly maintained."""
        expected_task_ids = set(self.task_ids or [])
        for task in tasks:
            if task.validator_round_id != self.validator_round_id:
                return False
            if expected_task_ids and task.task_id not in expected_task_ids:
                return False

        return True

    @field_validator("agent_run_id")
    @classmethod
    def _validate_agent_run_id(cls, value: str) -> str:
        return _require_non_empty(value, "agent_run_id")

    @model_validator(mode="after")
    def _validate_agent_identity(cls, values: "AgentEvaluationRun") -> "AgentEvaluationRun":  # type: ignore[override]
        if not values.is_sota:
            if values.miner_uid is None:
                raise ValueError("miner_uid is required for non-SOTA agent runs")
            if values.miner_info and values.miner_info.is_sota:
                values.miner_info.is_sota = False
        else:
            if values.miner_info and not values.miner_info.is_sota:
                values.miner_info.is_sota = True
        return values


# --- Task Definition (from validator) ---
# Test classes for polymorphic deserialization
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
    """
    Represents a task with associated metadata, specs, tests, etc.
    """

    task_id: str = Field(..., description="Unique identifier for the task")
    validator_round_id: str  # Reference to the validator round (validator info available in ValidatorRound)

    scope: Literal["global", "local"] = Field(
        default="local",
        description="Task scope: 'global' for system-wide tasks, 'local' for specific context tasks",
    )
    is_web_real: bool = Field(
        default=False,
        description="Indicates if the task operates on a real web environment versus simulation",
    )
    web_project_id: str | None = Field(default=None, description="Web project ID")
    url: str = Field(..., description="Target URL where the task will be executed")
    prompt: str = Field(
        ...,
        description="Natural language description of the task objectives and requirements",
    )
    html: str = Field(
        default_factory=str, description="Complete HTML content of the target page"
    )
    clean_html: str = Field(
        default_factory=str,
        description="Optimized HTML content with reduced overhead for processing",
    )
    interactive_elements: str | None = Field(
        default=None,
        description="Mapping of interactive elements found in the HTML content, including buttons, forms, etc.",
    )
    screenshot: str | None = Field(
        default=None,
        description="Pil Image of the task environment or webpage encoded in base64 and stringify",
    )
    screenshot_description: str | None = Field(
        default=None,
        description="Textual description of the screenshot content and relevant elements",
    )
    specifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Browser configuration and requirements for task execution",
    )
    tests: list[TestUnion] = Field(
        default_factory=list,
        description="Collection of validation tests that verify the task",
    )
    milestones: list["Task"] | None = Field(
        default=None,
        description="Ordered list of Subtasks that must be completed sequentially",
    )
    relevant_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional contextual data required for task execution",
    )
    success_criteria: str | None = Field(
        default=None,
        description="Clear definition of conditions that indicate successful task completion",
    )
    use_case: Any = Field(
        default=None, description="UseCase instance associated with this task"
    )
    should_record: bool = False

    _original_prompt: str = PrivateAttr()

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}

    def __init__(self, **data):
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


# --- Simple Action Class ---
class Action(BaseModel):
    """
    Simple action class with basic attributes.
    """

    type: str = Field(..., description="Action type")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Action attributes"
    )


# --- Task Solution ---
class TaskSolution(BaseModel):
    solution_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the task solution",
    )
    task_id: str  # Reference to the task
    validator_round_id: str  # Reference to the validator round (utility field)
    agent_run_id: str  # Reference to the agent evaluation run
    miner_uid: Optional[int] = Field(
        default=None, description="Reference to the miner (None for SOTA agents)"
    )
    validator_uid: int  # Reference to the validator (utility field)
    actions: list[Action] = Field(default_factory=list)
    web_agent_id: str | None = None
    recording: Any | None = Field(
        default=None,
        description="Optional recording data associated with the task solution.",
    )

    model_config = {"extra": "allow"}

    def nested_model_dump(self, *args, **kwargs) -> dict[str, Any]:
        base_dump = super().model_dump(*args, **kwargs)
        base_dump["actions"] = [action.model_dump() for action in self.actions]
        return base_dump

    def validate_relationships(
        self, agent_run: "AgentEvaluationRun", task: "Task"
    ) -> bool:
        """Validate that this task solution is properly linked to the agent run and task."""
        return (
            self.task_id == task.task_id
            and self.agent_run_id == agent_run.agent_run_id
            and self.validator_round_id == agent_run.validator_round_id
            and self.miner_uid == agent_run.miner_uid
            and self.validator_uid == agent_run.validator_uid
        )


# --- Evaluation Results ---
class TestResult(BaseModel):
    """Represents the evaluation result of a single test."""

    success: bool  # True if the test passed, False otherwise
    extra_data: dict | None = None  # Additional data related to the test


class Feedback(BaseModel):
    task_prompt: str  # The description of the task being evaluated
    final_score: float  # Overall evaluation score (0-10)
    executed_actions: int  # Number of successfully executed actions
    failed_actions: int  # Number of failed actions
    passed_tests: int  # Number of tests that passed
    failed_tests: int  # Number of tests that failed
    total_execution_time: float  # Total time taken for execution
    time_penalty: float  # Penalty points for exceeding expected time
    critical_test_penalty: int  # Penalty points for failing critical tests
    test_results: list[TestResult] = Field(
        default_factory=list
    )  # Detailed test results
    execution_history: list[Any] = Field(
        default_factory=list
    )  # Detailed execution logs

    def to_text(self) -> str:
        """Generates a human-readable textual summary."""
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
    """Statistics for a single evaluation"""

    web_agent_id: str
    task_id: str
    action_count: int
    action_types: dict[str, int] = Field(default_factory=dict)

    # Timing stats
    start_time: float
    total_time: float = 0
    browser_setup_time: float = 0
    action_execution_times: list[float] = Field(default_factory=list)
    test_execution_time: float = 0
    random_clicker_time: float = 0

    # Performance stats
    raw_score: float = 0
    final_score: float = 0
    tests_passed: int = 0
    total_tests: int = 0

    # Error tracking
    had_errors: bool = False
    error_message: str = ""

    def get_summary_dict(self) -> dict[str, Any]:
        """Get a dictionary of summary statistics"""
        action_time = (
            sum(self.action_execution_times) if self.action_execution_times else 0
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
    """Encapsulates the output of a task evaluation."""

    evaluation_id: str = Field(
        ..., description="Unique identifier for the evaluation result"
    )
    task_id: str  # Reference to the task
    task_solution_id: str  # Reference to the task solution
    validator_round_id: str  # Reference to the validator round (utility field)
    agent_run_id: str  # Reference to the agent evaluation run
    miner_uid: Optional[int] = Field(
        default=None, description="Reference to the miner (None for SOTA agents)"
    )
    validator_uid: int  # Reference to the validator (utility field)

    final_score: float = 0
    # List of test evaluation results
    test_results_matrix: list[list[TestResult]]
    # History of all actions executed
    execution_history: list[Any]
    feedback: Feedback | None = None  # Feedback generated during the evaluation
    web_agent_id: str | None = None
    raw_score: float = 0.0

    evaluation_time: float = 0.0  # Time taken to evaluate this solution
    stats: EvaluationStats | None = None
    gif_recording: str | None = Field(
        None,
        description="Base64-encoded GIF recording of the browser state after execution",
    )

    def model_dump(self, *args, **kwargs):
        base_dump = super().model_dump(*args, **kwargs)
        base_dump["execution_history"] = [
            action.model_dump() if hasattr(action, "model_dump") else str(action)
            for action in self.execution_history
        ]
        return base_dump

    def validate_relationships(
        self,
        agent_run: "AgentEvaluationRun",
        task: "Task",
        task_solution: "TaskSolution",
    ) -> bool:
        """Validate that this evaluation result is properly linked to the agent run, task, and task solution."""
        return (
            self.task_id == task.task_id
            and self.task_solution_id == task_solution.solution_id
            and self.agent_run_id == agent_run.agent_run_id
            and self.validator_round_id == agent_run.validator_round_id
            and self.miner_uid == agent_run.miner_uid
            and self.validator_uid == agent_run.validator_uid
        )

    @field_validator("evaluation_id")
    @classmethod
    def _validate_evaluation_id(cls, value: str) -> str:
        return _require_non_empty(value, "evaluation_id")

    @field_validator("task_id")
    @classmethod
    def _validate_task_reference(cls, value: str) -> str:
        return _require_non_empty(value, "task_id")

    @field_validator("task_solution_id")
    @classmethod
    def _validate_task_solution_reference(cls, value: str) -> str:
        return _require_non_empty(value, "task_solution_id")

    @field_validator("agent_run_id")
    @classmethod
    def _validate_agent_run_reference(cls, value: str) -> str:
        return _require_non_empty(value, "agent_run_id")


# --- Detailed Objects with Related Data ---
class AgentEvaluationRunWithDetails(AgentEvaluationRun):
    """AgentEvaluationRun with all its related Tasks, TaskSolutions, and EvaluationResults."""

    tasks: List[Task] = Field(
        default_factory=list, description="All tasks for this agent run"
    )
    task_solutions: List[TaskSolution] = Field(
        default_factory=list, description="All task solutions for this agent run"
    )
    evaluation_results: List[EvaluationResult] = Field(
        default_factory=list, description="All evaluation results for this agent run"
    )


class ValidatorRoundWithDetails(ValidatorRound):
    """Validator round with all its related AgentEvaluationRuns and their data."""

    agent_evaluation_runs: List[AgentEvaluationRunWithDetails] = Field(
        default_factory=list,
        description="All agent evaluation runs for this round with their complete data",
    )


# --- API Request/Response Models ---
class ValidatorRoundSubmissionRequest(BaseModel):
    """Request model for submitting complete round data."""

    round: ValidatorRound
    agent_evaluation_runs: List[AgentEvaluationRun]
    tasks: List[Task]
    task_solutions: List[TaskSolution]
    evaluation_results: List[EvaluationResult]


class ValidatorRoundSubmissionResponse(BaseModel):
    """Response model for round submission."""

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
    "ValidatorRound",
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
    "TestResult",
    "Feedback",
    "EvaluationStats",
    "EvaluationResult",
    "AgentEvaluationRunWithDetails",
    "ValidatorRoundWithDetails",
    "ValidatorRoundSubmissionRequest",
    "ValidatorRoundSubmissionResponse",
]

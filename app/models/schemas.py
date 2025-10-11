from typing import Any, Dict, List, Optional, Annotated, Literal
from pydantic import BaseModel, Field, PrivateAttr
from enum import Enum
import time
import uuid


# --- Common utilities ---
def now_ts() -> float:
    """Get current timestamp."""
    return time.time()


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
    uid: int
    hotkey: str
    coldkey: Optional[str] = None
    agent_name: str = ""
    agent_image: str = ""
    github: str = ""


# --- Round Definition ---
class Round(BaseModel):
    """Round definition with all necessary information."""
    round_id: str  # Ordinal round ID (incremental)
    validators: List[ValidatorInfo]  # Multiple validators participating in this round
    
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
    
    # Results
    winners: Optional[List[Dict[str, Any]]] = None  # Final winners with rankings
    winner_scores: List[float] = Field(default_factory=list)  # Scores of each winner
    weights: Optional[Dict[int, float]] = None  # Final weights assigned to miners
    
    # Calculated statistics
    average_score: Optional[float] = None  # Average score across all miners
    top_score: Optional[float] = None  # Highest score achieved
    status: str = "active"  # Round status: active, completed, pending


# --- Agent Evaluation Run ---
class AgentEvaluationRun(BaseModel):
    """Agent evaluation run - all tasks and results for one miner in one round."""
    agent_run_id: str  # Unique run ID
    round_id: str
    validator_uid: int  # Validator UID (validator info available in Round)
    miner_uid: int  # Miner UID (miner info available in Round)
    version: str = "1.0"  # Version of the evaluation run
    
    # Run timing
    started_at: float = Field(default_factory=now_ts)
    ended_at: Optional[float] = None
    elapsed_sec: Optional[float] = None
    

    # Aggregated scores
    avg_eval_score: Optional[float] = None
    avg_execution_time: Optional[float] = None
    avg_reward: Optional[float] = None
    
    # Final ranking
    rank: Optional[int] = None  # Final rank in the round (1-based)
    weight: Optional[float] = None  # Final weight assigned
    
    def validate_task_relationships(self, tasks: List["Task"]) -> bool:
        """Validate that all task relationships are properly maintained."""
        # Check that all tasks assigned to this agent run have the correct agent_run_id
        for task in tasks:
            if task.agent_run_id == self.agent_run_id:
                # Verify the task belongs to the same round
                # Note: validator_uid is not stored in Task anymore, it's available via AgentEvaluationRun
                if task.round_id != self.round_id:
                    return False
        
        return True


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
    description: str = Field(default="Find content in HTML using specified matching strategy")

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

TestUnion = Annotated[CheckUrlTest | FindInHtmlTest | CheckEventTest | JudgeBaseOnHTML | JudgeBaseOnScreenshot, Field(discriminator="type")]

class Task(BaseModel):
    """
    Represents a task with associated metadata, specs, tests, etc.
    """
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the task")
    round_id: str  # Reference to the round (validator info available in Round)
    agent_run_id: str  # Reference to the agent evaluation run
    
    scope: Literal["global", "local"] = Field(default="local", description="Task scope: 'global' for system-wide tasks, 'local' for specific context tasks")
    is_web_real: bool = Field(default=False, description="Indicates if the task operates on a real web environment versus simulation")
    web_project_id: str | None = Field(default=None, description="Web project ID")
    url: str = Field(..., description="Target URL where the task will be executed")
    prompt: str = Field(..., description="Natural language description of the task objectives and requirements")
    html: str = Field(default_factory=str, description="Complete HTML content of the target page")
    clean_html: str = Field(default_factory=str, description="Optimized HTML content with reduced overhead for processing")
    interactive_elements: str | None = Field(default=None, description="Mapping of interactive elements found in the HTML content, including buttons, forms, etc.")
    screenshot: str | None = Field(default=None, description="Pil Image of the task environment or webpage encoded in base64 and stringify")
    screenshot_description: str | None = Field(default=None, description="Textual description of the screenshot content and relevant elements")
    specifications: Dict[str, Any] = Field(default_factory=dict, description="Browser configuration and requirements for task execution")
    tests: list[TestUnion] = Field(default_factory=list, description="Collection of validation tests that verify the task")
    milestones: list["Task"] | None = Field(default=None, description="Ordered list of Subtasks that must be completed sequentially")
    relevant_data: dict[str, Any] = Field(default_factory=dict, description="Additional contextual data required for task execution")
    success_criteria: str | None = Field(default=None, description="Clear definition of conditions that indicate successful task completion")
    use_case: Any = Field(default=None, description="UseCase instance associated with this task")
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


# --- Simple Action Class ---
class Action(BaseModel):
    """
    Simple action class with basic attributes.
    """
    type: str = Field(..., description="Action type")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Action attributes")


# --- Task Solution ---
class TaskSolution(BaseModel):
    solution_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the task solution")
    task_id: str  # Reference to the task
    round_id: str  # Reference to the round (utility field)
    agent_run_id: str  # Reference to the agent evaluation run
    miner_uid: int  # Reference to the miner
    validator_uid: int  # Reference to the validator (utility field)
    actions: list[Action] = Field(default_factory=list)
    web_agent_id: str | None = None
    recording: Any | None = Field(default=None, description="Optional recording data associated with the task solution.")

    def nested_model_dump(self, *args, **kwargs) -> dict[str, Any]:
        base_dump = super().model_dump(*args, **kwargs)
        base_dump["actions"] = [action.model_dump() for action in self.actions]
        return base_dump
    
    def validate_relationships(self, agent_run: "AgentEvaluationRun", task: "Task") -> bool:
        """Validate that this task solution is properly linked to the agent run and task."""
        return (
            self.task_id == task.task_id and
            self.agent_run_id == agent_run.agent_run_id and
            self.round_id == agent_run.round_id and
            self.miner_uid == agent_run.miner_uid and
            self.validator_uid == agent_run.validator_uid and
            task.agent_run_id == agent_run.agent_run_id
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
    test_results: list[TestResult] = Field(default_factory=list)  # Detailed test results
    execution_history: list[Any] = Field(default_factory=list)  # Detailed execution logs

    def to_text(self) -> str:
        """Generates a human-readable textual summary."""
        feedback = f"Task: '{self.task_prompt}'\n"
        feedback += f"Final Score: {self.final_score}/10\n"
        feedback += f"Executed Actions: {self.executed_actions}, Failed Actions: {self.failed_actions}\n"
        feedback += f"Tests Passed: {self.passed_tests}, Tests Failed: {self.failed_tests}\n"
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
        action_time = sum(self.action_execution_times) if self.action_execution_times else 0
        return {
            "agent_id": self.web_agent_id,
            "task_id": self.task_id,
            "actions": self.action_count,
            "score": self.final_score,
            "time_total": round(self.total_time, 2),
            "time_browser_setup": round(self.browser_setup_time, 2),
            "time_actions": round(action_time, 2),
            "time_avg_per_action": round(action_time / max(1, len(self.action_execution_times)), 3),
            "time_random": round(self.random_clicker_time, 2),
            "tests_passed": f"{self.tests_passed}/{self.total_tests}",
            "success": not self.had_errors,
        }


class EvaluationResult(BaseModel):
    """Encapsulates the output of a task evaluation."""
    evaluation_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the evaluation result")
    task_id: str  # Reference to the task
    task_solution_id: str  # Reference to the task solution
    round_id: str  # Reference to the round (utility field)
    agent_run_id: str  # Reference to the agent evaluation run
    miner_uid: int  # Reference to the miner
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
    gif_recording: str | None = Field(None, description="Base64-encoded GIF recording of the browser state after execution")

    def model_dump(self, *args, **kwargs):
        base_dump = super().model_dump(*args, **kwargs)
        base_dump["execution_history"] = [action.model_dump() if hasattr(action, 'model_dump') else str(action) for action in self.execution_history]
        return base_dump
    
    def validate_relationships(self, agent_run: "AgentEvaluationRun", task: "Task", task_solution: "TaskSolution") -> bool:
        """Validate that this evaluation result is properly linked to the agent run, task, and task solution."""
        return (
            self.task_id == task.task_id and
            self.task_solution_id == task_solution.solution_id and
            self.agent_run_id == agent_run.agent_run_id and
            self.round_id == agent_run.round_id and
            self.miner_uid == agent_run.miner_uid and
            self.validator_uid == agent_run.validator_uid and
            task.agent_run_id == agent_run.agent_run_id
        )


# --- Detailed Objects with Related Data ---
class AgentEvaluationRunWithDetails(AgentEvaluationRun):
    """AgentEvaluationRun with all its related Tasks, TaskSolutions, and EvaluationResults."""
    tasks: List[Task] = Field(default_factory=list, description="All tasks for this agent run")
    task_solutions: List[TaskSolution] = Field(default_factory=list, description="All task solutions for this agent run")
    evaluation_results: List[EvaluationResult] = Field(default_factory=list, description="All evaluation results for this agent run")


class RoundWithDetails(Round):
    """Round with all its related AgentEvaluationRuns and their data."""
    agent_evaluation_runs: List[AgentEvaluationRunWithDetails] = Field(default_factory=list, description="All agent evaluation runs for this round with their complete data")


# --- API Request/Response Models ---
class RoundSubmissionRequest(BaseModel):
    """Request model for submitting complete round data."""
    round: Round
    agent_evaluation_runs: List[AgentEvaluationRun]
    tasks: List[Task]
    task_solutions: List[TaskSolution]
    evaluation_results: List[EvaluationResult]


class RoundSubmissionResponse(BaseModel):
    """Response model for round submission."""
    success: bool
    message: str
    round_id: str
    validator_uid: int
    processing_time_seconds: float
    entities_saved: Dict[str, Any]
    summary: Dict[str, int]
# Models package

# Core models - essential business logic
from .core import (
    # Core entities
    ValidatorInfo,
    MinerInfo,
    Task,
    ValidatorRound,
    AgentEvaluationRun,
    
    # Utilities
    now_ts,
    
    # Validator classes
    Action,
    TaskSolution,
    BaseTaskTest,
    CheckUrlTest,
    FindInHtmlTest,
    CheckEventTest,
    JudgeBaseOnHTML,
    JudgeBaseOnScreenshot,
    TestUnion,
    
    # Evaluation classes
    TestResult,
    Feedback,
    EvaluationStats,
    EvaluationResult,
)

# Legacy imports for backward compatibility
from .core import *

"""
Evaluation models that aggregate tasks, solutions, and results.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .action import Action
from .evaluation_result import EvaluationResult, EvaluationResultRead
from .log import LogEntry
from .task import TaskRead
from .task_solution import TaskSolution, TaskSolutionRead
from app.utils.datetime import to_datetime


class Evaluation(BaseModel):
    """Evaluation tying together a task, the submitted solution, and the results."""

    evaluation_id: str
    task_id: str
    task_solution: TaskSolution
    evaluation_result: EvaluationResult
    actions: List[Action] = Field(default_factory=list)
    screenshots: List[str] = Field(default_factory=list)
    logs: List[LogEntry] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class EvaluationRead(Evaluation):
    """Evaluation retrieved from persistent storage."""
    task: TaskRead
    task_solution: TaskSolutionRead
    evaluation_result: EvaluationResultRead
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: Any) -> datetime:
        result = to_datetime(value)
        if result is None:
            raise ValueError("created_at cannot be null")
        return result

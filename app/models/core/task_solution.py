"""
Task solution models submitted by miners.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.datetime import to_datetime


class TaskSolution(BaseModel):
    """Solution submitted by a miner."""

    task_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    content: Dict[str, Any] = Field(default_factory=dict)
    attributes: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class TaskSolutionRead(TaskSolution):
    """Task solution retrieved from persistent storage."""

    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: Any) -> datetime:
        result = to_datetime(value)
        if result is None:
            raise ValueError("created_at cannot be null")
        return result

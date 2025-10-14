"""
Validator round models aggregating tasks and agent runs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent import AgentEvaluationRun, AgentEvaluationRunRead
from .info import ValidatorInfo
from .task import Task, TaskRead
from app.utils.datetime import to_datetime


class ValidatorRound(BaseModel):
    """Validator round with nested entities."""

    validator_round_id: str = Field(..., description="Unique validator round identifier")
    round: int = 0
    validator: ValidatorInfo
    start_block: Optional[int] = None
    start_epoch: Optional[int] = None
    end_block: Optional[int] = None
    end_epoch: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    round_epochs_length: int = 20
    n_tasks: int
    n_winners: int
    tasks: List[Task] = Field(default_factory=list)
    agent_runs: List[AgentEvaluationRun] = Field(default_factory=list)
    winners: Optional[List[Dict[str, Any]]] = None
    winner_scores: List[float] = Field(default_factory=list)
    weights: Optional[Dict[str, float]] = None
    average_score: Optional[float] = None
    top_score: Optional[float] = None
    status: str = "active"
    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("validator_round_id")
    @classmethod
    def _ensure_uuid(cls, value: str) -> str:
        try:
            UUID(str(value))
        except (ValueError, TypeError):
            raise ValueError("validator_round_id must be a valid UUID string")
        return str(value)

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)


class ValidatorRoundRead(ValidatorRound):
    """Validator round retrieved from persistent storage."""

    created_at: datetime
    tasks: List[TaskRead] = Field(default_factory=list)
    agent_runs: List[AgentEvaluationRunRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: Any) -> datetime:
        result = to_datetime(value)
        if result is None:
            raise ValueError("created_at cannot be null")
        return result

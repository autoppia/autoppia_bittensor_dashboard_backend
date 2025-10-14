"""
Agent evaluation run models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .evaluation import Evaluation, EvaluationRead
from .info import MinerInfo
from .log import LogEntry, RunEvent
from .metrics import RunMetrics
from app.utils.datetime import to_datetime


class AgentEvaluationRun(BaseModel):
    """Agent evaluation run within a round."""

    agent_run_id: str
    round: Optional[int] = None
    miner: MinerInfo
    version: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    elapsed_sec: Optional[float] = None
    average_score: Optional[float] = None
    average_response_time: Optional[float] = None
    average_reward: Optional[float] = None
    rank: Optional[int] = None
    weight: Optional[float] = None
    evaluations: List[Evaluation] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    timeline: List[RunEvent] = Field(default_factory=list)
    logs: List[LogEntry] = Field(default_factory=list)
    metrics: Optional[RunMetrics] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)


class AgentEvaluationRunRead(AgentEvaluationRun):
    """Agent evaluation run retrieved from persistent storage."""

    created_at: datetime
    evaluations: List[EvaluationRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: Any) -> datetime:
        result = to_datetime(value)
        if result is None:
            raise ValueError("created_at cannot be null")
        return result

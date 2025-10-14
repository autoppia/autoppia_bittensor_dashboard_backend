"""
Evaluation-centric UI models for the dashboard.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict

from app.models.ui.agent_runs import Action, Log, ResponseBase


class EvaluationStatus(str, Enum):
    """Status of an evaluation based on its resulting score."""

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


class EvaluationTaskInfo(BaseModel):
    """Metadata about the task associated with an evaluation."""

    id: str
    url: str
    prompt: str
    scope: str
    useCase: Optional[str] = None
    useCaseMetadata: Dict[str, Any] = Field(default_factory=dict)


class EvaluationListItem(BaseModel):
    """Summary information for an evaluation."""

    evaluationId: str
    runId: str
    agentId: str
    validatorId: str
    roundId: int
    taskId: str
    taskUrl: str
    status: EvaluationStatus
    score: float
    reward: float
    responseTime: float
    createdAt: Optional[str]
    updatedAt: Optional[str]


class EvaluationDetail(EvaluationListItem):
    """Full detail for an evaluation including solution and result artifacts."""

    task: EvaluationTaskInfo
    actions: List[Action] = Field(default_factory=list)
    logs: List[Log] = Field(default_factory=list)
    screenshots: List[str] = Field(default_factory=list)
    taskSolution: Dict[str, Any] = Field(default_factory=dict)
    evaluationResult: Dict[str, Any] = Field(default_factory=dict)
    extras: Dict[str, Any] = Field(default_factory=dict)


class EvaluationListResponse(ResponseBase):
    """Response wrapper for evaluation list requests."""

    data: Optional[Dict[str, Any]] = None


class EvaluationDetailResponse(ResponseBase):
    """Response wrapper for evaluation detail requests."""

    data: Optional[Dict[str, EvaluationDetail]] = None


__all__ = [
    "EvaluationStatus",
    "EvaluationTaskInfo",
    "EvaluationListItem",
    "EvaluationDetail",
    "EvaluationListResponse",
    "EvaluationDetailResponse",
]

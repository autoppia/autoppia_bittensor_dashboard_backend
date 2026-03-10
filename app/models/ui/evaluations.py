"""
Evaluation-centric UI models for the dashboard.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models.ui.agent_runs import Action, Log


class ResponseBase(BaseModel):
    success: bool
    error: str | None = None
    code: str | None = None


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
    useCase: str | None = None
    useCaseMetadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationListItem(BaseModel):
    """Summary information for an evaluation."""

    evaluationId: str
    runId: str
    agentId: str
    validatorId: str
    roundId: int
    season: int | None = None  # Season number (e.g., 1, 2, 3)
    taskId: str
    taskUrl: str
    status: EvaluationStatus
    score: float
    reward: float
    responseTime: float
    createdAt: str | None = None
    updatedAt: str | None = None
    zeroReason: str | None = None  # Reason for score 0 (e.g. task_timeout, tests_failed)


class EvaluationDetail(EvaluationListItem):
    """Full detail for an evaluation including solution and result artifacts."""

    task: EvaluationTaskInfo
    actions: list[Action] = Field(default_factory=list)
    logs: list[Log] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    taskSolution: dict[str, Any] = Field(default_factory=dict)
    evaluationResult: dict[str, Any] = Field(default_factory=dict)
    extras: dict[str, Any] = Field(default_factory=dict)


class EvaluationListResponse(ResponseBase):
    """Response wrapper for evaluation list requests."""

    data: dict[str, Any] | None = None


class EvaluationDetailResponse(ResponseBase):
    """Response wrapper for evaluation detail requests."""

    data: dict[str, EvaluationDetail] | None = None


class EvaluationGifUploadResponse(ResponseBase):
    """Response wrapper for evaluation GIF upload requests."""

    data: dict[str, str] | None = None


__all__ = [
    "EvaluationStatus",
    "EvaluationTaskInfo",
    "EvaluationListItem",
    "EvaluationDetail",
    "EvaluationListResponse",
    "EvaluationDetailResponse",
    "EvaluationGifUploadResponse",
]

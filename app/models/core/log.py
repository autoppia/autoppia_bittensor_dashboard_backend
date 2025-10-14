"""
Structured logging models for agent runs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.datetime import to_datetime


class LogEntry(BaseModel):
    """Structured log entry emitted during a run or task."""

    timestamp: Optional[datetime] = None
    level: str = "info"
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)


class RunEvent(BaseModel):
    """Timeline event for an agent run."""

    timestamp: Optional[datetime] = None
    type: str
    message: str
    task_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)

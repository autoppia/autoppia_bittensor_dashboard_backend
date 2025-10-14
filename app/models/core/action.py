"""
Fine-grained task actions performed by agents.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.datetime import to_datetime


class Action(BaseModel):
    """Fine-grained action taken while executing a task."""

    id: Optional[str] = None
    type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    timestamp: Optional[datetime] = None
    duration: Optional[float] = None
    success: Optional[bool] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)

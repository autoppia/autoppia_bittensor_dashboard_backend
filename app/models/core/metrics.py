"""
Metric sampling models for agent runs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.datetime import to_datetime


class MetricSample(BaseModel):
    """Single metric sample captured during a run."""

    timestamp: Optional[datetime] = None
    value: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)


class RunMetrics(BaseModel):
    """Aggregated performance metrics for an agent run."""

    cpu: List[MetricSample] = Field(default_factory=list)
    memory: List[MetricSample] = Field(default_factory=list)
    network: List[MetricSample] = Field(default_factory=list)
    duration: Optional[float] = None
    peak_cpu: Optional[float] = None
    peak_memory: Optional[float] = None
    total_network_traffic: Optional[float] = None
    extras: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

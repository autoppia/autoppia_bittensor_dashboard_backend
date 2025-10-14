"""
Task models provided by the validator.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.datetime import to_datetime


class Task(BaseModel):
    """Task definition received from the validator."""

    task_id: str
    round: Optional[int] = None
    agent_run_id: Optional[str] = None
    scope: str = "local"
    is_web_real: bool = False
    web_project_id: Optional[str] = None
    url: str
    prompt: str
    html: str = ""
    clean_html: str = ""
    interactive_elements: Optional[str] = None
    screenshot: Optional[str] = None
    screenshot_description: Optional[str] = None
    specifications: Dict[str, Any] = Field(default_factory=dict)
    tests: List[Dict[str, Any]] = Field(default_factory=list)
    milestones: Optional[List[Dict[str, Any]]] = None
    relevant_data: Dict[str, Any] = Field(default_factory=dict)
    success_criteria: Optional[str] = None
    use_case: Optional[Dict[str, Any]] = None
    should_record: bool = False
    extras: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class TaskRead(Task):
    """Task retrieved from persistent storage."""

    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: Any) -> Optional[datetime]:
        return to_datetime(value)

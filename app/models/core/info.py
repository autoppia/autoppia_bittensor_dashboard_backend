"""
Validator and miner metadata models.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.validation import validate_miner_image_url


class ValidatorInfo(BaseModel):
    """Validator metadata persisted with rounds."""

    identifier: str
    uid: int
    hotkey: str
    coldkey: Optional[str] = None
    stake: float = 0.0
    vtrust: float = 0.0
    name: Optional[str] = None
    version: Optional[str] = None
    image: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class MinerInfo(BaseModel):
    """Miner metadata persisted with agent runs."""

    identifier: str
    uid: int
    hotkey: str
    coldkey: Optional[str] = None
    stake: float = 0.0
    incentive: float = 0.0
    trust: float = 0.0
    ip: Optional[str] = None
    port: Optional[int] = None
    agent_name: str = ""
    agent_image: str = ""
    github: str = ""
    is_sota: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_validator("agent_image")
    @classmethod
    def _validate_agent_image(cls, value: str) -> str:
        return validate_miner_image_url(value)

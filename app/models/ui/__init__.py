"""
Lightweight UI response models used by the API routes.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class OverviewResponse(BaseModel):
    success: bool
    overview: Dict[str, Any]
    validator_cards: List[Dict[str, Any]] = Field(default_factory=list)
    live_events: List[Dict[str, Any]] = Field(default_factory=list)


class LeaderboardData(BaseModel):
    type: str
    data: List[Dict[str, Any]]
    limit: int
    offset: int
    sort_by: str
    sort_order: str


class LeaderboardResponse(BaseModel):
    success: bool
    leaderboard: Dict[str, Any]
    total_entries: int
    current_page: int
    total_pages: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentsListData(BaseModel):
    agents: List[Dict[str, Any]]
    total_agents: int
    current_page: int
    total_pages: int


class AgentsListResponse(BaseModel):
    success: bool
    agents: AgentsListData


class MinerDetailsData(BaseModel):
    miner: Dict[str, Any]
    rounds: List[Dict[str, Any]] = Field(default_factory=list)
    validator_cards: List[Dict[str, Any]] = Field(default_factory=list)


class MinerDetailsResponse(BaseModel):
    success: bool
    miner_details: MinerDetailsData


class AgentRunDetailsData(BaseModel):
    agent_run: Dict[str, Any]
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    total_tasks: int
    current_page: int
    total_pages: int


class AgentRunDetailsResponse(BaseModel):
    success: bool
    agent_run_details: AgentRunDetailsData


class TaskDetailsData(BaseModel):
    task: Dict[str, Any]
    round: Dict[str, Any]
    solutions: List[Dict[str, Any]] = Field(default_factory=list)
    evaluations: List[Dict[str, Any]] = Field(default_factory=list)


class TaskDetailsResponse(BaseModel):
    success: bool
    task_details: TaskDetailsData


class AnalyticsResponse(BaseModel):
    success: bool
    analytics: Dict[str, Any]


__all__ = [
    "OverviewResponse",
    "LeaderboardData",
    "LeaderboardResponse",
    "AgentsListData",
    "AgentsListResponse",
    "MinerDetailsData",
    "MinerDetailsResponse",
    "AgentRunDetailsData",
    "AgentRunDetailsResponse",
    "TaskDetailsData",
    "TaskDetailsResponse",
    "AnalyticsResponse",
]

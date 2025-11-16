from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.agents import (
    ActivityType,
    AgentStatus,
    AgentType,
)
from app.services.ui.agents_service import (
    AgentsService,
    AgentAggregateCacheWarmupRequired,
)

logger = logging.getLogger(__name__)

CACHE_WARMING_MESSAGE = "Agent aggregate cache is warming; try again shortly."

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _service(session: AsyncSession) -> AgentsService:
    return AgentsService(session)


@router.get("")
async def list_agents(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: AgentType | None = Query(None),
    status: AgentStatus | None = Query(None),
    sortBy: str = Query("name"),
    sortOrder: str = Query("asc"),
    search: str | None = Query(None),
):
    service = await _service(session)
    try:
        data = await service.list_agents(
            page=page,
            limit=limit,
            agent_type=type,
            status=status,
            sort_by=sortBy,
            sort_order=sortOrder,
            search=search,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    return {"success": True, "data": data.model_dump()}


@router.get("/statistics")
async def get_agent_statistics(session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        response = await service.statistics()
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    return {"success": True, "data": {"statistics": response.statistics.model_dump()}}


@router.get("/activity")
async def list_agent_activity(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    type: ActivityType | None = Query(None),
    since: datetime | None = Query(None),
    agentId: Optional[str] = Query(None),
):
    service = await _service(session)
    try:
        response = await service.get_all_activity(
            limit=limit,
            offset=offset,
            activity_type=type,
            since=since,
            agent_id=agentId,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    return {"success": True, "data": response.model_dump()}


@router.post("/compare")
async def compare_agents(payload: dict, session: AsyncSession = Depends(get_session)):
    agent_ids = payload.get("agentIds", [])
    if not isinstance(agent_ids, list) or not agent_ids:
        raise HTTPException(status_code=400, detail="agentIds must be a non-empty list")
    service = await _service(session)
    try:
        response = await service.compare_agents(agent_ids)
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": response.model_dump()}


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    round: Optional[int] = Query(None),
):
    service = await _service(session)
    try:
        data = await service.get_agent(agent_id, round_number=round)
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data.model_dump()}


@router.get("/{agent_id}/performance")
async def get_agent_performance(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    timeRange: Optional[str] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    granularity: Optional[str] = Query(None),
):
    service = await _service(session)
    try:
        response = await service.get_performance(
            agent_id,
            time_range=timeRange,
            start_date=startDate,
            end_date=endDate,
            granularity=granularity,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": {"metrics": response.metrics.model_dump()}}


@router.get("/{agent_id}/runs")
async def list_agent_runs(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    service = await _service(session)
    try:
        data = await service.list_agent_runs(agent_id, page=page, limit=limit)
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    return {"success": True, "data": data.model_dump()}


@router.get("/{agent_id}/activity")
async def get_agent_activity(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    type: ActivityType | None = Query(None),
    since: datetime | None = Query(None),
):
    service = await _service(session)
    try:
        response = await service.get_agent_activity(
            agent_id,
            limit=limit,
            offset=offset,
            activity_type=type,
            since=since,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": response.model_dump()}

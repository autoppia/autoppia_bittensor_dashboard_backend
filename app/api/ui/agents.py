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
from app.services.ui.rounds_service import RoundsService

logger = logging.getLogger(__name__)

CACHE_WARMING_MESSAGE = "Agent aggregate cache is warming; try again shortly."

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _service(session: AsyncSession) -> AgentsService:
    return AgentsService(session)


async def _rounds_service(session: AsyncSession) -> RoundsService:
    return RoundsService(session)


@router.get("")
async def list_agents(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: AgentType | None = Query(None),
    status: AgentStatus | None = Query(None),
    sortBy: str = Query("averageScore"),
    sortOrder: str = Query("desc"),
    search: str | None = Query(None),
):
    """
    List agents with pagination and filtering.
    """
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


@router.get("/latest-round-top-miner")
async def get_latest_round_top_miner(
    session: AsyncSession = Depends(get_session),
):
    """
    Get the latest round number and the top miner (post_consensus_rank = 1) for that round.
    Used for initial redirect when accessing /subnet36/agents without parameters.
    """
    rounds_service = await _rounds_service(session)
    try:
        data = await rounds_service.get_latest_round_and_top_miner()
        if data is None:
            raise HTTPException(status_code=404, detail="No rounds available")
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting latest round and top miner: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rounds")
async def get_rounds_data(
    round_number: Optional[int] = Query(None, description="Round number to get miners for"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get available rounds and miners for a selected round (or first round if none selected).
    
    Returns: 
    {
        "rounds": [round_number, ...],
        "round_selected": {
            "round": round_number,
            "miners": [...]
        } | null
    }
    """
    rounds_service = await _rounds_service(session)
    try:
        # Get all available rounds
        rounds = await rounds_service.get_available_rounds()
        
        # Determine which round to get miners for
        target_round = round_number
        if target_round is None and rounds:
            # If no round specified, use the first (latest) round
            target_round = rounds[0]
        
        # Get miners for target round if available
        round_selected = None
        if target_round is not None:
            round_selected = await rounds_service.get_round_miners_for_autoppia(target_round)
        
        return {
            "success": True,
            "data": {
                "rounds": rounds,
                "round_selected": round_selected,
            }
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting rounds data: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/round-details")
async def get_miner_round_details(
    round: int = Query(..., description="Round number"),
    miner_uid: int = Query(..., description="Miner UID"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get detailed information about a specific miner in a specific round.
    
    Returns miner info, post-consensus metrics, tasks statistics, and performance by website.
    """
    rounds_service = await _rounds_service(session)
    try:
        data = await rounds_service.get_miner_round_details(round, miner_uid)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting miner round details: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{miner_uid}/historical")
async def get_miner_historical(
    miner_uid: int,
    session: AsyncSession = Depends(get_session),
):
    """
    Get historical statistics for a miner across all rounds.
    
    Returns:
        - Summary statistics (rounds won/lost, total tasks, etc.)
        - Performance by website with use cases breakdown
        - Rounds history
        - Alpha earned calculation (ALPHA_EMISSION_PER_EPOCH * round_epochs * weight, then convert to TAO)
    """
    rounds_service = await _rounds_service(session)
    try:
        data = await rounds_service.get_miner_historical(miner_uid)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting miner historical data: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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

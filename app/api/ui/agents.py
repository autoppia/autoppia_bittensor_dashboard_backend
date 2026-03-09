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
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


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
    newdb = UIDataService(session)
    data = await newdb.list_agents_catalog(
        page=page,
        limit=limit,
        sort_by=sortBy,
        sort_order=sortOrder,
        search=search,
    )
    if type is not None:
        data["agents"] = [a for a in data.get("agents", []) if str(type.value) in ("autoppia", "custom")]
        data["total"] = len(data["agents"])
    if status is not None and status.value != "active":
        data["agents"] = []
        data["total"] = 0
    return {"success": True, "data": data}


@router.get("/latest-round-top-miner")
async def get_latest_round_top_miner(
    session: AsyncSession = Depends(get_session),
):
    """
    Get latest round and top miner for initial redirect when accessing /subnet36/agents.

    Returns JSON { season, round, miner_uid, miner_hotkey }.
    Frontend performs the redirect. Only considers Autoppia validators (83, 124, 60).
    """
    from app.services.redis_cache import redis_cache

    # Try Redis cache first
    cache_key = "latest_round_top_miner_data"
    cached = redis_cache.get(cache_key)
    if cached is not None:
        return {"success": True, "data": cached}

    # Cache miss - fetch from database
    newdb = UIDataService(session)
    try:
        data = await newdb.get_latest_round_top_miner()
        if data is None:
            # No rounds available: return 200 with null so frontend can use fallback (e.g. rounds list)
            return {"success": True, "data": None}

        payload = {
            "season": data["season"],
            "round": data["round"],
            "miner_uid": data["miner_uid"],
            "miner_hotkey": data.get("miner_hotkey"),
        }
        redis_cache.set(cache_key, payload, ttl=30)

        return {"success": True, "data": payload}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting latest round and top miner: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rounds")
async def get_rounds_data(
    season: Optional[int] = Query(None, description="Season number"),
    round_number: Optional[int] = Query(None, description="Round number (compat alias)"),
    round_identifier: Optional[str] = Query(None, description="Round in format 'season/round' (e.g. '83/20')"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get available rounds and miners for a selected round (or first round if none selected).

    Use round_identifier (e.g. "83/20") when available; round_number is a compat alias.

    Returns:
    {
        "rounds": ["season/round", ...],
        "round_selected": {
            "round": "season/round",
            "miners": [...]
        } | null
    }
    """
    newdb = UIDataService(session)
    try:
        # Get all available rounds
        rounds = await newdb.get_available_rounds()

        latest_season = await newdb.get_latest_season_number()

        # Prefer round_identifier (season/round), else season aggregate, else round_number (compat alias)
        target_round = round_identifier if round_identifier else round_number
        target_season = season if season is not None else latest_season

        # Get miners for target selection if available
        round_selected = None
        if target_round is not None:
            if isinstance(target_round, str) and "/" in target_round:
                season_s, round_s = target_round.split("/", 1)
                round_selected = await newdb.get_round_miners(int(season_s), int(round_s))
            else:
                # Numeric alias without season is not canonical in the new schema.
                round_selected = None
        elif target_season is not None:
            round_selected = await newdb.get_season_miners(int(target_season))

        return {
            "success": True,
            "data": {
                "rounds": rounds,
                "round_selected": round_selected,
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting rounds data: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/seasons/{season_ref}/rank")
async def get_season_rank(
    season_ref: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Get season ranking payload for agents UI.

    `season_ref` can be a numeric season or `latest`.
    Returns available seasons and the ranking for the selected season.
    """
    newdb = UIDataService(session)
    try:
        data = await newdb.get_agents_season_rank(season_ref)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting season rank data: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/round-details")
async def get_miner_round_details(
    round: str = Query(..., description="Round identifier in format 'season/round' (e.g., '1/1') or encoded number"),
    miner_uid: int = Query(..., description="Miner UID"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get detailed information about a specific miner in a specific round.

    Returns miner info, post-consensus metrics, tasks statistics, and performance by website.
    """
    newdb = UIDataService(session)
    try:
        if isinstance(round, str) and "/" in round:
            season_s, round_s = round.split("/", 1)
            data = await newdb.get_agent_detail(miner_uid, int(season_s), int(round_s))
        else:
            data = await newdb.get_agent_detail(miner_uid, None, None)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting miner round details: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{miner_uid}/historical")
async def get_miner_historical(
    miner_uid: int,
    season: Optional[int] = Query(None, description="Optional season number to filter historical data"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get historical statistics for a miner across all rounds or for a specific season.

    Returns:
        - Summary statistics (rounds won/lost, total tasks, etc.)
        - Performance by website with use cases breakdown
        - Rounds history
        - Alpha earned calculation (ALPHA_EMISSION_PER_EPOCH * round_epochs * weight, then convert to TAO)
    """
    newdb = UIDataService(session)
    try:
        data = await newdb.get_miner_historical(miner_uid, season=season)
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
    season: Optional[int] = Query(None),
):
    newdb = UIDataService(session)
    try:
        uid = int(str(agent_id).replace("agent-", ""))
        data = await newdb.get_agent_detail(uid, season=season, round_in_season=round)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/{agent_id}/performance")
async def get_agent_performance(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    timeRange: Optional[str] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    granularity: Optional[str] = Query(None),
):
    newdb = UIDataService(session)
    try:
        metrics = await newdb.get_agent_performance_metrics(
            agent_id=agent_id,
            start_date=startDate,
            end_date=endDate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": {"metrics": metrics}}


@router.get("/{agent_id}/runs-by-round")
async def get_agent_runs_by_round(
    agent_id: str,
    season: Optional[int] = Query(None, description="Season number. Defaults to latest season."),
    session: AsyncSession = Depends(get_session),
):
    """
    Return all rounds where the agent participated (for a given season),
    grouped by round. Each round includes:
    - consensus: stake-weighted aggregated metrics
    - validators[]: each validator's individual run for this agent
    Used by the 'Runs' tab on the agent page.
    """
    newdb = UIDataService(session)
    try:
        data = await newdb.get_agent_runs_by_round(agent_id=agent_id, season=season)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error getting agent runs by round: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/{agent_id}/runs")
async def list_agent_runs(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    newdb = UIDataService(session)
    try:
        data = await newdb.list_agent_runs_for_agent(agent_id=agent_id, page=page, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/{agent_id}/activity")
async def get_agent_activity(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    type: ActivityType | None = Query(None),
    since: datetime | None = Query(None),
):
    newdb = UIDataService(session)
    try:
        data = await newdb.get_agent_activity_feed(
            agent_id=agent_id,
            limit=limit,
            offset=offset,
            activity_type=type.value if type else None,
            since=since,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}

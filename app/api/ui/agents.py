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
    sortBy: str = Query("averageScore"),
    sortOrder: str = Query("desc"),
    search: str | None = Query(None),
):
    """
    List agents with pagination and filtering.
    Optimized: Uses miner_aggregates_mv materialized view for ultra-fast queries.
    Falls back to AgentStatsORM if MV query fails.
    """
    from app.db.models import MinerAggregatesMV, AgentStatsORM
    from sqlalchemy import select, func, String, cast, or_
    from datetime import timedelta, timezone
    
    try:
        # OPTIMIZED: Try miner_aggregates_mv first (most up-to-date)
        stmt = select(MinerAggregatesMV)
        
        # Build filters
        filters = []
        
        if search:
            search_lower = search.lower()
            filters.append(
                or_(
                    func.lower(MinerAggregatesMV.name).contains(search_lower),
                    cast(MinerAggregatesMV.uid, String).contains(search),
                    func.lower(MinerAggregatesMV.hotkey).contains(search_lower),
                )
            )
        
        if type and type == AgentType.SOTA:
            filters.append(MinerAggregatesMV.is_sota == True)
        elif type and type == AgentType.MINER:
            filters.append(MinerAggregatesMV.is_sota == False)
        
        # Status filter (active = seen in last 7 days)
        if status:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            if status == AgentStatus.ACTIVE:
                filters.append(MinerAggregatesMV.last_seen >= cutoff)
            elif status == AgentStatus.INACTIVE:
                filters.append(
                    (MinerAggregatesMV.last_seen < cutoff) | (MinerAggregatesMV.last_seen == None)
                )
        
        if filters:
            stmt = stmt.where(*filters)
        
        # Sorting
        sort_field_map = {
            "name": MinerAggregatesMV.name,
            "averageScore": MinerAggregatesMV.avg_score,
            "bestScore": MinerAggregatesMV.best_score,
            "totalRuns": MinerAggregatesMV.total_runs,
            "lastSeen": MinerAggregatesMV.last_seen,
            "currentRank": MinerAggregatesMV.current_rank,
        }
        sort_field = sort_field_map.get(sortBy, MinerAggregatesMV.avg_score)
        
        if sortOrder == "desc":
            stmt = stmt.order_by(sort_field.desc().nulls_last())
        else:
            stmt = stmt.order_by(sort_field.asc().nulls_last())
        
        # Count total (optimized: reuse same filters)
        count_stmt = select(func.count(MinerAggregatesMV.uid))
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = await session.scalar(count_stmt) or 0
        
        # Pagination
        stmt = stmt.limit(limit).offset((page - 1) * limit)
        
        # Execute
        miners = list(await session.scalars(stmt))
        
        # Convert to response format
        agents = []
        now = datetime.now(timezone.utc)
        for m in miners:
            # Build recentPerformance from rounds JSONB (last 5 rounds)
            recent_performance = []
            if m.rounds:
                round_keys = sorted(
                    [int(k) for k in m.rounds.keys() if k.isdigit()],
                    reverse=True
                )[:5]
                for rk in round_keys:
                    round_data = m.rounds.get(str(rk), {})
                    if round_data:
                        recent_performance.append({
                            "round": rk,
                            "avgScore": round(float(round_data.get("avgScore", 0.0)), 4),
                            "rank": round_data.get("rank"),
                            "totalRuns": round_data.get("totalRuns", 0),
                        })
            
            # Calculate total rounds from rounds JSONB
            total_rounds = len(m.rounds) if m.rounds else 0
            
            agents.append({
                "id": f"M{m.uid}",
                "uid": m.uid,
                "name": m.name or f"Miner {m.uid}",
                "imageUrl": m.image_url or "",
                "type": "sota" if m.is_sota else "miner",
                "status": "active" if m.last_seen and (now - m.last_seen).days < 7 else "inactive",
                "averageScore": round(m.avg_score, 4),
                "bestScore": round(m.best_score, 4),
                "totalRounds": total_rounds,
                "totalRuns": m.total_runs,
                "successRate": round(m.success_rate, 4) if m.success_rate else 0.0,
                "totalTasks": m.total_tasks,
                "completedTasks": m.completed_tasks,
                "lastSeen": m.last_seen.isoformat() if m.last_seen else None,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
                "recentPerformance": recent_performance,
            })
        
        return {
            "success": True,
            "data": {
                "agents": agents,
                "total": total,
                "page": page,
                "limit": limit,
            },
        }
    
    except Exception as e:
        logger.warning(f"Failed to query miner_aggregates_mv, falling back to AgentStatsORM: {e}")
        # Fallback to AgentStatsORM
        from app.db.models import AgentStatsORM
        
        stmt = select(AgentStatsORM)
        
        # Filtros
        if search:
            search_lower = search.lower()
            stmt = stmt.where(
                func.lower(AgentStatsORM.name).contains(search_lower)
                | cast(AgentStatsORM.uid, String).contains(search)
            )
        
        if type and type == AgentType.SOTA:
            stmt = stmt.where(AgentStatsORM.is_sota == True)
        
        # Status filter (active = seen in last 7 days)
        if status:
            if status == AgentStatus.ACTIVE:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                stmt = stmt.where(AgentStatsORM.last_seen >= cutoff)
            elif status == AgentStatus.INACTIVE:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                stmt = stmt.where(
                    (AgentStatsORM.last_seen < cutoff) | (AgentStatsORM.last_seen == None)
                )
        
        # Sorting
        sort_field_map = {
            "name": AgentStatsORM.name,
            "averageScore": AgentStatsORM.avg_score,
            "totalRounds": AgentStatsORM.total_rounds,
            "lastSeen": AgentStatsORM.last_seen,
        }
        sort_field = sort_field_map.get(sortBy, AgentStatsORM.avg_score)
        
        if sortOrder == "desc":
            stmt = stmt.order_by(sort_field.desc())
        else:
            stmt = stmt.order_by(sort_field.asc())
        
        # Pagination
        stmt = stmt.limit(limit).offset((page - 1) * limit)
        
        # Execute
        stats = list(await session.scalars(stmt))
        
        # Count total (optimized: build same filters)
        count_stmt = select(func.count(AgentStatsORM.uid))
        if search:
            search_lower = search.lower()
            count_stmt = count_stmt.where(
                func.lower(AgentStatsORM.name).contains(search_lower)
                | cast(AgentStatsORM.uid, String).contains(search)
            )
        if type and type == AgentType.SOTA:
            count_stmt = count_stmt.where(AgentStatsORM.is_sota == True)
        if status:
            if status == AgentStatus.ACTIVE:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                count_stmt = count_stmt.where(AgentStatsORM.last_seen >= cutoff)
            elif status == AgentStatus.INACTIVE:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                count_stmt = count_stmt.where(
                    (AgentStatsORM.last_seen < cutoff) | (AgentStatsORM.last_seen == None)
                )
        total = await session.scalar(count_stmt) or 0
        
        # Convert to response format
        agents = []
        for s in stats:
            agents.append({
                "id": f"M{s.uid}",
                "uid": s.uid,
                "name": s.name or f"Miner {s.uid}",
                "imageUrl": s.image_url,
                "type": "sota" if s.is_sota else "miner",
                "status": "active" if s.last_seen and (datetime.now(timezone.utc) - s.last_seen).days < 7 else "inactive",
                "averageScore": round(s.avg_score, 4),
                "bestScore": round(s.best_score, 4),
                "totalRounds": s.total_rounds,
                "totalRuns": s.total_runs,
                "successRate": round(s.successful_runs / s.total_runs, 4) if s.total_runs else 0.0,
                "totalTasks": s.total_tasks,
                "completedTasks": s.completed_tasks,
                "lastSeen": s.last_seen.isoformat() if s.last_seen else None,
                "createdAt": s.created_at.isoformat() if s.created_at else None,
                "recentPerformance": s.recent_rounds,
            })
        
        return {
            "success": True,
            "data": {
                "agents": agents,
                "total": total,
                "page": page,
                "limit": limit,
            },
        }


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

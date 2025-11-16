from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.miners import Granularity, MinerStatus, TimeRange
from app.services.ui.miners_service import MinersService
from app.services.ui.agents_service import AgentAggregateCacheWarmupRequired

logger = logging.getLogger(__name__)

CACHE_WARMING_MESSAGE = "Agent aggregate cache is warming; try again shortly."

router = APIRouter(prefix="/api/v1/miners", tags=["miners"])


async def _service(session: AsyncSession) -> MinersService:
    return MinersService(session)


@router.get("")
async def list_miners(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    isSota: bool | None = Query(None),
    status: MinerStatus | None = Query(None),
    sortBy: str = Query("averageScore"),
    sortOrder: str = Query("desc"),
    search: str | None = Query(None),
):
    service = await _service(session)
    try:
        data = await service.list_miners(
            page=page,
            limit=limit,
            is_sota=isSota,
            status=status,
            sort_by=sortBy,
            sort_order=sortOrder,
            search=search,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    return {"success": True, "data": data.model_dump()}


@router.get("/{uid}")
async def get_miner(uid: int, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        data = await service.get_miner(uid)
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data.model_dump()}


@router.get("/{uid}/performance")
async def get_miner_performance(
    uid: int,
    session: AsyncSession = Depends(get_session),
    timeRange: TimeRange = Query(TimeRange.SEVEN_DAYS),
    startDate: datetime | None = Query(None),
    endDate: datetime | None = Query(None),
    granularity: Granularity = Query(Granularity.DAY),
):
    service = await _service(session)
    try:
        data = await service.get_miner_performance(
            uid=uid,
            time_range=timeRange,
            start_date=startDate,
            end_date=endDate,
            granularity=granularity,
        )
    except AgentAggregateCacheWarmupRequired as exc:
        raise HTTPException(status_code=503, detail=CACHE_WARMING_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data.model_dump()}

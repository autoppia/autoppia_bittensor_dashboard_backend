from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.miners import Granularity, MinerStatus, TimeRange
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miners", tags=["miners"])


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


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
    data = await service.list_agents_catalog(
        page=page,
        limit=limit,
        sort_by=sortBy,
        sort_order=sortOrder,
        search=search,
    )
    agents = data.get("agents", [])
    if isSota is not None:
        agents = [a for a in agents if bool(a.get("isSota")) is isSota]
    if status is not None and status.value != "active":
        agents = []
    data["agents"] = agents
    data["total"] = len(agents)
    return {"success": True, "data": data}


@router.get("/{uid}")
async def get_miner(uid: int, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        data = await service.get_agent_detail(uid, season=None, round_in_season=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}


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
        data = await service.get_agent_performance_metrics(
            agent_id=f"agent-{uid}",
            start_date=startDate,
            end_date=endDate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}

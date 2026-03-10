from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.miners import Granularity, MinerStatus, TimeRange
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miners", tags=["miners"])


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query models (Sonar: reduce endpoint params)
# ---------------------------------------------------------------------------


class MinersListQuery(BaseModel):
    """Query params for list_miners."""

    page: int = 1
    limit: int = 20
    isSota: Optional[bool] = None
    status: Optional[MinerStatus] = None
    sortBy: str = "averageScore"
    sortOrder: str = "desc"
    search: Optional[str] = None

    model_config = {"extra": "forbid"}


def get_miners_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    isSota: Annotated[bool | None, Query(None)] = None,
    status: Annotated[MinerStatus | None, Query(None)] = None,
    sortBy: Annotated[str, Query("averageScore")] = "averageScore",
    sortOrder: Annotated[str, Query("desc")] = "desc",
    search: Annotated[str | None, Query(None)] = None,
) -> MinersListQuery:
    return MinersListQuery(
        page=page,
        limit=limit,
        isSota=isSota,
        status=status,
        sortBy=sortBy,
        sortOrder=sortOrder,
        search=search,
    )


class MinerPerformanceQuery(BaseModel):
    """Query params for get_miner_performance."""

    timeRange: TimeRange = TimeRange.SEVEN_DAYS
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    granularity: Granularity = Granularity.DAY

    model_config = {"extra": "forbid"}


def get_miner_performance_query(
    timeRange: Annotated[TimeRange, Query(TimeRange.SEVEN_DAYS)] = TimeRange.SEVEN_DAYS,
    startDate: Annotated[datetime | None, Query(None)] = None,
    endDate: Annotated[datetime | None, Query(None)] = None,
    granularity: Annotated[Granularity, Query(Granularity.DAY)] = Granularity.DAY,
) -> MinerPerformanceQuery:
    return MinerPerformanceQuery(
        timeRange=timeRange,
        startDate=startDate,
        endDate=endDate,
        granularity=granularity,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_miners(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[MinersListQuery, Depends(get_miners_list_query)],
):
    service = await _service(session)
    data = await service.list_agents_catalog(
        page=q.page,
        limit=q.limit,
        sort_by=q.sortBy,
        sort_order=q.sortOrder,
        search=q.search,
    )
    agents = data.get("agents", [])
    if q.isSota is not None:
        agents = [a for a in agents if bool(a.get("isSota")) is q.isSota]
    if q.status is not None and q.status.value != "active":
        agents = []
    data["agents"] = agents
    data["total"] = len(agents)
    return {"success": True, "data": data}


@router.get("/{uid}")
async def get_miner(
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = await _service(session)
    try:
        data = await service.get_agent_detail(uid, season=None, round_in_season=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/{uid}/performance")
async def get_miner_performance(
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[MinerPerformanceQuery, Depends(get_miner_performance_query)],
):
    service = await _service(session)
    try:
        data = await service.get_agent_performance_metrics(
            agent_id=f"agent-{uid}",
            start_date=q.startDate,
            end_date=q.endDate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}

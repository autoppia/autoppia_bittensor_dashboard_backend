from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.miners import Granularity, MinerStatus, TimeRange
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miners", tags=["miners"])


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query models for miners endpoints
# ---------------------------------------------------------------------------


class MinersListQuery(BaseModel):
    """Query params for list_miners."""

    page: int = 1
    limit: int = 20
    is_sota: bool | None = None
    status: MinerStatus | None = None
    sort_by: str = "averageScore"
    sort_order: str = "desc"
    search: str | None = None

    model_config = {"extra": "forbid"}


def get_miners_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    is_sota: Annotated[bool | None, Query(None, alias="isSota")] = None,
    status: Annotated[MinerStatus | None, Query(None)] = None,
    sort_by: Annotated[str, Query("averageScore", alias="sortBy")] = "averageScore",
    sort_order: Annotated[str, Query("desc", alias="sortOrder")] = "desc",
    search: Annotated[str | None, Query(None)] = None,
) -> MinersListQuery:
    return MinersListQuery(
        page=page,
        limit=limit,
        is_sota=is_sota,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        search=search,
    )


class MinerPerformanceQuery(BaseModel):
    """Query params for get_miner_performance."""

    time_range: TimeRange = TimeRange.SEVEN_DAYS
    start_date: datetime | None = None
    end_date: datetime | None = None
    granularity: Granularity = Granularity.DAY

    model_config = {"extra": "forbid"}


def get_miner_performance_query(
    time_range: Annotated[TimeRange, Query(TimeRange.SEVEN_DAYS, alias="timeRange")] = TimeRange.SEVEN_DAYS,
    start_date: Annotated[datetime | None, Query(None, alias="startDate")] = None,
    end_date: Annotated[datetime | None, Query(None, alias="endDate")] = None,
    granularity: Annotated[Granularity, Query(Granularity.DAY)] = Granularity.DAY,
) -> MinerPerformanceQuery:
    return MinerPerformanceQuery(
        time_range=time_range,
        start_date=start_date,
        end_date=end_date,
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
    service = _service(session)
    data = await service.list_agents_catalog(
        page=q.page,
        limit=q.limit,
        sort_by=q.sort_by,
        sort_order=q.sort_order,
        search=q.search,
    )
    agents = data.get("agents", [])
    if q.is_sota is not None:
        agents = [a for a in agents if bool(a.get("isSota")) is q.is_sota]
    if q.status is not None and q.status.value != "active":
        agents = []
    data["agents"] = agents
    data["total"] = len(agents)
    return {"success": True, "data": data}


@router.get("/{uid}", responses={404: {"description": "Miner not found"}})
async def get_miner(
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _service(session)
    try:
        data = await service.get_agent_detail(uid, season=None, round_in_season=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/{uid}/performance", responses={404: {"description": "Miner not found"}})
async def get_miner_performance(
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[MinerPerformanceQuery, Depends(get_miner_performance_query)],
):
    service = _service(session)
    try:
        data = await service.get_agent_performance_metrics(
            agent_id=f"agent-{uid}",
            start_date=q.start_date,
            end_date=q.end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": data}

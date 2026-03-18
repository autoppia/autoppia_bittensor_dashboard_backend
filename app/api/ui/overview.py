from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.overview import (
    CurrentRoundResponse,
    LeaderboardResponse,
    NetworkStatusResponse,
    OverviewMetricsResponse,
    PerformanceTrendsResponse,
    RecentActivityResponse,
    RoundDetailResponse,
    RoundsListResponse,
    StatisticsResponse,
    ValidatorDetailResponse,
    ValidatorsFilterResponse,
    ValidatorsListResponse,
)
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query models for the overview endpoints
# ---------------------------------------------------------------------------


class ValidatorsListQuery(BaseModel):
    """Query params for get_validators."""

    page: int = 1
    limit: int = 10
    status: str | None = None
    sort_by: str = "weight"
    sort_order: str = "desc"

    model_config = {"extra": "forbid"}


def get_validators_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(10, ge=1, le=100)] = 10,
    status: Annotated[str | None, Query(None)] = None,
    sort_by: Annotated[str, Query("weight", alias="sortBy")] = "weight",
    sort_order: Annotated[str, Query("desc", alias="sortOrder")] = "desc",
) -> ValidatorsListQuery:
    return ValidatorsListQuery(page=page, limit=limit, status=status, sort_by=sort_by, sort_order=sort_order)


class RoundsListQuery(BaseModel):
    """Query params for list_rounds."""

    page: int = 1
    limit: int = 10
    status: str | None = None

    model_config = {"extra": "forbid"}


def get_rounds_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(10, ge=1, le=100)] = 10,
    status: Annotated[str | None, Query(None)] = None,
) -> RoundsListQuery:
    return RoundsListQuery(page=page, limit=limit, status=status)


class LeaderboardQuery(BaseModel):
    """Query params for get_leaderboard (API accepts timeRange alias)."""

    time_range: str | None = None
    limit: int | None = None

    model_config = {"extra": "forbid"}


def get_leaderboard_query(
    time_range: Annotated[str | None, Query(None, alias="timeRange")] = None,
    limit: Annotated[int | None, Query(None, ge=1, le=365)] = None,
) -> LeaderboardQuery:
    return LeaderboardQuery(time_range=time_range, limit=limit)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/metrics")
@cache("overview_metrics", ttl=900)
async def get_overview_metrics(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OverviewMetricsResponse:
    """
    Get overview metrics. Cached for 15 minutes and pre-warmed by background worker.

    The cache warmer thread calls this endpoint periodically, ensuring the cache
    is always populated before users request it (zero cold starts).
    """
    service = _service(session)
    metrics = await service.get_overview_metrics()
    return OverviewMetricsResponse(success=True, data={"metrics": metrics})


@router.get("/validators")
@cache("validators_list", ttl=180)  # Cache 3 minutes - mantenido caliente por el cache warmer
async def get_validators(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[ValidatorsListQuery, Depends(get_validators_list_query)],
) -> ValidatorsListResponse:
    service = _service(session)
    validators, total = await service.get_overview_validators_list(
        page=q.page,
        limit=q.limit,
        status=q.status,
        sort_by=q.sort_by,
        sort_order=q.sort_order,
    )
    return ValidatorsListResponse(
        success=True,
        data={
            "validators": validators,
            "total": total,
            "page": q.page,
            "limit": q.limit,
        },
    )


@router.get("/validators/filter")
async def get_validators_filter(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ValidatorsFilterResponse:
    service = _service(session)
    items = await service.get_overview_validators_filter()
    return ValidatorsFilterResponse(success=True, data={"validators": items})


@router.get("/validators/{validator_id}", responses={404: {"description": "Validator not found"}})
@cache("validator_detail", ttl=180)  # Cache 3 minutes - similar to validators_list
async def get_validator_detail(
    validator_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ValidatorDetailResponse:
    service = _service(session)
    try:
        validator = await service.get_overview_validator_detail(validator_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValidatorDetailResponse(success=True, data={"validator": validator})


@router.get("/rounds/current", responses={404: {"description": "No rounds available"}})
@cache("current_round", ttl=300)  # Cache 5 minutes - current round changes more frequently
async def get_current_round(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentRoundResponse:
    service = _service(session)
    round_info = await service.get_overview_current_round()
    if round_info is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return CurrentRoundResponse(success=True, data={"round": round_info})


@router.get("/rounds")
async def list_rounds(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[RoundsListQuery, Depends(get_rounds_list_query)],
) -> RoundsListResponse:
    service = _service(session)
    rounds, current, total = await service.get_overview_rounds_list(page=q.page, limit=q.limit, status=q.status)
    return RoundsListResponse(
        success=True,
        data={
            "rounds": rounds,
            "currentRound": current,
            "total": total,
        },
    )


@router.get("/rounds/{validator_round_id}", responses={404: {"description": "Round not found"}})
async def get_round_detail(
    validator_round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundDetailResponse:
    service = _service(session)
    try:
        round_info = await service.get_overview_round_detail(validator_round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundDetailResponse(success=True, data={"round": round_info})


@router.get("/leaderboard")
@cache("leaderboard", ttl=600)  # Cache 10 minutes - standardized for consistent performance
async def get_leaderboard(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[LeaderboardQuery, Depends(get_leaderboard_query)],
) -> LeaderboardResponse:
    service = _service(session)
    entries, time_window = await service.get_overview_leaderboard(limit=q.limit)
    return LeaderboardResponse(
        success=True,
        data={
            "leaderboard": entries,
            "total": len(entries),
            "timeRange": time_window,
        },
    )


@router.get("/statistics")
@cache("statistics", ttl=600)  # Cache 10 minutes - standardized for consistent performance
async def get_statistics(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatisticsResponse:
    service = _service(session)
    stats = await service.get_overview_statistics()
    return StatisticsResponse(success=True, data={"statistics": stats})


@router.get("/network-status")
@cache("network_status", ttl=600)  # Cache 10 minutes - increased for performance
async def get_network_status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NetworkStatusResponse:
    service = _service(session)
    status = await service.get_overview_network_status()
    return NetworkStatusResponse(success=True, data=status)


@router.get("/recent-activity")
async def get_recent_activity(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(10, ge=1, le=100)] = 10,
) -> RecentActivityResponse:
    service = _service(session)
    activities = await service.get_overview_recent_activity(limit)
    return RecentActivityResponse(
        success=True,
        data={
            "activities": activities,
            "total": len(activities),
        },
    )


@router.get("/performance-trends")
async def get_performance_trends(
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(7, ge=1, le=30)] = 7,
) -> PerformanceTrendsResponse:
    service = _service(session)
    trends = await service.get_overview_performance_trends(days)
    return PerformanceTrendsResponse(
        success=True,
        data={
            "trends": trends,
            "period": f"{days} days",
        },
    )

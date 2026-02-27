from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("/metrics", response_model=OverviewMetricsResponse)
@cache("overview_metrics", ttl=900)
async def get_overview_metrics(
    session: AsyncSession = Depends(get_session),
) -> OverviewMetricsResponse:
    """
    Get overview metrics. Cached for 15 minutes and pre-warmed by background worker.

    The cache warmer thread calls this endpoint periodically, ensuring the cache
    is always populated before users request it (zero cold starts).
    """
    newdb = UIDataService(session)
    metrics = await newdb.get_overview_metrics()
    return OverviewMetricsResponse(success=True, data={"metrics": metrics})


@router.get("/validators", response_model=ValidatorsListResponse)
@cache("validators_list", ttl=180)  # Cache 3 minutes - mantenido caliente por el cache warmer
async def get_validators(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    sortBy: str = Query("weight"),
    sortOrder: str = Query("desc"),
) -> ValidatorsListResponse:
    newdb = UIDataService(session)
    validators, total = await newdb.get_overview_validators_list(
        page=page,
        limit=limit,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return ValidatorsListResponse(
        success=True,
        data={
            "validators": validators,
            "total": total,
            "page": page,
            "limit": limit,
        },
    )


@router.get("/validators/filter", response_model=ValidatorsFilterResponse)
async def get_validators_filter(
    session: AsyncSession = Depends(get_session),
) -> ValidatorsFilterResponse:
    newdb = UIDataService(session)
    items = await newdb.get_overview_validators_filter()
    return ValidatorsFilterResponse(success=True, data={"validators": items})


@router.get("/validators/{validator_id}", response_model=ValidatorDetailResponse)
@cache("validator_detail", ttl=180)  # Cache 3 minutes - similar to validators_list
async def get_validator_detail(validator_id: str, session: AsyncSession = Depends(get_session)) -> ValidatorDetailResponse:
    newdb = UIDataService(session)
    try:
        validator = await newdb.get_overview_validator_detail(validator_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValidatorDetailResponse(success=True, data={"validator": validator})


@router.get("/rounds/current", response_model=CurrentRoundResponse)
@cache("current_round", ttl=300)  # Cache 5 minutes - current round changes more frequently
async def get_current_round(
    session: AsyncSession = Depends(get_session),
) -> CurrentRoundResponse:
    newdb = UIDataService(session)
    round_info = await newdb.get_overview_current_round()
    if round_info is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return CurrentRoundResponse(success=True, data={"round": round_info})


@router.get("/rounds", response_model=RoundsListResponse)
async def list_rounds(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
) -> RoundsListResponse:
    newdb = UIDataService(session)
    rounds, current, total = await newdb.get_overview_rounds_list(page=page, limit=limit, status=status)
    return RoundsListResponse(
        success=True,
        data={
            "rounds": rounds,
            "currentRound": current,
            "total": total,
        },
    )


@router.get("/rounds/{validator_round_id}", response_model=RoundDetailResponse)
async def get_round_detail(
    validator_round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundDetailResponse:
    newdb = UIDataService(session)
    try:
        round_info = await newdb.get_overview_round_detail(validator_round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundDetailResponse(success=True, data={"round": round_info})


@router.get("/leaderboard", response_model=LeaderboardResponse)
@cache("leaderboard", ttl=600)  # Cache 10 minutes - standardized for consistent performance
async def get_leaderboard(
    session: AsyncSession = Depends(get_session),
    time_range: Optional[str] = Query(None, alias="timeRange"),
    limit: Optional[int] = Query(None, ge=1, le=365),
) -> LeaderboardResponse:
    newdb = UIDataService(session)
    entries, time_window = await newdb.get_overview_leaderboard(limit=limit)
    return LeaderboardResponse(
        success=True,
        data={
            "leaderboard": entries,
            "total": len(entries),
            "timeRange": time_window,
        },
    )


@router.get("/statistics", response_model=StatisticsResponse)
@cache("statistics", ttl=600)  # Cache 10 minutes - standardized for consistent performance
async def get_statistics(
    session: AsyncSession = Depends(get_session),
) -> StatisticsResponse:
    newdb = UIDataService(session)
    stats = await newdb.get_overview_statistics()
    return StatisticsResponse(success=True, data={"statistics": stats})


@router.get("/network-status", response_model=NetworkStatusResponse)
@cache("network_status", ttl=600)  # Cache 10 minutes - increased for performance
async def get_network_status(
    session: AsyncSession = Depends(get_session),
) -> NetworkStatusResponse:
    newdb = UIDataService(session)
    status = await newdb.get_overview_network_status()
    return NetworkStatusResponse(success=True, data=status)


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def get_recent_activity(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(10, ge=1, le=100),
) -> RecentActivityResponse:
    newdb = UIDataService(session)
    activities = await newdb.get_overview_recent_activity(limit)
    return RecentActivityResponse(
        success=True,
        data={
            "activities": activities,
            "total": len(activities),
        },
    )


@router.get("/performance-trends", response_model=PerformanceTrendsResponse)
async def get_performance_trends(
    session: AsyncSession = Depends(get_session),
    days: int = Query(7, ge=1, le=30),
) -> PerformanceTrendsResponse:
    newdb = UIDataService(session)
    trends = await newdb.get_overview_performance_trends(days)
    return PerformanceTrendsResponse(
        success=True,
        data={
            "trends": trends,
            "period": f"{days} days",
        },
    )

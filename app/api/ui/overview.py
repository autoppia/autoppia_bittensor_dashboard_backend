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
    ValidatorsListResponse,
    ValidatorsFilterResponse,
)
from app.services.ui.overview_service import OverviewService
from app.services.redis_cache import cache, redis_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])


async def _service(session: AsyncSession) -> OverviewService:
    return OverviewService(session)


@router.get("", response_model=OverviewMetricsResponse)
@cache(
    "overview", ttl=600
)  # Cache 10 minutes - standardized for consistent performance
async def get_overview(
    session: AsyncSession = Depends(get_session),
) -> OverviewMetricsResponse:
    service = await _service(session)
    metrics = await service.overview_metrics()
    return OverviewMetricsResponse(success=True, data={"metrics": metrics})


@router.get("/metrics", response_model=OverviewMetricsResponse)
@cache(
    "overview_metrics", ttl=600
)  # Cache 10 minutes - pre-warmed by background worker
async def get_overview_metrics(
    session: AsyncSession = Depends(get_session),
) -> OverviewMetricsResponse:
    """
    Get overview metrics. Cached for 10 minutes and pre-warmed by background worker.

    The cache warmer thread calls this endpoint every 5 minutes, ensuring the cache
    is always populated before users request it (zero cold starts).
    """
    service = await _service(session)
    metrics = await service.overview_metrics()
    return OverviewMetricsResponse(success=True, data={"metrics": metrics})


@router.get("/validators", response_model=ValidatorsListResponse)
@cache(
    "validators_list", ttl=600
)  # Cache 10 minutes - standardized for consistent performance
async def get_validators(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    sortBy: str = Query("weight"),
    sortOrder: str = Query("desc"),
) -> ValidatorsListResponse:
    service = await _service(session)
    validators, total = await service.validators_list(
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
    service = await _service(session)
    items = await service.validators_filter()
    return ValidatorsFilterResponse(success=True, data={"validators": items})


@router.get("/validators/{validator_id}", response_model=ValidatorDetailResponse)
async def get_validator_detail(
    validator_id: str, session: AsyncSession = Depends(get_session)
) -> ValidatorDetailResponse:
    service = await _service(session)
    try:
        validator = await service.validator_detail(validator_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ValidatorDetailResponse(success=True, data={"validator": validator})


@router.get("/rounds/current", response_model=CurrentRoundResponse)
@cache(
    "current_round", ttl=300
)  # Cache 5 minutes - current round changes more frequently
async def get_current_round(
    session: AsyncSession = Depends(get_session),
) -> CurrentRoundResponse:
    service = await _service(session)
    round_info = await service.current_round()
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
    service = await _service(session)
    rounds, current, total = await service.rounds_list(
        page=page, limit=limit, status=status
    )
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
    service = await _service(session)
    try:
        round_info = await service.round_detail(validator_round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundDetailResponse(success=True, data={"round": round_info})


@router.get("/leaderboard", response_model=LeaderboardResponse)
@cache(
    "leaderboard", ttl=600
)  # Cache 10 minutes - standardized for consistent performance
async def get_leaderboard(
    session: AsyncSession = Depends(get_session),
    time_range: Optional[str] = Query(None, alias="timeRange"),
    limit: Optional[int] = Query(None, ge=1, le=365),
) -> LeaderboardResponse:
    service = await _service(session)
    entries, time_window = await service.leaderboard(time_range=time_range, limit=limit)
    return LeaderboardResponse(
        success=True,
        data={
            "leaderboard": entries,
            "total": len(entries),
            "timeRange": time_window,
        },
    )


@router.get("/statistics", response_model=StatisticsResponse)
@cache(
    "statistics", ttl=600
)  # Cache 10 minutes - standardized for consistent performance
async def get_statistics(
    session: AsyncSession = Depends(get_session),
) -> StatisticsResponse:
    service = await _service(session)
    stats = await service.statistics()
    return StatisticsResponse(success=True, data={"statistics": stats})


@router.get("/network-status", response_model=NetworkStatusResponse)
@cache("network_status", ttl=600)  # Cache 10 minutes - increased for performance
async def get_network_status(
    session: AsyncSession = Depends(get_session),
) -> NetworkStatusResponse:
    service = await _service(session)
    status = await service.network_status()
    return NetworkStatusResponse(success=True, data=status)


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def get_recent_activity(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(10, ge=1, le=100),
) -> RecentActivityResponse:
    service = await _service(session)
    activities = await service.recent_activity(limit)
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
    service = await _service(session)
    trends = await service.performance_trends(days)
    return PerformanceTrendsResponse(
        success=True,
        data={
            "trends": trends,
            "period": f"{days} days",
        },
    )

from __future__ import annotations

import logging
from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.core import AgentEvaluationRunWithDetails
from app.models.ui.rounds import (
    RoundActivityResponse,
    RoundComparisonRequest,
    RoundComparisonResponse,
    RoundDetailResponse,
    RoundMinersResponse,
    RoundProgressResponse,
    RoundStatisticsResponse,
    RoundSummaryResponse,
    RoundTimelineResponse,
    RoundValidatorsResponse,
)
from app.services.chain_state import get_current_block_estimate
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds"])


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


async def _round_detail_from_identifier(service: UIDataService, round_id: str) -> dict:
    raw = str(round_id).strip()
    if "/" in raw:
        season_s, round_s = raw.split("/", 1)
        return await service.get_round_detail(int(season_s), int(round_s))
    parsed = int(raw)
    if parsed >= 10000 and (parsed % 10000) > 0:
        return await service.get_round_detail(parsed // 10000, parsed % 10000)
    return await service.get_round_detail_by_round_id(parsed)


def _find_validator_in_data(data: dict, validator_id: str) -> Any:
    """Find a validator in round validators data by id or uid (Sonar: deduplicate)."""
    for item in data.get("validators", []):
        item_id = str(item.get("id", ""))
        item_uid = item_id.replace("validator-", "")
        if validator_id == item_id or validator_id == item_uid:
            return item
    return None


# ---------------------------------------------------------------------------
# Query models (Sonar: reduce endpoint params)
# ---------------------------------------------------------------------------


class RoundIdsQuery(BaseModel):
    limit: int = 500
    status: Optional[str] = None
    sortOrder: str = "desc"
    model_config = {"extra": "forbid"}


def get_round_ids_query(
    limit: Annotated[int, Query(500, ge=1, le=1000)] = 500,
    status: Annotated[Optional[str], Query(None)] = None,
    sortOrder: Annotated[str, Query("desc")] = "desc",
) -> RoundIdsQuery:
    return RoundIdsQuery(limit=limit, status=status, sortOrder=sortOrder)


class RoundsListQuery(BaseModel):
    page: int = 1
    limit: int = 10
    status: Optional[str] = None
    sortBy: str = "round"
    sortOrder: str = "desc"
    skip: Optional[int] = None
    model_config = {"extra": "forbid"}


def get_rounds_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(10, ge=1, le=100)] = 10,
    status: Annotated[Optional[str], Query(None)] = None,
    sortBy: Annotated[str, Query("round")] = "round",
    sortOrder: Annotated[str, Query("desc")] = "desc",
    skip: Annotated[Optional[int], Query(None, ge=0)] = None,
) -> RoundsListQuery:
    return RoundsListQuery(page=page, limit=limit, status=status, sortBy=sortBy, sortOrder=sortOrder, skip=skip)


class RoundMinersQuery(BaseModel):
    page: int = 1
    limit: int = 20
    sortBy: str = "score"
    sortOrder: str = "desc"
    success: Optional[bool] = None
    minScore: Optional[float] = None
    maxScore: Optional[float] = None
    model_config = {"extra": "forbid"}


def get_round_miners_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    sortBy: Annotated[str, Query("score")] = "score",
    sortOrder: Annotated[str, Query("desc")] = "desc",
    success: Annotated[Optional[bool], Query(None)] = None,
    minScore: Annotated[Optional[float], Query(None)] = None,
    maxScore: Annotated[Optional[float], Query(None)] = None,
) -> RoundMinersQuery:
    return RoundMinersQuery(
        page=page, limit=limit, sortBy=sortBy, sortOrder=sortOrder,
        success=success, minScore=minScore, maxScore=maxScore,
    )


class RoundActivityQuery(BaseModel):
    limit: int = 20
    offset: int = 0
    activity_type: Optional[str] = None
    since: Optional[str] = None
    model_config = {"extra": "forbid"}


def get_round_activity_query(
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    offset: Annotated[int, Query(0, ge=0)] = 0,
    activity_type: Annotated[Optional[str], Query(None, alias="type")] = None,
    since: Annotated[Optional[str], Query(None)] = None,
) -> RoundActivityQuery:
    return RoundActivityQuery(limit=limit, offset=offset, activity_type=activity_type, since=since)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/ids")
async def list_round_ids(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[RoundIdsQuery, Depends(get_round_ids_query)],
):
    """
    Get lightweight list of round IDs only (no nested data).
    Much faster than full /rounds endpoint - use this for dropdowns and lists.
    """
    service = await _service(session)
    entries, _ = await service.get_rounds_list(page=1, limit=q.limit)
    round_ids = [int(e.get("id", 0)) for e in entries if int(e.get("id", 0)) > 0]
    return {
        "success": True,
        "data": {
            "roundIds": round_ids,
            "total": len(round_ids),
        },
    }


@router.get("/")
@cache("rounds_list", ttl=600)  # Cache 10 minutes - pre-warmed by background worker
async def list_rounds(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[RoundsListQuery, Depends(get_rounds_list_query)],
):
    service = await _service(session)
    if q.skip is not None:
        page = (q.skip // q.limit) + 1
        offset = q.skip % q.limit
        entries, _ = await service.get_rounds_list(page=page, limit=q.limit)
        sliced = entries[offset:]
        return sliced

    entries, total = await service.get_rounds_list(page=q.page, limit=q.limit)
    current = await service.get_current_round()
    payload = {
        "rounds": entries,
        "total": total,
        "page": q.page,
        "limit": q.limit,
    }
    if current:
        payload["currentRound"] = current
    return {
        "success": True,
        "data": payload,
    }


router.add_api_route(
    "",
    list_rounds,
    methods=["GET"],
    include_in_schema=False,
)


@router.get("/current", response_model=RoundDetailResponse)
@cache("rounds_current", ttl=300)  # Cache 5 minutes - different key to avoid collision with overview
async def get_current_round(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundDetailResponse:
    service = await _service(session)
    current = await service.get_current_round()
    if current is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return RoundDetailResponse(success=True, data={"round": current})


@router.get("/{season}/{round}/progress", response_model=RoundProgressResponse)
async def get_round_progress_by_season(
    season: int,
    round: int,  # noqa: A001 - path param name must match URL segment {round}
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundProgressResponse:
    """Get round progress by season and round number.

    Example: /rounds/1/1/progress returns progress for Season 1, Round 1
    """
    service = await _service(session)
    try:
        progress = await service.get_round_progress_data(f"{season}/{round}", get_current_block_estimate())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundProgressResponse(success=True, data={"progress": progress})


@router.get("/{season}/{round}", response_model=RoundDetailResponse)
@cache("round_by_season", ttl=300)
async def get_round_by_season(
    season: int,
    round: int,  # noqa: A001 - path param name must match URL segment {round}
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundDetailResponse:
    """Get round by season and round number within season.

    Example: /rounds/8/3 returns Season 8, Round 3
    """
    service = await _service(session)
    try:
        detail_data = await service.get_round_detail(season, round)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RoundDetailResponse(success=True, data={"round": detail_data})


@router.get("/get-round")
async def get_round_aggregated(
    season: Annotated[int, Query(..., description="Season number (e.g., 1)")],
    round_in_season: Annotated[int, Query(..., description="Round number within season (e.g., 1)")],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Endpoint simplificado que devuelve métricas agregadas (post-consensus) y por validator (local)
    desde validator_round_summary_miners.

    - aggregated: winner, avg_winner_score, avg_eval_time, miners_evaluated, tasks_evaluated (post-consensus, desde Autoppia UID 83)
    - validators: lista de validators con sus métricas locales (prefijo local_)
    """
    service = await _service(session)
    try:
        data = await service.get_round_with_validators(season, round_in_season)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - intentional: any service error -> 500
        logger.error("Error in get_round_aggregated: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{round_id}/basic")
@cache("round_basic", ttl=300)  # Cache 5 minutes
async def get_round_basic(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """
    Get basic round info without nested agent runs, tasks, solutions, or evaluations.
    Use this for round page header and status display.
    """
    service = await _service(session)
    try:
        basic_data = await _round_detail_from_identifier(service, round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {"round": basic_data},
    }


@router.get("/{round_id}")
async def get_round(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """
    Get complete round details with intelligent caching:

    1. For FINISHED rounds: Read from round_snapshots table (PostgreSQL) - instant, permanent
    2. For ACTIVE rounds: Cache in Redis (1 day TTL) - fast, temporary
    3. If no snapshot exists: Calculate, save to round_snapshots, return

    This ensures:
    - Historical rounds load instantly from DB (no Redis needed)
    - Current round cached in Redis (updates frequently)
    - Auto-caching on first request
    """
    service = await _service(session)
    try:
        if "/" in round_id:
            season_s, round_s = round_id.split("/", 1)
            detail_data = await service.get_round_detail(int(season_s), int(round_s))
        else:
            parsed = int(round_id)
            if parsed >= 10000 and (parsed % 10000) > 0:
                detail_data = await service.get_round_detail(parsed // 10000, parsed % 10000)
            else:
                detail_data = await service.get_round_detail_by_round_id(parsed)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Error loading round %s: %s", round_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "success": True,
        "data": {"round": detail_data},
    }


@router.get("/{round_id}/statistics", response_model=RoundStatisticsResponse)
@cache("round_statistics", ttl=180)  # Cache 3 minutes
async def get_round_statistics(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundStatisticsResponse:
    service = await _service(session)
    try:
        stats = await service.get_round_statistics(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundStatisticsResponse(success=True, data={"statistics": stats})


@router.get("/{round_id}/miners", response_model=RoundMinersResponse)
@cache("round_miners", ttl=300)  # Cache 5 minutes
async def get_round_miners(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[RoundMinersQuery, Depends(get_round_miners_query)],
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        data = await service.get_round_miners_data(
            round_identifier=round_id,
            page=q.page,
            limit=q.limit,
            sort_by=q.sortBy,
            sort_order=q.sortOrder,
            success=q.success,
            min_score=q.minScore,
            max_score=q.maxScore,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data=data)


@router.get("/{round_id}/miners/top", response_model=RoundMinersResponse)
async def get_top_round_miners(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(10, ge=1, le=50)] = 10,
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        data = await service.get_round_miners_data(
            round_identifier=round_id,
            page=1,
            limit=limit,
            sort_by="score",
            sort_order="desc",
            success=None,
            min_score=None,
            max_score=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data=data)


@router.get("/{round_id}/miners/{uid}", response_model=RoundMinersResponse)
async def get_round_miner(
    round_id: str,
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        data = await service.get_round_miners_data(
            round_identifier=round_id,
            page=1,
            limit=1000,
            sort_by="ranking",
            sort_order="asc",
            success=None,
            min_score=None,
            max_score=None,
        )
        miner = next((m for m in data.get("miners", []) if int(m.get("uid", -1)) == uid), None)
        if miner is None:
            raise ValueError(f"Miner {uid} not found in round {round_id}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data={"miner": miner})


@router.get("/{round_id}/validators", response_model=RoundValidatorsResponse)
async def get_round_validators(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        data = await service.get_round_validators_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data=data)


@router.get("/by-id/{round_id}/validators", response_model=RoundValidatorsResponse, include_in_schema=False)
async def get_round_validators_by_id_alias(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        data = await service.get_round_validators_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data=data)


@router.get("/{round_id}/validators/{validator_id}", response_model=RoundValidatorsResponse)
async def get_round_validator(
    round_id: str,
    validator_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        data = await service.get_round_validators_data(round_id)
        validator = _find_validator_in_data(data, validator_id)
        if validator is None:
            raise ValueError(f"Validator {validator_id} not found in round {round_id}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data={"validator": validator})


@router.get("/by-id/{round_id}/validators/{validator_id}", response_model=RoundValidatorsResponse, include_in_schema=False)
async def get_round_validator_by_id_alias(
    round_id: str,
    validator_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        data = await service.get_round_validators_data(round_id)
        validator = _find_validator_in_data(data, validator_id)
        if validator is None:
            raise ValueError(f"Validator {validator_id} not found in round {round_id}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data={"validator": validator})


@router.get("/{round_id}/activity", response_model=RoundActivityResponse)
async def get_round_activity(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[RoundActivityQuery, Depends(get_round_activity_query)],
) -> RoundActivityResponse:
    service = await _service(session)
    try:
        detail = await _round_detail_from_identifier(service, round_id)
        activities = []
        if detail.get("startTime"):
            activities.append(
                {
                    "id": f"{detail['id']}-start",
                    "type": "round_started",
                    "message": f"Round {detail['roundKey']} started",
                    "timestamp": detail["startTime"],
                    "metadata": {"roundId": detail["id"]},
                }
            )
        if detail.get("endTime"):
            activities.append(
                {
                    "id": f"{detail['id']}-end",
                    "type": "round_ended",
                    "message": f"Round {detail['roundKey']} finished",
                    "timestamp": detail["endTime"],
                    "metadata": {"roundId": detail["id"]},
                }
            )
        if q.activity_type:
            activities = [a for a in activities if a.get("type") == q.activity_type]
        data = {"activities": activities[q.offset : q.offset + q.limit], "total": len(activities)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundActivityResponse(success=True, data=data)


@router.get("/{round_id}/progress", response_model=RoundProgressResponse)
async def get_round_progress(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundProgressResponse:
    """Get round progress for the provided identifier (season/round or numeric)."""
    service = await _service(session)
    try:
        progress = await service.get_round_progress_data(round_id, get_current_block_estimate())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundProgressResponse(success=True, data={"progress": progress})


@router.post("/compare", response_model=RoundComparisonResponse)
async def compare_rounds(
    payload: RoundComparisonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundComparisonResponse:
    service = await _service(session)
    if not payload.roundIds:
        raise HTTPException(status_code=400, detail="roundIds cannot be empty")
    try:
        comparisons = []
        for rid in payload.roundIds:
            stats = await service.get_round_statistics(str(rid))
            top = await service.get_round_miners_data(
                round_identifier=str(rid),
                page=1,
                limit=3,
                sort_by="score",
                sort_order="desc",
                success=None,
                min_score=None,
                max_score=None,
            )
            comparisons.append(
                {
                    "roundId": rid,
                    "statistics": stats,
                    "topMiners": [{"uid": int(m["uid"]), "score": float(m["score"]), "ranking": int(m["ranking"])} for m in top.get("miners", [])],
                }
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundComparisonResponse(success=True, data={"rounds": comparisons})


@router.get("/{round_id}/timeline", response_model=RoundTimelineResponse)
async def get_round_timeline(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundTimelineResponse:
    service = await _service(session)
    try:
        detail = await _round_detail_from_identifier(service, round_id)
        timeline = []
        if detail.get("startTime"):
            timeline.append(
                {
                    "timestamp": detail["startTime"],
                    "block": int(detail.get("startBlock") or 0),
                    "completedTasks": 0,
                    "activeMiners": 0,
                }
            )
        timeline.append(
            {
                "timestamp": detail.get("endTime") or detail.get("startTime"),
                "block": int(detail.get("endBlock") or detail.get("startBlock") or 0),
                "completedTasks": int(detail.get("completedTasks") or 0),
                "activeMiners": 0,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundTimelineResponse(success=True, data={"timeline": timeline})


@router.get("/{round_id}/summary", response_model=RoundSummaryResponse)
async def get_round_summary(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundSummaryResponse:
    service = await _service(session)
    try:
        summary = await service.get_round_summary_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundSummaryResponse(success=True, data=summary)


@router.get(
    "/{round_id}/agent-runs",
    response_model=List[AgentEvaluationRunWithDetails],
)
async def list_round_agent_runs(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(100, ge=1, le=500)] = 100,
    skip: Annotated[int, Query(0, ge=0)] = 0,
) -> List[AgentEvaluationRunWithDetails]:
    service = await _service(session)
    try:
        runs = await service.list_round_agent_runs(round_id, limit=limit, skip=skip)
        return [AgentEvaluationRunWithDetails(**run) for run in runs]
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to list agent runs for round %s: %s",
            round_id,
            exc,
        )
        raise HTTPException(status_code=500, detail="Failed to fetch agent runs") from exc


@router.get(
    "/agent-runs/{agent_run_id}",
    response_model=AgentEvaluationRunWithDetails,
)
async def get_agent_run(
    agent_run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentEvaluationRunWithDetails:
    service = await _service(session)
    try:
        run = await service.get_agent_run_by_id(agent_run_id)
        return AgentEvaluationRunWithDetails(**run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch agent run %s: %s", agent_run_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch agent run") from exc

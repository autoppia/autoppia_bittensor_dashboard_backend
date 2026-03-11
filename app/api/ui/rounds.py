import logging
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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

# Snapshot functionality removed
# from app.services.snapshot_service import SnapshotService
from app.services.chain_state import get_current_block_estimate
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds"])

# Reusable response docs for Sonar (document HTTPException in responses)
RESPONSES_404 = {404: {"description": "Round or resource not found"}}
RESPONSES_404_500 = {404: {"description": "Round or resource not found"}, 500: {"description": "Internal server error"}}


def _newdb(session: AsyncSession) -> UIDataService:
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


# Snapshot functionality removed - no longer using round_snapshots table


@router.get("/ids")
async def list_round_ids(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    status: Annotated[Optional[str], Query()] = None,
    sort_order: Annotated[str, Query(alias="sortOrder")] = "desc",
):
    """
    Get lightweight list of round IDs only (no nested data).
    Much faster than full /rounds endpoint - use this for dropdowns and lists.
    """
    service = _newdb(session)
    entries, _ = await service.get_rounds_list(page=1, limit=limit)
    round_ids = [int(e.get("id", 0)) for e in entries if int(e.get("id", 0)) > 0]
    return {
        "success": True,
        "data": {
            "roundIds": round_ids,
            "total": len(round_ids),
        },
    }


@router.get("/seasons")
async def list_round_seasons(
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _newdb(session)
    seasons = await service.get_available_seasons()
    return {
        "success": True,
        "data": {
            "seasons": seasons,
        },
    }


@router.get("/")
@cache("rounds_list", ttl=600)  # Cache 10 minutes - pre-warmed by background worker
async def list_rounds(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    status: Annotated[Optional[str], Query()] = None,
    sort_by: Annotated[str, Query(alias="sortBy")] = "round",
    sort_order: Annotated[str, Query(alias="sortOrder")] = "desc",
    skip: Annotated[Optional[int], Query(ge=0)] = None,
):
    service = _newdb(session)
    if skip is not None:
        page = (skip // limit) + 1
        offset = skip % limit
        entries, _ = await service.get_rounds_list(page=page, limit=limit)
        sliced = entries[offset:]
        return sliced

    entries, total = await service.get_rounds_list(page=page, limit=limit)
    current = await service.get_current_round()
    payload = {
        "rounds": entries,
        "total": total,
        "page": page,
        "limit": limit,
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


@router.get("/current", responses=RESPONSES_404)
@cache("rounds_current", ttl=300)  # Cache 5 minutes - different key to avoid collision with overview
async def get_current_round(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundDetailResponse:
    service = _newdb(session)
    current = await service.get_current_round()
    if current is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return RoundDetailResponse(success=True, data={"round": current})


@router.get("/{season}/{round}/progress", responses=RESPONSES_404)
async def get_round_progress_by_season(
    season: int,
    round: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundProgressResponse:
    """Get round progress by season and round number.

    Example: /rounds/1/1/progress returns progress for Season 1, Round 1
    """
    service = _newdb(session)
    try:
        progress = await service.get_round_progress_data(f"{season}/{round}", get_current_block_estimate())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundProgressResponse(success=True, data={"progress": progress})


@router.get("/{season}/{round}", responses=RESPONSES_404)
@cache("round_by_season", ttl=300)
async def get_round_by_season(
    season: int,
    round: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundDetailResponse:
    """Get round by season and round number within season.

    Example: /rounds/8/3 returns Season 8, Round 3
    """
    service = _newdb(session)
    try:
        detail_data = await service.get_round_detail(season, round)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RoundDetailResponse(success=True, data={"round": detail_data})


@router.get("/{season}/{round}/status", responses=RESPONSES_404)
async def get_round_status_view(
    season: int,
    round: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _newdb(session)
    try:
        data = await service.get_round_status_view(season, round, get_current_block_estimate())
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{season}/{round}/season-summary", responses=RESPONSES_404)
async def get_round_season_summary_view(
    season: int,
    round: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _newdb(session)
    try:
        data = await service.get_round_season_summary_view(season, round)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{season}/{round}/validators", responses=RESPONSES_404)
async def get_round_validators_view(
    season: int,
    round: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _newdb(session)
    try:
        data = await service.get_round_validators_view(season, round)
        return {"success": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{round_id}/basic", responses=RESPONSES_404)
@cache("round_basic", ttl=300)  # Cache 5 minutes
async def get_round_basic(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """
    Get basic round info without nested agent runs, tasks, solutions, or evaluations.
    Use this for round page header and status display.
    """
    service = _newdb(session)
    try:
        basic_data = await _round_detail_from_identifier(service, round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {"round": basic_data},
    }


@router.get("/{round_id}", responses=RESPONSES_404_500)
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
    service = _newdb(session)
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
        logger.error("Error loading round (see exc_info)", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "success": True,
        "data": {"round": detail_data},
    }


@router.get("/{round_id}/statistics", responses=RESPONSES_404)
@cache("round_statistics", ttl=180)  # Cache 3 minutes
async def get_round_statistics(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundStatisticsResponse:
    service = _newdb(session)
    try:
        stats = await service.get_round_statistics(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundStatisticsResponse(success=True, data={"statistics": stats})


@router.get("/{round_id}/miners", responses=RESPONSES_404)
@cache("round_miners", ttl=300)  # Cache 5 minutes
async def get_round_miners(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: Annotated[str, Query(alias="sortBy")] = "score",
    sort_order: Annotated[str, Query(alias="sortOrder")] = "desc",
    success: Annotated[Optional[bool], Query()] = None,
    min_score: Annotated[Optional[float], Query(alias="minScore")] = None,
    max_score: Annotated[Optional[float], Query(alias="maxScore")] = None,
) -> RoundMinersResponse:
    service = _newdb(session)
    try:
        data = await service.get_round_miners_data(
            round_identifier=round_id,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            success=success,
            min_score=min_score,
            max_score=max_score,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data=data)


@router.get("/{round_id}/miners/top", responses=RESPONSES_404)
async def get_top_round_miners(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> RoundMinersResponse:
    service = _newdb(session)
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


@router.get("/{round_id}/miners/{uid}", responses=RESPONSES_404)
async def get_round_miner(
    round_id: str,
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundMinersResponse:
    service = _newdb(session)
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


@router.get("/{round_id}/validators", responses=RESPONSES_404)
async def get_round_validators(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = _newdb(session)
    try:
        data = await service.get_round_validators_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data=data)


@router.get("/by-id/{round_id}/validators", responses=RESPONSES_404, include_in_schema=False)
async def get_round_validators_by_id_alias(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = _newdb(session)
    try:
        data = await service.get_round_validators_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data=data)


@router.get("/{round_id}/validators/{validator_id}", responses=RESPONSES_404)
async def get_round_validator(
    round_id: str,
    validator_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = _newdb(session)
    try:
        data = await service.get_round_validators_data(round_id)
        validator = None
        for item in data.get("validators", []):
            item_id = str(item.get("id", ""))
            item_uid = item_id.replace("validator-", "")
            if validator_id == item_id or validator_id == item_uid:
                validator = item
                break
        if validator is None:
            raise ValueError(f"Validator {validator_id} not found in round {round_id}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data={"validator": validator})


@router.get("/by-id/{round_id}/validators/{validator_id}", responses=RESPONSES_404, include_in_schema=False)
async def get_round_validator_by_id_alias(
    round_id: str,
    validator_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundValidatorsResponse:
    service = _newdb(session)
    try:
        data = await service.get_round_validators_data(round_id)
        validator = None
        for item in data.get("validators", []):
            item_id = str(item.get("id", ""))
            item_uid = item_id.replace("validator-", "")
            if validator_id == item_id or validator_id == item_uid:
                validator = item
                break
        if validator is None:
            raise ValueError(f"Validator {validator_id} not found in round {round_id}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data={"validator": validator})


@router.get("/{round_id}/activity", responses=RESPONSES_404)
async def get_round_activity(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    activity_type: Annotated[Optional[str], Query(alias="type")] = None,
    since: Annotated[Optional[str], Query()] = None,
) -> RoundActivityResponse:
    service = _newdb(session)
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
        if activity_type:
            activities = [a for a in activities if a.get("type") == activity_type]
        data = {"activities": activities[offset : offset + limit], "total": len(activities)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundActivityResponse(success=True, data=data)


@router.get("/{round_id}/progress", responses=RESPONSES_404)
async def get_round_progress(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundProgressResponse:
    """Get round progress for the provided identifier (season/round or numeric)."""
    service = _newdb(session)
    try:
        progress = await service.get_round_progress_data(round_id, get_current_block_estimate())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundProgressResponse(success=True, data={"progress": progress})


@router.post("/compare", responses={400: {"description": "Bad request"}, 404: {"description": "Round not found"}})
async def compare_rounds(
    payload: RoundComparisonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundComparisonResponse:
    service = _newdb(session)
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
                    "topMiners": [{"uid": int(m["uid"]), "reward": float(m["reward"]), "ranking": int(m["ranking"])} for m in top.get("miners", [])],
                }
            )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundComparisonResponse(success=True, data={"rounds": comparisons})


@router.get("/{round_id}/timeline", responses=RESPONSES_404)
async def get_round_timeline(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundTimelineResponse:
    service = _newdb(session)
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


@router.get("/{round_id}/summary", responses=RESPONSES_404)
async def get_round_summary(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RoundSummaryResponse:
    service = _newdb(session)
    try:
        summary = await service.get_round_summary_data(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundSummaryResponse(success=True, data=summary)


@router.get(
    "/{round_id}/agent-runs",
    responses=RESPONSES_404_500,
)
async def list_round_agent_runs(
    round_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> List[AgentEvaluationRunWithDetails]:
    service = _newdb(session)
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
    responses=RESPONSES_404_500,
)
async def get_agent_run(
    agent_run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentEvaluationRunWithDetails:
    service = _newdb(session)
    try:
        run = await service.get_agent_run_by_id(agent_run_id)
        return AgentEvaluationRunWithDetails(**run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch agent run %s: %s", agent_run_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch agent run") from exc

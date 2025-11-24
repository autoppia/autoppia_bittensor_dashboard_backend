from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
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
    RoundsListResponse,
)
from app.services.ui.rounds_service import RoundsService
from app.services.snapshot_service import SnapshotService
from app.services.chain_state import get_current_block_estimate
from app.services.redis_cache import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds"])


async def _service(session: AsyncSession) -> RoundsService:
    return RoundsService(session)


async def _persist_snapshot_from_detail(
    session: AsyncSession,
    round_id: str,
    detail_data: dict,
) -> None:
    """
    Persist a round snapshot if the round is completed.
    This is called automatically when a round is requested and no snapshot exists.
    """
    from app.db.models import RoundSnapshotORM, ValidatorRoundORM

    # Only persist if round is completed
    round_status = detail_data.get("status", "")
    if round_status not in ("finished", "completed", "complete"):
        logger.debug(
            f"Round {round_id} not finished ({round_status}), skipping snapshot"
        )
        return

    # Extract round_number
    round_number = detail_data.get("roundNumber") or detail_data.get("round")
    if not round_number or not isinstance(round_number, int):
        logger.warning(f"Cannot persist snapshot for {round_id}: no valid round_number")
        return

    # Check if snapshot already exists
    existing = await session.get(RoundSnapshotORM, round_number)
    if existing:
        logger.debug(f"Snapshot for round {round_number} already exists, skipping")
        return

    # Create snapshot
    import json

    snapshot_json = detail_data
    json_str = json.dumps(snapshot_json, default=str)
    data_size = len(json_str.encode("utf-8"))

    new_snapshot = RoundSnapshotORM(
        round_number=round_number,
        snapshot_json=snapshot_json,
        snapshot_version=1,
        data_size_bytes=data_size,
    )
    session.add(new_snapshot)
    await session.flush()

    logger.info(
        f"✅ Created snapshot for round {round_number} ({data_size / 1024:.1f} KB)"
    )


@router.get("/ids")
async def list_round_ids(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(500, ge=1, le=1000),
    status: Optional[str] = Query(None),
    sortOrder: str = Query("desc"),
):
    """
    Get lightweight list of round IDs only (no nested data).
    Much faster than full /rounds endpoint - use this for dropdowns and lists.
    """
    service = await _service(session)
    round_ids = await service.list_round_ids(
        limit=limit,
        status=status,
        sort_order=sortOrder,
    )
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
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    sortBy: str = Query("round"),
    sortOrder: str = Query("desc"),
    skip: Optional[int] = Query(None, ge=0),
):
    service = await _service(session)
    if skip is not None:
        # Maintain legacy semantics but return aggregated round-day entries.
        page = (skip // limit) + 1
        offset = skip % limit
        entries, _ = await service.list_rounds_paginated(
            page=page,
            limit=limit,
            status=status,
            sort_by=sortBy,
            sort_order=sortOrder,
        )
        # Filter to started rounds only (based on chain state)
        current_block = get_current_block_estimate()
        if current_block is not None:
            entries = [
                e for e in entries if int(e.get("startBlock", 0) or 0) < current_block
            ]
        sliced = entries[offset:]
        return sliced

    entries, total = await service.list_rounds_paginated(
        page=page,
        limit=limit,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    # Filter to started rounds only (based on chain state)
    current_block = get_current_block_estimate()
    if current_block is not None:
        entries = [
            e for e in entries if int(e.get("startBlock", 0) or 0) < current_block
        ]
        total = len(entries)
    current = await service.get_current_round_overview()
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


@router.get("/current", response_model=RoundDetailResponse)
@cache(
    "rounds_current", ttl=300
)  # Cache 5 minutes - different key to avoid collision with overview
async def get_current_round(
    session: AsyncSession = Depends(get_session),
) -> RoundDetailResponse:
    service = await _service(session)
    current = await service.get_current_round_overview()
    if current is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return RoundDetailResponse(success=True, data={"round": current})


@router.get("/{round_id}/basic")
@cache("round_basic", ttl=300)  # Cache 5 minutes
async def get_round_basic(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Get basic round info without nested agent runs, tasks, solutions, or evaluations.
    Use this for round page header and status display.
    """
    service = await _service(session)
    try:
        basic_data = await service.get_round_basic(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {"round": basic_data},
    }


@router.get("/{round_id}")
async def get_round(
    round_id: str,
    session: AsyncSession = Depends(get_session),
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
    from app.db.models import RoundSnapshotORM
    from app.services.redis_cache import redis_cache
    import logging

    logger = logging.getLogger(__name__)

    # Parse round_number
    round_number: Optional[int] = None
    try:
        round_number = int(round_id)
    except ValueError:
        # Resolver validator_round_id -> round_number
        from app.db.models import RoundORM

        round_number = await session.scalar(
            select(RoundORM.round_number).where(RoundORM.validator_round_id == round_id)
        )

    # Try to get from PostgreSQL snapshot first (for finished rounds)
    snapshot = None
    if round_number is not None:
        snapshot = await session.get(RoundSnapshotORM, round_number)

    if snapshot and snapshot.snapshot_json:
        logger.info(
            "✅ Serving round %s from PostgreSQL snapshot (instant)", round_number
        )
        return {
            "success": True,
            "data": {"round": snapshot.snapshot_json},
        }

    # Check if it's the current/active round - use Redis cache (1 day TTL)
    service = await _service(session)
    current_round_overview = await service.get_current_round_overview()
    current_round_number = (
        current_round_overview.get("round") if current_round_overview else None
    )
    is_current_round = round_number == current_round_number

    if is_current_round:
        # Try Redis cache for current round (1 day TTL)
        cache_key = f"round:current:{round_number}"
        cached_data = redis_cache.get(cache_key)
        if cached_data:
            logger.info("✅ Serving current round %s from Redis cache", round_number)
            return {
                "success": True,
                "data": {"round": cached_data},
            }

    # Calculate from DB (slow path)
    logger.info("🔄 Calculating round %s from DB...", round_id)
    try:
        detail_data = await service.get_round(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Save based on round status
    round_status = detail_data.get("status", "")
    is_finished = round_status in (
        "finished",
        "completed",
        "complete",
        "evaluating_finished",
    )

    if is_finished and round_number:
        # Update status in DB for all validator_rounds of this round_number
        logger.info("🔄 Marking round %s as finished in DB...", round_number)
        try:
            from app.db.models import ValidatorRoundORM

            stmt = select(ValidatorRoundORM).where(
                ValidatorRoundORM.round_number == round_number
            )
            round_rows = list(await session.scalars(stmt))
            for round_row in round_rows:
                if round_row.status != "finished":
                    round_row.status = "finished"
                    logger.info(
                        f"  Updated {round_row.validator_round_id} status to finished"
                    )
            await session.commit()
            logger.info(
                f"✅ Marked {len(round_rows)} validator_rounds as finished for round {round_number}"
            )
        except Exception:
            logger.exception("Failed to update round status in DB")

        # Save to PostgreSQL snapshot (permanent)
        logger.info(
            "💾 Saving finished round %s to PostgreSQL snapshot...", round_number
        )
        try:
            from app.db.session import AsyncSessionLocal
            from app.services.snapshot_service import SnapshotService

            async with AsyncSessionLocal() as snapshot_session:
                await _persist_snapshot_from_detail(
                    snapshot_session, round_id, detail_data
                )
                
                # Also update agent_stats incrementally
                logger.info("📊 Updating agent stats for round %s...", round_number)
                snapshot_service = SnapshotService(snapshot_session)
                await snapshot_service.update_agent_stats(round_number)
                
                await snapshot_session.commit()
                logger.info("✅ Round %s snapshot and agent stats saved", round_number)
        except Exception:
            logger.exception("Failed to persist snapshot for round %s", round_id)
    elif is_current_round and round_number:
        # Cache current round in Redis (1 day TTL)
        logger.info("💾 Caching current round %s in Redis (1 day)...", round_number)
        try:
            redis_cache.set(
                f"round:current:{round_number}", detail_data, ttl=86400
            )  # 1 day
            logger.info("✅ Current round %s cached in Redis", round_number)
        except Exception:
            logger.exception("Failed to cache current round %s", round_number)

    return {
        "success": True,
        "data": {"round": detail_data},
    }


@router.get("/{round_id}/statistics", response_model=RoundStatisticsResponse)
@cache("round_statistics", ttl=180)  # Cache 3 minutes
async def get_round_statistics(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundStatisticsResponse:
    service = await _service(session)
    try:
        stats = await service.get_round_statistics(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundStatisticsResponse(success=True, data={"statistics": stats})


@router.get("/{round_id}/miners", response_model=RoundMinersResponse)
@cache(
    "round_miners", ttl=300
)  # Cache 5 minutes (smart_cache will extend for completed rounds)
async def get_round_miners(
    round_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sortBy: str = Query("score"),
    sortOrder: str = Query("desc"),
    success: Optional[bool] = Query(None),
    minScore: Optional[float] = Query(None),
    maxScore: Optional[float] = Query(None),
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        data = await service.get_round_miners(
            round_identifier=round_id,
            page=page,
            limit=limit,
            sort_by=sortBy,
            sort_order=sortOrder,
            success=success,
            min_score=minScore,
            max_score=maxScore,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data=data)


@router.get("/{round_id}/miners/top", response_model=RoundMinersResponse)
async def get_top_round_miners(
    round_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(10, ge=1, le=50),
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        data = await service.get_top_miners(round_id, limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data=data)


@router.get("/{round_id}/miners/{uid}", response_model=RoundMinersResponse)
async def get_round_miner(
    round_id: str,
    uid: int,
    session: AsyncSession = Depends(get_session),
) -> RoundMinersResponse:
    service = await _service(session)
    try:
        miner = await service.get_round_miner(round_id, uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundMinersResponse(success=True, data={"miner": miner})


@router.get("/{round_id}/validators", response_model=RoundValidatorsResponse)
async def get_round_validators(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        data = await service.get_round_validators(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data=data)


@router.get(
    "/{round_id}/validators/{validator_id}", response_model=RoundValidatorsResponse
)
async def get_round_validator(
    round_id: str,
    validator_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundValidatorsResponse:
    service = await _service(session)
    try:
        validator = await service.get_round_validator(round_id, validator_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundValidatorsResponse(success=True, data={"validator": validator})


@router.get("/{round_id}/activity", response_model=RoundActivityResponse)
async def get_round_activity(
    round_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    activity_type: Optional[str] = Query(None, alias="type"),
    since: Optional[str] = Query(None),
) -> RoundActivityResponse:
    service = await _service(session)
    try:
        data = await service.get_round_activity(
            round_identifier=round_id,
            limit=limit,
            offset=offset,
            activity_type=activity_type,
            since=since,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundActivityResponse(success=True, data=data)


@router.get("/{round_id}/progress", response_model=RoundProgressResponse)
async def get_round_progress(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundProgressResponse:
    service = await _service(session)
    try:
        progress = await service.get_round_progress(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundProgressResponse(success=True, data={"progress": progress})


@router.post("/compare", response_model=RoundComparisonResponse)
async def compare_rounds(
    payload: RoundComparisonRequest,
    session: AsyncSession = Depends(get_session),
) -> RoundComparisonResponse:
    service = await _service(session)
    if not payload.roundIds:
        raise HTTPException(status_code=400, detail="roundIds cannot be empty")
    try:
        comparisons = await service.compare_rounds(payload.roundIds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundComparisonResponse(success=True, data={"rounds": comparisons})


@router.get("/{round_id}/timeline", response_model=RoundTimelineResponse)
async def get_round_timeline(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundTimelineResponse:
    service = await _service(session)
    try:
        timeline = await service.get_round_timeline(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundTimelineResponse(success=True, data={"timeline": timeline})


@router.get("/{round_id}/summary", response_model=RoundSummaryResponse)
async def get_round_summary(
    round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundSummaryResponse:
    service = await _service(session)
    try:
        summary = await service.get_round_summary_card(round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RoundSummaryResponse(success=True, data=summary)


@router.get(
    "/{round_id}/agent-runs",
    response_model=List[AgentEvaluationRunWithDetails],
)
async def list_round_agent_runs(
    round_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
) -> List[AgentEvaluationRunWithDetails]:
    service = await _service(session)
    try:
        return await service.list_agent_runs(
            validator_round_id=round_id,
            limit=limit,
            skip=skip,
            include_details=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to list agent runs for round %s: %s",
            round_id,
            exc,
        )
        raise HTTPException(
            status_code=500, detail="Failed to fetch agent runs"
        ) from exc


@router.get(
    "/agent-runs/{agent_run_id}",
    response_model=AgentEvaluationRunWithDetails,
)
async def get_agent_run(
    agent_run_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentEvaluationRunWithDetails:
    service = await _service(session)
    try:
        return await service.get_agent_run(agent_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch agent run %s: %s", agent_run_id, exc)
        raise HTTPException(
            status_code=500, detail="Failed to fetch agent run"
        ) from exc

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.subnets import SubnetTimelineResponse
from app.services.subnet_timeline import (
    MAX_ROSTER_SIZE,
    MAX_ROUND_COUNT,
    SubnetTimelineService,
)

router = APIRouter(prefix="/api/v1/subnets", tags=["subnets"])
legacy_router = APIRouter(prefix="/subnets", tags=["subnets"], include_in_schema=False)


async def _timeline_response(
    subnet_id: str,
    session: AsyncSession,
    *,
    rounds: int | None,
    end_round: int | None,
    seconds_back: int | None,
    miners: int | None,
) -> SubnetTimelineResponse:
    service = SubnetTimelineService(session)
    try:
        return await service.build_timeline(
            subnet_id=subnet_id,
            rounds=rounds,
            end_round=end_round,
            seconds_back=seconds_back,
            miners=miners,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{subnet_id}/timeline",
    response_model=SubnetTimelineResponse,
    summary="Get timeline animation data for a subnet",
)
async def get_subnet_timeline(
    subnet_id: str = Path(..., description="Subnet identifier"),
    rounds: int | None = Query(
        None,
        ge=1,
        le=MAX_ROUND_COUNT,
        description="Number of rounds to return (defaults to last 90 rounds)",
    ),
    end_round: int | None = Query(
        None,
        ge=1,
        description="Ending round number (defaults to most recent inferred round)",
    ),
    seconds_back: int | None = Query(
        None,
        ge=1,
        description="Alternative to 'rounds'; converts seconds to round count",
    ),
    miners: int | None = Query(
        None,
        ge=1,
        le=MAX_ROSTER_SIZE,
        description="Roster size to return (defaults to 8)",
    ),
    session: AsyncSession = Depends(get_session),
) -> SubnetTimelineResponse:
    return await _timeline_response(
        subnet_id,
        session,
        rounds=rounds,
        end_round=end_round,
        seconds_back=seconds_back,
        miners=miners,
    )


@legacy_router.get(
    "/{subnet_id}/timeline",
    response_model=SubnetTimelineResponse,
)
async def get_subnet_timeline_legacy(
    subnet_id: str = Path(..., description="Subnet identifier"),
    rounds: int | None = Query(None, ge=1, le=MAX_ROUND_COUNT),
    end_round: int | None = Query(None, ge=1),
    seconds_back: int | None = Query(None, ge=1),
    miners: int | None = Query(None, ge=1, le=MAX_ROSTER_SIZE),
    session: AsyncSession = Depends(get_session),
) -> SubnetTimelineResponse:
    return await _timeline_response(
        subnet_id,
        session,
        rounds=rounds,
        end_round=end_round,
        seconds_back=seconds_back,
        miners=miners,
    )

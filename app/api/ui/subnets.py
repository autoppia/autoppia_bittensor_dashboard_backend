from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.subnets import SubnetTimelineResponse
from app.services.subnet_timeline import (
    MAX_ROSTER_SIZE,
    MAX_ROUND_COUNT,
    SubnetTimelineService,
)

router = APIRouter(prefix="/api/v1/subnets", tags=["subnets"])


# ---------------------------------------------------------------------------
# Query model for the subnet timeline endpoint
# ---------------------------------------------------------------------------


class SubnetTimelineQuery(BaseModel):
    """Query params for GET /{subnet_id}/timeline."""

    rounds: int | None = None
    end_round: int | None = None
    seconds_back: int | None = None
    miners: int | None = None

    model_config = {"extra": "forbid"}


def get_subnet_timeline_query(
    rounds: Annotated[
        int | None,
        Query(None, ge=1, le=MAX_ROUND_COUNT, description="Number of rounds to return (defaults to last 90 rounds)"),
    ] = None,
    end_round: Annotated[
        int | None,
        Query(None, ge=1, description="Ending round number (defaults to most recent inferred round)"),
    ] = None,
    seconds_back: Annotated[
        int | None,
        Query(None, ge=1, description="Alternative to 'rounds'; converts seconds to round count"),
    ] = None,
    miners: Annotated[
        int | None,
        Query(None, ge=1, le=MAX_ROSTER_SIZE, description="Roster size to return (defaults to 8)"),
    ] = None,
) -> SubnetTimelineQuery:
    return SubnetTimelineQuery(
        rounds=rounds,
        end_round=end_round,
        seconds_back=seconds_back,
        miners=miners,
    )


# ---------------------------------------------------------------------------
# Helper shared outside FastAPI dependency injection
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{subnet_id}/timeline",
    responses={404: {"description": "Subnet not found"}},
    summary="Get timeline animation data for a subnet",
)
async def get_subnet_timeline(
    subnet_id: Annotated[str, Path(..., description="Subnet identifier")],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[SubnetTimelineQuery, Depends(get_subnet_timeline_query)],
) -> SubnetTimelineResponse:
    return await _timeline_response(
        subnet_id,
        session,
        rounds=q.rounds,
        end_round=q.end_round,
        seconds_back=q.seconds_back,
        miners=q.miners,
    )

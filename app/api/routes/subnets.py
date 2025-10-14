from typing import Optional

from fastapi import APIRouter, Path, Query

from app.models.ui.subnets import SubnetTimelineResponse
from app.services.subnet_timeline import (
    MAX_ROSTER_SIZE,
    MAX_ROUND_COUNT,
    build_subnet_timeline,
)

router = APIRouter(prefix="/subnets", tags=["subnets"])


@router.get(
    "/{subnet_id}/timeline",
    response_model=SubnetTimelineResponse,
    summary="Get timeline animation data for a subnet",
)
async def get_subnet_timeline(
    subnet_id: str = Path(..., description="Subnet identifier"),
    rounds: Optional[int] = Query(
        None,
        ge=1,
        le=MAX_ROUND_COUNT,
        description="Number of rounds to return (defaults to last 90 rounds)",
    ),
    end_round: Optional[int] = Query(
        None,
        ge=1,
        description="Ending round number (defaults to most recent inferred round)",
    ),
    seconds_back: Optional[int] = Query(
        None,
        ge=1,
        description="Alternative to 'rounds'; converts seconds to round count",
    ),
    miners: Optional[int] = Query(
        None,
        ge=1,
        le=MAX_ROSTER_SIZE,
        description="Roster size to return (defaults to 8)",
    ),
) -> SubnetTimelineResponse:
    """
    Return deterministic mock data for the animation component, including miner roster
    metadata and round-by-round snapshots.
    """

    return build_subnet_timeline(
        subnet_id=subnet_id,
        rounds=rounds,
        end_round=end_round,
        seconds_back=seconds_back,
        miners=miners,
    )

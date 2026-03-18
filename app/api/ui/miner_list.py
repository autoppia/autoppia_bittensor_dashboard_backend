from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miner-list", tags=["miner-list"])


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query model that avoids shadowing the built-in "round"
# ---------------------------------------------------------------------------


class MinerListQuery(BaseModel):
    """Query params for list_miners (API still accepts ?round= via alias)."""

    page: int = 1
    limit: int = 50
    is_sota: bool | None = None
    search: str | None = None
    round_num: int | None = None  # API query param name is "round" (alias in dependency)

    model_config = {"extra": "forbid"}


def get_miner_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(50, ge=1, le=100)] = 50,
    is_sota: Annotated[bool | None, Query(None, alias="isSota")] = None,
    search: Annotated[str | None, Query(None)] = None,
    round_num: Annotated[int | None, Query(None, alias="round")] = None,
) -> MinerListQuery:
    return MinerListQuery(page=page, limit=limit, is_sota=is_sota, search=search, round_num=round_num)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", responses={500: {"description": "Internal error listing miners"}})
@router.get("", include_in_schema=False, responses={500: {"description": "Internal error listing miners"}})
@cache("miner_list", ttl=600)  # Cache 10 minutes - pre-warmed by background worker
async def list_miners(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[MinerListQuery, Depends(get_miner_list_query)],
):
    try:
        service = _service(session)
        response = await service.list_agents_catalog(
            page=q.page,
            limit=q.limit,
            search=q.search,
            sort_by="score",
            sort_order="desc",
        )
        agents = response.get("agents", [])
        if q.is_sota is not None:
            agents = [a for a in agents if bool(a.get("isSota")) is q.is_sota]
        response["agents"] = agents
        response["total"] = len(agents)
        return response
    except Exception as exc:  # noqa: BLE001 - intentional: catch any service error and return 500
        logger.error("Error in list_miners endpoint: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{uid}", responses={404: {"description": "Miner not found"}})
async def get_miner_detail(
    uid: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _service(session)
    try:
        detail = await service.get_agent_detail(uid, season=None, round_in_season=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail

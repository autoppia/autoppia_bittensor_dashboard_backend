from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miner-list", tags=["miner-list"])


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


@router.get("/")
@router.get("", include_in_schema=False)
@cache("miner_list", ttl=600)  # Cache 10 minutes - pre-warmed by background worker
async def list_miners(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    isSota: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    round: Optional[int] = Query(None),
):
    try:
        service = await _service(session)
        response = await service.list_agents_catalog(
            page=page,
            limit=limit,
            search=search,
            sort_by="score",
            sort_order="desc",
        )
        agents = response.get("agents", [])
        if isSota is not None:
            agents = [a for a in agents if bool(a.get("isSota")) is isSota]
        response["agents"] = agents
        response["total"] = len(agents)
        return response
    except Exception as exc:
        logger.error(f"Error in list_miners endpoint: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{uid}")
async def get_miner_detail(uid: int, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        detail = await service.get_agent_detail(uid, season=None, round_in_season=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail

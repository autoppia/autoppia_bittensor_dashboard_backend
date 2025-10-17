from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.miner_list_service import MinerListService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/miner-list", tags=["miner-list"])


async def _service(session: AsyncSession) -> MinerListService:
    return MinerListService(session)


@router.get("/")
@router.get("", include_in_schema=False)
async def list_miners(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    isSota: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
):
    service = await _service(session)
    response = await service.list_miners(page=page, limit=limit, is_sota=isSota, search=search)
    return response


@router.get("/{uid}")
async def get_miner_detail(uid: int, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        detail = await service.get_miner_detail(uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return detail

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.rounds_service import RoundsService

legacy_router = APIRouter(prefix="/rounds", tags=["legacy-rounds"], include_in_schema=False)


async def _service(session: AsyncSession) -> RoundsService:
    return RoundsService(session)


@legacy_router.get("")
async def legacy_list_rounds(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    sortBy: str = Query("id"),
    sortOrder: str = Query("desc"),
) -> list:
    service = await _service(session)
    rounds, _ = await service.list_rounds_paginated(
        page=page,
        limit=limit,
        status=status,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return [entry for entry in rounds if entry.get("validatorRoundCount", 0) > 0]


@legacy_router.get("/current")
async def legacy_get_current_round(
    session: AsyncSession = Depends(get_session),
) -> dict:
    service = await _service(session)
    current = await service.get_current_round_overview()
    if current is None:
        raise HTTPException(status_code=404, detail="No rounds available")
    return current

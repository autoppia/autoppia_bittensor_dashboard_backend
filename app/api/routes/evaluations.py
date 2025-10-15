from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.evaluations import EvaluationDetailResponse, EvaluationListResponse
from app.services.evaluations_service import EvaluationsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


async def _service(session: AsyncSession) -> EvaluationsService:
    return EvaluationsService(session)


@router.get("", response_model=EvaluationListResponse)
async def list_evaluations(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    runId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    validatorId: Optional[str] = Query(None),
    taskId: Optional[str] = Query(None),
    roundId: Optional[int] = Query(None),
) -> EvaluationListResponse:
    service = await _service(session)
    data = await service.list_evaluations(
        page=page,
        limit=limit,
        run_id=runId,
        agent_id=agentId,
        validator_id=validatorId,
        task_id=taskId,
        round_id=roundId,
    )
    return EvaluationListResponse(success=True, data=data)


@router.get("/{evaluation_id}", response_model=EvaluationDetailResponse)
async def get_evaluation(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
) -> EvaluationDetailResponse:
    service = await _service(session)
    try:
        context = await service.get_evaluation(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail = service.build_detail(context)
    return EvaluationDetailResponse(success=True, data={"evaluation": detail})

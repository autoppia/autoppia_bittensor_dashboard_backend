from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.evaluations import (
    EvaluationDetailResponse,
    EvaluationGifUploadResponse,
    EvaluationListResponse,
)
from app.services.evaluations_service import EvaluationsService
from app.services.media_storage import (
    GifStorageConfigError,
    build_public_url,
    store_gif,
)

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


@router.post(
    "/{evaluation_id}/gif",
    status_code=201,
    response_model=EvaluationGifUploadResponse,
)
async def upload_evaluation_gif(
    evaluation_id: str,
    gif: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> EvaluationGifUploadResponse:
    if gif.content_type != "image/gif":
        raise HTTPException(
            status_code=400,
            detail="Only GIF images are supported",
        )

    service = await _service(session)
    try:
        await service.get_evaluation(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    file_data = await gif.read()
    if not file_data:
        raise HTTPException(status_code=400, detail="Uploaded GIF is empty")

    if not file_data.startswith((b"GIF87a", b"GIF89a")):
        raise HTTPException(status_code=400, detail="File is not a valid GIF image")

    try:
        object_key = await store_gif(evaluation_id, file_data)
    except (GifStorageConfigError, BotoCoreError, ClientError) as exc:
        logger.error("Failed to upload GIF for %s: %s", evaluation_id, exc)
        raise HTTPException(status_code=500, detail="Failed to store GIF image") from exc

    gif_url = build_public_url(object_key)

    try:
        await service.update_gif_recording(evaluation_id, gif_url)
    except ValueError as exc:
        logger.error("Unable to update GIF record for %s: %s", evaluation_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update evaluation record") from exc

    logger.info("Uploaded GIF for evaluation %s to key %s", evaluation_id, object_key)
    return EvaluationGifUploadResponse(success=True, data={"gifUrl": gif_url})

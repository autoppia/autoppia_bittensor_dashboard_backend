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
from app.services.ui.evaluations_service import EvaluationsService
from app.services.media_storage import (
    GifStorageConfigError,
    build_public_url,
    store_gif,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


async def _service(session: AsyncSession) -> EvaluationsService:
    return EvaluationsService(session)


async def _reset_session_transaction(session: AsyncSession) -> None:
    """
    Roll back the current transaction so the connection is released before
    performing long-running non-DB work (e.g., uploading to S3).
    """
    transaction = session.get_transaction()
    if transaction is None or not transaction.is_active:
        return
    await session.rollback()


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


@router.get("/{evaluation_id}/task-details")
async def get_evaluation_as_task_details(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Get evaluation in task details format (for UI compatibility).
    This allows using the same UI components for both tasks and evaluations.
    """
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    detail = task_service.build_task_detail(task_context)
    return {"success": True, "data": {"details": detail}}


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
    logger.info(
        "Received GIF upload request for evaluation %s filename=%s content_type=%s",
        evaluation_id,
        gif.filename,
        gif.content_type,
    )
    if gif.content_type != "image/gif":
        logger.warning(
            "Rejected GIF upload for evaluation %s due to invalid content type %s",
            evaluation_id,
            gif.content_type,
        )
        raise HTTPException(
            status_code=400,
            detail="Only GIF images are supported",
        )

    service = await _service(session)
    try:
        await service.get_evaluation(evaluation_id)
    except ValueError as exc:
        logger.warning("GIF upload requested for unknown evaluation %s", evaluation_id)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _reset_session_transaction(session)

    file_data = await gif.read()
    received_bytes = len(file_data) if file_data else 0
    logger.info(
        "Read GIF upload payload for evaluation %s size_bytes=%s",
        evaluation_id,
        received_bytes,
    )
    if not file_data:
        logger.warning(
            "Rejected GIF upload for evaluation %s because payload is empty",
            evaluation_id,
        )
        raise HTTPException(status_code=400, detail="Uploaded GIF is empty")

    if not file_data.startswith((b"GIF87a", b"GIF89a")):
        logger.warning(
            "Rejected GIF upload for evaluation %s because payload is not a GIF header",
            evaluation_id,
        )
        raise HTTPException(status_code=400, detail="File is not a valid GIF image")

    try:
        logger.info("Storing GIF for evaluation %s", evaluation_id)
        object_key = await store_gif(evaluation_id, file_data)
    except (GifStorageConfigError, BotoCoreError, ClientError) as exc:
        logger.error("Failed to upload GIF for %s: %s", evaluation_id, exc)
        raise HTTPException(
            status_code=500, detail="Failed to store GIF image"
        ) from exc

    gif_url = build_public_url(object_key)

    try:
        logger.info("Updating evaluation %s with GIF URL %s", evaluation_id, gif_url)
        await service.update_gif_recording(evaluation_id, gif_url)
    except ValueError as exc:
        logger.error("Unable to update GIF record for %s: %s", evaluation_id, exc)
        raise HTTPException(
            status_code=500, detail="Failed to update evaluation record"
        ) from exc

    logger.info(
        "Uploaded GIF for evaluation %s to key %s (size_bytes=%s, url=%s)",
        evaluation_id,
        object_key,
        received_bytes,
        gif_url,
    )
    return EvaluationGifUploadResponse(success=True, data={"gifUrl": gif_url})

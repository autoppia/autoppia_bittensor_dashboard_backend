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


@router.get("/{evaluation_id}/get-evaluation")
async def get_evaluation_complete(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Get all evaluation data in a single call (similar to get-round).
    Returns details, personas, results, actions, screenshots, logs, timeline, metrics, and statistics.
    """
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        data = await task_service.get_evaluation_complete(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    return {"success": True, "data": data}


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


@router.get("/{evaluation_id}/personas")
async def get_evaluation_personas(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get personas for an evaluation (same format as task personas)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    personas = task_service.build_personas(task_context)
    return {"success": True, "data": {"personas": personas.model_dump()}}


@router.get("/{evaluation_id}/results")
async def get_evaluation_results(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get results for an evaluation (same format as task results)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    results = task_service.build_task_results(task_context)
    return {"success": True, "data": {"results": results}}


@router.get("/{evaluation_id}/actions")
async def get_evaluation_actions(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get actions for an evaluation (same format as task actions)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    actions = task_service.build_actions(task_context)
    total = len(actions)
    success_count = sum(1 for action in actions if getattr(action, 'success', False))
    fail_count = sum(1 for action in actions if getattr(action, 'error', False) or not getattr(action, 'success', False))
    
    start = (page - 1) * limit
    end = start + limit
    paginated = actions[start:end]
    return {
        "success": True,
        "data": {
            "actions": [action.model_dump() for action in paginated],
            "total": total,
            "successCount": success_count,
            "failCount": fail_count,
            "page": page,
            "limit": limit,
        },
    }


@router.get("/{evaluation_id}/screenshots")
async def get_evaluation_screenshots(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get screenshots for an evaluation (same format as task screenshots)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    screenshots = task_service.build_screenshots(task_context)
    return {
        "success": True,
        "data": {"screenshots": [shot.model_dump() for shot in screenshots]},
    }


@router.get("/{evaluation_id}/logs")
async def get_evaluation_logs(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get logs for an evaluation (same format as task logs)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    logs = task_service.build_logs(task_context)
    return {"success": True, "data": {"logs": [log.model_dump() for log in logs]}}


@router.get("/{evaluation_id}/timeline")
async def get_evaluation_timeline(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get timeline for an evaluation (same format as task timeline)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    timeline = task_service.build_timeline(task_context)
    return {"success": True, "data": {"timeline": [item.model_dump() for item in timeline]}}


@router.get("/{evaluation_id}/metrics")
async def get_evaluation_metrics(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get metrics for an evaluation (same format as task metrics)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    metrics = task_service.build_metrics(task_context)
    return {"success": True, "data": {"metrics": metrics}}


@router.get("/{evaluation_id}/statistics")
async def get_evaluation_statistics(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get statistics for an evaluation (same format as task statistics)."""
    from app.services.ui.tasks_service import TasksService
    
    task_service = TasksService(session)
    try:
        task_context = await task_service.get_task_by_evaluation_id(evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    statistics = task_service.build_task_statistics(task_context)
    return {"success": True, "data": {"statistics": statistics.model_dump()}}


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
    # Only verify evaluation exists (simple query) before resetting transaction
    # Don't build full context here as it accesses lazy-loaded relationships
    # that will be invalidated after rollback
    try:
        from app.db.models import EvaluationORM
        from sqlalchemy import select
        stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
        evaluation_row = await session.scalar(stmt)
        if not evaluation_row:
            raise ValueError(f"Evaluation {evaluation_id} not found")
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

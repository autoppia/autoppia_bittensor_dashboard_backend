from __future__ import annotations

import logging
from typing import Annotated, Any, Awaitable, Callable

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvaluationORM
from app.db.session import get_session
from app.models.ui.evaluations import (
    EvaluationDetailResponse,
    EvaluationGifUploadResponse,
    EvaluationListResponse,
)
from app.services.media_storage import (
    GifStorageConfigError,
    build_public_url,
    store_gif,
)
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


async def _reset_session_transaction(session: AsyncSession) -> None:
    """
    Roll back the current transaction so the connection is released before
    performing long-running non-DB work (e.g., uploading to S3).
    """
    transaction = session.get_transaction()
    if transaction is None or not transaction.is_active:
        return
    await session.rollback()


# ---------------------------------------------------------------------------
# Query model (Sonar: reduce list endpoint params)
# ---------------------------------------------------------------------------


class EvaluationListQuery(BaseModel):
    """Query params for list endpoint."""

    page: int = 1
    limit: int = 20
    run_id: str | None = None
    agent_id: str | None = None
    validator_id: str | None = None
    task_id: str | None = None
    round_id: int | None = None

    model_config = {"extra": "forbid"}


def get_evaluation_list_query(
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    run_id: Annotated[str | None, Query(alias="runId")] = None,
    agent_id: Annotated[str | None, Query(alias="agentId")] = None,
    validator_id: Annotated[str | None, Query(alias="validatorId")] = None,
    task_id: Annotated[str | None, Query(alias="taskId")] = None,
    round_id: Annotated[int | None, Query(alias="roundId")] = None,
) -> EvaluationListQuery:
    return EvaluationListQuery(
        page=page,
        limit=limit,
        run_id=run_id,
        agent_id=agent_id,
        validator_id=validator_id,
        task_id=task_id,
        round_id=round_id,
    )


# ---------------------------------------------------------------------------
# Helper: fetch evaluation/task data or 404 (Sonar: deduplicate try/except)
# ---------------------------------------------------------------------------


async def _fetch_or_404(
    session: AsyncSession,
    evaluation_id: str,
    fetch: Callable[[UIDataService, str], Awaitable[Any]],
) -> Any:
    service = _service(session)
    try:
        return await fetch(service, evaluation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# List & export
# ---------------------------------------------------------------------------


@router.get("")
async def list_evaluations(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[EvaluationListQuery, Depends(get_evaluation_list_query)],
) -> EvaluationListResponse:
    service = _service(session)
    data = await service.list_evaluations(
        page=q.page,
        limit=q.limit,
        run_id=q.run_id,
        agent_id=q.agent_id,
        validator_id=q.validator_id,
        task_id=q.task_id,
        round_id=q.round_id,
    )
    return EvaluationListResponse(success=True, data=data)


@router.get("/export")
async def export_evaluations_by_season(
    season: Annotated[int, Query(..., description="Season number to export evaluations for")],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _service(session)
    data = await service.export_evaluations_by_season(season=season)
    return {"success": True, "data": {"season": season, "evaluations": data}}


# ---------------------------------------------------------------------------
# Get by evaluation_id (shared helper to reduce duplication)
# ---------------------------------------------------------------------------


@router.get("/{evaluation_id}", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EvaluationDetailResponse:
    context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_evaluation(eid))
    service = _service(session)
    detail = service.build_detail(context)
    return EvaluationDetailResponse(success=True, data={"evaluation": detail})


@router.get("/{evaluation_id}/get-evaluation", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_complete(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Get all evaluation data in a single call (similar to get-round).
    Returns details, personas, results, actions, screenshots, logs, timeline, metrics, and statistics.
    """
    data = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_evaluation_complete(eid))
    return {"success": True, "data": data}


@router.get("/{evaluation_id}/task-details", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_as_task_details(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """
    Get evaluation in task details format (for UI compatibility).
    This allows using the same UI components for both tasks and evaluations.
    """
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    detail = service.build_task_detail(task_context)
    return {"success": True, "data": {"details": detail}}


@router.get("/{evaluation_id}/personas", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_personas(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get personas for an evaluation (same format as task personas)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    personas = service.build_personas(task_context)
    return {"success": True, "data": {"personas": personas.model_dump()}}


@router.get("/{evaluation_id}/results", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_results(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get results for an evaluation (same format as task results)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    results = service.build_task_results(task_context)
    return {"success": True, "data": {"results": results}}


@router.get("/{evaluation_id}/actions", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_actions(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Get actions for an evaluation (same format as task actions)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    actions = service.build_actions(task_context)
    total = len(actions)
    success_count = sum(1 for action in actions if getattr(action, "success", False))
    fail_count = sum(1 for action in actions if getattr(action, "error", False) or not getattr(action, "success", False))
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


@router.get("/{evaluation_id}/screenshots", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_screenshots(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get screenshots for an evaluation (same format as task screenshots)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    screenshots = service.build_screenshots(task_context)
    return {
        "success": True,
        "data": {"screenshots": [shot.model_dump() for shot in screenshots]},
    }


@router.get("/{evaluation_id}/logs", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_logs(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get logs for an evaluation (same format as task logs)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    logs = service.build_logs(task_context)
    return {"success": True, "data": {"logs": [log.model_dump() for log in logs]}}


@router.get("/{evaluation_id}/timeline", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_timeline(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get timeline for an evaluation (same format as task timeline)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    timeline = service.build_timeline(task_context)
    return {"success": True, "data": {"timeline": [item.model_dump() for item in timeline]}}


@router.get("/{evaluation_id}/metrics", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_metrics(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get metrics for an evaluation (same format as task metrics)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    metrics = service.build_metrics(task_context)
    return {"success": True, "data": {"metrics": metrics}}


@router.get("/{evaluation_id}/statistics", responses={404: {"description": "Evaluation not found"}})
async def get_evaluation_statistics(
    evaluation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get statistics for an evaluation (same format as task statistics)."""
    task_context = await _fetch_or_404(session, evaluation_id, lambda s, eid: s.get_task_by_evaluation_id(eid))
    service = _service(session)
    statistics = service.build_task_statistics(task_context)
    return {"success": True, "data": {"statistics": statistics.model_dump()}}


# ---------------------------------------------------------------------------
# GIF upload
# ---------------------------------------------------------------------------


@router.post(
    "/{evaluation_id}/gif",
    status_code=201,
    responses={
        400: {"description": "Invalid content type, empty payload, or not a valid GIF"},
        404: {"description": "Evaluation not found"},
        500: {"description": "Failed to store GIF or update record"},
    },
)
async def upload_evaluation_gif(
    evaluation_id: str,
    gif: Annotated[UploadFile, File(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EvaluationGifUploadResponse:
    logger.info("Received GIF upload request for evaluation")
    if gif.content_type != "image/gif":
        logger.warning(
            "Rejected GIF upload due to invalid content type: %s",
            gif.content_type,
        )
        raise HTTPException(
            status_code=400,
            detail="Only GIF images are supported",
        )

    service = _service(session)
    try:
        stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
        evaluation_row = await session.scalar(stmt)
        if not evaluation_row:
            raise ValueError(f"Evaluation {evaluation_id} not found")
    except ValueError as exc:
        logger.warning("GIF upload requested for unknown evaluation")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _reset_session_transaction(session)

    file_data = await gif.read()
    received_bytes = len(file_data) if file_data else 0
    logger.info("Read GIF upload payload size_bytes=%s", received_bytes)
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
        raise HTTPException(status_code=500, detail="Failed to store GIF image") from exc

    gif_url = build_public_url(object_key)

    try:
        logger.info("Updating evaluation %s with GIF URL %s", evaluation_id, gif_url)
        await service.update_gif_recording(evaluation_id, gif_url)
    except ValueError as exc:
        logger.error("Unable to update GIF record for %s: %s", evaluation_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update evaluation record") from exc

    logger.info(
        "Uploaded GIF for evaluation %s to key %s (size_bytes=%s, url=%s)",
        evaluation_id,
        object_key,
        received_bytes,
        gif_url,
    )
    return EvaluationGifUploadResponse(success=True, data={"gifUrl": gif_url})

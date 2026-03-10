from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query / body models (Sonar: reduce params, typed body)
# ---------------------------------------------------------------------------


class TasksListQuery(BaseModel):
    """Query params for list_tasks and search_tasks (shared)."""

    page: int = 1
    limit: int = 20
    includeDetails: bool = True
    agentRunId: Optional[str] = None
    agentId: Optional[str] = None
    validatorId: Optional[str] = None
    website: Optional[str] = None
    useCase: Optional[str] = None
    status: Optional[str] = None
    query: Optional[str] = None
    minScore: Optional[float] = None
    maxScore: Optional[float] = None
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    sortBy: str = "startTime"
    sortOrder: str = "desc"

    model_config = {"extra": "forbid"}


def get_tasks_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    includeDetails: Annotated[
        bool,
        Query(True, description="Include full task details (actions/screenshots/logs). Set false for lightweight listing."),
    ] = True,
    agentRunId: Annotated[Optional[str], Query(None)] = None,
    agentId: Annotated[Optional[str], Query(None)] = None,
    validatorId: Annotated[Optional[str], Query(None)] = None,
    website: Annotated[Optional[str], Query(None)] = None,
    useCase: Annotated[Optional[str], Query(None)] = None,
    status: Annotated[Optional[str], Query(None)] = None,
    query: Annotated[Optional[str], Query(None)] = None,
    minScore: Annotated[Optional[float], Query(None)] = None,
    maxScore: Annotated[Optional[float], Query(None)] = None,
    startDate: Annotated[Optional[datetime], Query(None)] = None,
    endDate: Annotated[Optional[datetime], Query(None)] = None,
    sortBy: Annotated[str, Query("startTime")] = "startTime",
    sortOrder: Annotated[str, Query("desc")] = "desc",
) -> TasksListQuery:
    return TasksListQuery(
        page=page,
        limit=limit,
        includeDetails=includeDetails,
        agentRunId=agentRunId,
        agentId=agentId,
        validatorId=validatorId,
        website=website,
        useCase=useCase,
        status=status,
        query=query,
        minScore=minScore,
        maxScore=maxScore,
        startDate=startDate,
        endDate=endDate,
        sortBy=sortBy,
        sortOrder=sortOrder,
    )


class CompareTasksRequest(BaseModel):
    """Body for POST /compare (Sonar: avoid raw dict)."""

    taskIds: list[str] = Field(default_factory=list, description="List of task IDs to compare")


# ---------------------------------------------------------------------------
# Helper: get task context or 404 (Sonar: deduplicate try/except pattern)
# ---------------------------------------------------------------------------


async def _fetch_task_or_404(
    session: AsyncSession,
    task_id: str,
    fetch: Callable[[UIDataService, str], Awaitable[Any]],
) -> Any:
    service = await _service(session)
    try:
        return await fetch(service, task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# List & search
# ---------------------------------------------------------------------------


@router.get("")
async def list_tasks(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[TasksListQuery, Depends(get_tasks_list_query)],
):
    service = await _service(session)
    data = await service.list_tasks(
        page=q.page,
        limit=q.limit,
        agent_run_id=q.agentRunId,
        agent_id=q.agentId,
        validator_id=q.validatorId,
        website=q.website,
        use_case=q.useCase,
        status=q.status,
        query=q.query,
        min_score=q.minScore,
        max_score=q.maxScore,
        start_date=q.startDate,
        end_date=q.endDate,
        sort_by=q.sortBy,
        sort_order=q.sortOrder,
        include_details=q.includeDetails,
    )
    return {"success": True, "data": data}


@router.get("/search")
async def search_tasks(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[TasksListQuery, Depends(get_tasks_list_query)],
):
    service = await _service(session)
    data = await service.search_tasks(
        page=q.page,
        limit=q.limit,
        agent_run_id=q.agentRunId,
        agent_id=q.agentId,
        validator_id=q.validatorId,
        website=q.website,
        use_case=q.useCase,
        status=q.status,
        query=q.query,
        min_score=q.minScore,
        max_score=q.maxScore,
        start_date=q.startDate,
        end_date=q.endDate,
        sort_by=q.sortBy,
        sort_order=q.sortOrder,
        include_details=q.includeDetails,
    )
    return {"success": True, "data": data}


# ---------------------------------------------------------------------------
# Get by task_id (shared helper to reduce duplication)
# ---------------------------------------------------------------------------


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"task": detail}}


@router.get("/{task_id}/details")
async def get_task_details(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"details": detail}}


@router.get("/{task_id}/personas")
async def get_task_personas(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    personas = service.build_personas(context)
    return {"success": True, "data": {"personas": personas.model_dump()}}


@router.get("/{task_id}/statistics")
async def get_task_statistics(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    statistics = service.build_task_statistics(context)
    return {"success": True, "data": {"statistics": statistics.model_dump()}}


@router.get("/{task_id}/actions")
async def get_task_actions(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(50, ge=1, le=200)] = 50,
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    actions = service.build_actions(context)
    total = len(actions)
    success_count = sum(1 for action in actions if getattr(action, "success", False))
    fail_count = sum(
        1 for action in actions if getattr(action, "error", False) or not getattr(action, "success", False)
    )
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


@router.get("/{task_id}/screenshots")
async def get_task_screenshots(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    screenshots = service.build_screenshots(context)
    return {
        "success": True,
        "data": {"screenshots": [shot.model_dump() for shot in screenshots]},
    }


@router.get("/{task_id}/results")
async def get_task_results(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    results = service.build_task_results(context)
    return {"success": True, "data": {"results": results}}


@router.get("/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    logs = service.build_logs(context)
    return {"success": True, "data": {"logs": [log.model_dump() for log in logs]}}


@router.get("/{task_id}/timeline")
async def get_task_timeline(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    timeline = service.build_timeline(context)
    return {"success": True, "data": {"timeline": [item.model_dump() for item in timeline]}}


@router.get("/{task_id}/metrics")
async def get_task_metrics(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = await _service(session)
    metrics = service.build_metrics(context)
    return {"success": True, "data": {"metrics": metrics}}


# ---------------------------------------------------------------------------
# Analytics & compare
# ---------------------------------------------------------------------------


@router.get("/analytics")
async def get_task_analytics(
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = await _service(session)
    analytics = await service.analytics()
    return {"success": True, "data": {"analytics": analytics}}


@router.post("/compare")
async def compare_tasks(
    payload: CompareTasksRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = await _service(session)
    comparison = await service.compare_tasks(payload.taskIds)
    return {"success": True, "data": comparison.model_dump()}

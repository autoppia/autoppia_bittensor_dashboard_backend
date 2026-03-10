from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query / body models (Sonar: reduce params, typed body)
# ---------------------------------------------------------------------------


class TasksListQuery(BaseModel):
    """Query params for list_tasks and search_tasks (shared)."""

    page: int = 1
    limit: int = 20
    include_details: bool = True
    agent_run_id: str | None = None
    agent_id: str | None = None
    validator_id: str | None = None
    website: str | None = None
    use_case: str | None = None
    status: str | None = None
    query: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    sort_by: str = "startTime"
    sort_order: str = "desc"

    model_config = {"extra": "forbid"}


def get_tasks_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    include_details: Annotated[
        bool,
        Query(True, description="Include full task details (actions/screenshots/logs). Set false for lightweight listing.", alias="includeDetails"),
    ] = True,
    agent_run_id: Annotated[str | None, Query(None, alias="agentRunId")] = None,
    agent_id: Annotated[str | None, Query(None, alias="agentId")] = None,
    validator_id: Annotated[str | None, Query(None, alias="validatorId")] = None,
    website: Annotated[str | None, Query(None)] = None,
    use_case: Annotated[str | None, Query(None, alias="useCase")] = None,
    status: Annotated[str | None, Query(None)] = None,
    query: Annotated[str | None, Query(None)] = None,
    min_score: Annotated[float | None, Query(None, alias="minScore")] = None,
    max_score: Annotated[float | None, Query(None, alias="maxScore")] = None,
    start_date: Annotated[datetime | None, Query(None, alias="startDate")] = None,
    end_date: Annotated[datetime | None, Query(None, alias="endDate")] = None,
    sort_by: Annotated[str, Query("startTime", alias="sortBy")] = "startTime",
    sort_order: Annotated[str, Query("desc", alias="sortOrder")] = "desc",
) -> TasksListQuery:
    return TasksListQuery(
        page=page,
        limit=limit,
        include_details=include_details,
        agent_run_id=agent_run_id,
        agent_id=agent_id,
        validator_id=validator_id,
        website=website,
        use_case=use_case,
        status=status,
        query=query,
        min_score=min_score,
        max_score=max_score,
        start_date=start_date,
        end_date=end_date,
        sort_by=sort_by,
        sort_order=sort_order,
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
    service = _service(session)
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
    service = _service(session)
    data = await service.list_tasks(
        page=q.page,
        limit=q.limit,
        agent_run_id=q.agent_run_id,
        agent_id=q.agent_id,
        validator_id=q.validator_id,
        website=q.website,
        use_case=q.use_case,
        status=q.status,
        query=q.query,
        min_score=q.min_score,
        max_score=q.max_score,
        start_date=q.start_date,
        end_date=q.end_date,
        sort_by=q.sort_by,
        sort_order=q.sort_order,
        include_details=q.include_details,
    )
    return {"success": True, "data": data}


@router.get("/search")
async def search_tasks(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[TasksListQuery, Depends(get_tasks_list_query)],
):
    service = _service(session)
    data = await service.search_tasks(
        page=q.page,
        limit=q.limit,
        agent_run_id=q.agent_run_id,
        agent_id=q.agent_id,
        validator_id=q.validator_id,
        website=q.website,
        use_case=q.use_case,
        status=q.status,
        query=q.query,
        min_score=q.min_score,
        max_score=q.max_score,
        start_date=q.start_date,
        end_date=q.end_date,
        sort_by=q.sort_by,
        sort_order=q.sort_order,
        include_details=q.include_details,
    )
    return {"success": True, "data": data}


# ---------------------------------------------------------------------------
# Get by task_id (shared helper to reduce duplication)
# ---------------------------------------------------------------------------


@router.get("/{task_id}", responses={404: {"description": "Task not found"}})
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"task": detail}}


@router.get("/{task_id}/details")
async def get_task_details(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"details": detail}}


@router.get("/{task_id}/personas", responses={404: {"description": "Task not found"}})
async def get_task_personas(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    personas = service.build_personas(context)
    return {"success": True, "data": {"personas": personas.model_dump()}}


@router.get("/{task_id}/statistics", responses={404: {"description": "Task not found"}})
async def get_task_statistics(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    statistics = service.build_task_statistics(context)
    return {"success": True, "data": {"statistics": statistics.model_dump()}}


@router.get("/{task_id}/actions", responses={404: {"description": "Task not found"}})
async def get_task_actions(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(50, ge=1, le=200)] = 50,
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
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


@router.get("/{task_id}/screenshots", responses={404: {"description": "Task not found"}})
async def get_task_screenshots(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    screenshots = service.build_screenshots(context)
    return {
        "success": True,
        "data": {"screenshots": [shot.model_dump() for shot in screenshots]},
    }


@router.get("/{task_id}/results", responses={404: {"description": "Task not found"}})
async def get_task_results(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    results = service.build_task_results(context)
    return {"success": True, "data": {"results": results}}


@router.get("/{task_id}/logs", responses={404: {"description": "Task not found"}})
async def get_task_logs(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    logs = service.build_logs(context)
    return {"success": True, "data": {"logs": [log.model_dump() for log in logs]}}


@router.get("/{task_id}/timeline", responses={404: {"description": "Task not found"}})
async def get_task_timeline(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    timeline = service.build_timeline(context)
    return {"success": True, "data": {"timeline": [item.model_dump() for item in timeline]}}


@router.get("/{task_id}/metrics", responses={404: {"description": "Task not found"}})
async def get_task_metrics(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    context = await _fetch_task_or_404(session, task_id, lambda s, tid: s.get_task(tid))
    service = _service(session)
    metrics = service.build_metrics(context)
    return {"success": True, "data": {"metrics": metrics}}


# ---------------------------------------------------------------------------
# Analytics & compare
# ---------------------------------------------------------------------------


@router.get("/analytics")
async def get_task_analytics(
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _service(session)
    analytics = await service.analytics()
    return {"success": True, "data": {"analytics": analytics}}


@router.post("/compare")
async def compare_tasks(
    payload: CompareTasksRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    service = _service(session)
    comparison = await service.compare_tasks(payload.taskIds)
    return {"success": True, "data": comparison.model_dump()}

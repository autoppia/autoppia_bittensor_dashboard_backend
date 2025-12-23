from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.tasks_service import TasksService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


async def _service(session: AsyncSession) -> TasksService:
    return TasksService(session)


@router.get("")
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    includeDetails: bool = Query(
        True,
        description="Include full task details (actions/screenshots/logs). Set false for lightweight listing.",
    ),
    agentRunId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    validatorId: Optional[str] = Query(None),
    website: Optional[str] = Query(None),
    useCase: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    minScore: Optional[float] = Query(None),
    maxScore: Optional[float] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    sortBy: str = Query("startTime"),
    sortOrder: str = Query("desc"),
):
    service = await _service(session)
    data = await service.list_tasks(
        page=page,
        limit=limit,
        agent_run_id=agentRunId,
        agent_id=agentId,
        validator_id=validatorId,
        website=website,
        use_case=useCase,
        status=status,
        query=query,
        min_score=minScore,
        max_score=maxScore,
        start_date=startDate,
        end_date=endDate,
        sort_by=sortBy,
        sort_order=sortOrder,
        include_details=includeDetails,
    )
    return {"success": True, "data": data}


@router.get("/search")
async def search_tasks(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    includeDetails: bool = Query(
        True,
        description="Include full task details (actions/screenshots/logs). Set false for lightweight listing.",
    ),
    agentRunId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    validatorId: Optional[str] = Query(None),
    website: Optional[str] = Query(None),
    useCase: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    minScore: Optional[float] = Query(None),
    maxScore: Optional[float] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    sortBy: str = Query("startTime"),
    sortOrder: str = Query("desc"),
):
    service = await _service(session)
    data = await service.search_tasks(
        page=page,
        limit=limit,
        agent_run_id=agentRunId,
        agent_id=agentId,
        validator_id=validatorId,
        website=website,
        use_case=useCase,
        status=status,
        query=query,
        min_score=minScore,
        max_score=maxScore,
        start_date=startDate,
        end_date=endDate,
        sort_by=sortBy,
        sort_order=sortOrder,
        include_details=includeDetails,
    )
    return {"success": True, "data": data}


@router.get("/{task_id}")
async def get_task(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"task": detail}}


@router.get("/{task_id}/details")
async def get_task_details(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"details": detail}}


@router.get("/{task_id}/personas")
async def get_task_personas(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    personas = service.build_personas(context)
    return {"success": True, "data": {"personas": personas.model_dump()}}


@router.get("/{task_id}/statistics")
async def get_task_statistics(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    statistics = service.build_task_statistics(context)
    return {"success": True, "data": {"statistics": statistics.model_dump()}}


@router.get("/{task_id}/actions")
async def get_task_actions(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    actions = service.build_actions(context)
    total = len(actions)
    
    # Count total successful and failed actions (not just paginated)
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


@router.get("/{task_id}/screenshots")
async def get_task_screenshots(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    screenshots = service.build_screenshots(context)
    return {
        "success": True,
        "data": {"screenshots": [shot.model_dump() for shot in screenshots]},
    }


@router.get("/{task_id}/results")
async def get_task_results(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    results = service.build_task_results(context)
    return {"success": True, "data": {"results": results}}


@router.get("/{task_id}/logs")
async def get_task_logs(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logs = service.build_logs(context)
    return {"success": True, "data": {"logs": [log.model_dump() for log in logs]}}


@router.get("/{task_id}/timeline")
async def get_task_timeline(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    timeline = service.build_timeline(context)
    return {"success": True, "data": {"timeline": [item.model_dump() for item in timeline]}}


@router.get("/{task_id}/metrics")
async def get_task_metrics(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics = service.build_metrics(context)
    return {"success": True, "data": {"metrics": metrics}}


@router.get("/analytics")
async def get_task_analytics(session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    analytics = await service.analytics()
    return {"success": True, "data": {"analytics": analytics}}


@router.post("/compare")
async def compare_tasks(payload: dict, session: AsyncSession = Depends(get_session)):
    task_ids = payload.get("taskIds", [])
    if not isinstance(task_ids, list):
        raise HTTPException(status_code=400, detail="taskIds must be a list")
    service = await _service(session)
    comparison = await service.compare_tasks(task_ids)
    return {"success": True, "data": comparison.model_dump()}

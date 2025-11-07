from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.tasks_service import TasksService
from app.services.redis_cache import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


async def _service(session: AsyncSession) -> TasksService:
    return TasksService(session)


@router.get("")
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
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
    )
    return {"success": True, "data": data}


@router.get("/search")
async def search_tasks(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
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
    includeDetails: bool = Query(
        False, description="Include actions, screenshots, logs (SLOW)"
    ),
):
    """
    Search tasks with optional detailed data.

    Set includeDetails=false for fast searches (dropdowns, lists).
    Set includeDetails=true for full task details (task detail pages).
    """
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
@cache("task", ttl=300)  # Cache 5 minutes
async def get_task(task_id: str, session: AsyncSession = Depends(get_session)):
    service = await _service(session)
    try:
        context = await service.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail = service.build_task_detail(context)
    return {"success": True, "data": {"task": detail}}


@router.get("/{task_id}/details")
@cache("task_details", ttl=300)  # Cache 5 minutes
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
@cache("task_statistics", ttl=180)  # Cache 3 minutes
async def get_task_statistics(
    task_id: str, session: AsyncSession = Depends(get_session)
):
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
    success_count = sum(1 for action in actions if getattr(action, "success", False))
    fail_count = sum(
        1
        for action in actions
        if getattr(action, "error", False) or not getattr(action, "success", False)
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
    task_id: str, session: AsyncSession = Depends(get_session)
):
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
    return {
        "success": True,
        "data": {"timeline": [item.model_dump() for item in timeline]},
    }


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


@router.get("/with-solutions")
async def get_tasks_with_solutions(
    session: AsyncSession = Depends(get_session),
    key: str = Query(..., description="API key required"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=500, description="Items per page"),
    taskId: Optional[str] = Query(None, description="Filter by task ID"),
    website: Optional[str] = Query(
        None, description="Filter by website (e.g., 'autocinema', 'autobooks')"
    ),
    useCase: Optional[str] = Query(
        None, description="Filter by use case (e.g., 'FILM DETAIL')"
    ),
    minerUid: Optional[int] = Query(None, description="Filter by miner UID"),
    validatorId: Optional[str] = Query(None, description="Filter by validator ID"),
    roundId: Optional[int] = Query(None, description="Filter by round number"),
    success: Optional[bool] = Query(
        None, description="Filter by result: true (score=1), false (score=0)"
    ),
    sort: str = Query(
        "created_at_desc",
        description="Sort: created_at_desc, created_at_asc, score_desc, score_asc",
    ),
):
    """
    Get tasks with solutions for RL training.

    Response structure:
        task:
            - taskId, website, useCase, intent
            - startUrl, requiredUrl (with seed parameter)
            - createdAt
        solution:
            - taskSolutionId
            - actions (array of actions taken)
            - trajectory (array of state transitions)
        evaluation:
            - evaluationResultId
            - score (0-100)
            - passed (true/false)
        agentRun:
            - agentRunId, minerUid, minerHotkey
            - validatorUid, validatorHotkey

    Filters:
        taskId: Specific task ID
        website: Project (autocinema, autobooks)
        useCase: Use case (FILM DETAIL, SEARCH BOOK)
        minerUid: Miner UID
        validatorId: Validator hotkey
        roundId: Round number
        success: true (passed), false (failed), null (all)
        sort: created_at_desc, created_at_asc, score_desc, score_asc

    Examples:
        ?key=AIagent2025&website=autocinema&success=true&limit=500
        ?key=AIagent2025&minerUid=42
        ?key=AIagent2025&useCase=FILM%20DETAIL&success=false
    """
    # Validate API key
    if key != "AIagent2025":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    # Parse sort
    sort_mapping = {
        "created_at_desc": ("created_at", "desc"),
        "created_at_asc": ("created_at", "asc"),
        "score_desc": ("score", "desc"),
        "score_asc": ("score", "asc"),
    }
    sort_by, sort_order = sort_mapping.get(sort, ("created_at", "desc"))

    # Convert success to status
    status_filter = None
    if success is True:
        status_filter = "completed"  # score = 1
    elif success is False:
        status_filter = "failed"  # score = 0

    service = await _service(session)
    data = await service.get_tasks_with_solutions(
        page=page,
        limit=limit,
        task_id=taskId,
        website=website,
        use_case=useCase,
        miner_uid=minerUid,
        validator_id=validatorId,
        round_id=roundId,
        status=status_filter,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {"success": True, "data": data}

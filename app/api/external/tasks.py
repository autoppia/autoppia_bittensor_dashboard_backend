from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.tasks_service_extension import get_tasks_with_solutions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["external-tasks"])


@router.get("/with-solutions")
async def get_tasks_with_solutions_endpoint(
    session: AsyncSession = Depends(get_session),
    key: str = Query(..., description="API key for authentication"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    taskId: Optional[str] = Query(None, alias="taskId"),
    website: Optional[str] = Query(None),
    useCase: Optional[str] = Query(None, alias="useCase"),
    webVersion: Optional[str] = Query(None, alias="webVersion", description="Filter by web demo version (e.g., '0.1.0+d2e4029e')"),
    minerUid: Optional[int] = Query(None, alias="minerUid"),
    agentId: Optional[str] = Query(None, alias="agentId"),
    validatorId: Optional[str] = Query(None, alias="validatorId"),
    roundId: Optional[int] = Query(None, alias="roundId"),
    minScore: Optional[float] = Query(None, alias="minScore"),
    maxScore: Optional[float] = Query(None, alias="maxScore"),
    status: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    sort: Optional[str] = Query("created_at_desc", description="Sort order: created_at_desc, created_at_asc, score_desc, score_asc"),
):
    """
    Get tasks with their solutions, applying multiple filters.
    
    This endpoint requires an API key and supports pagination, filtering, and sorting.
    """
    # Validate API key
    if key != "AIagent2025":
        raise HTTPException(status_code=422, detail="Invalid API key")
    
    # Parse sort parameter
    sort_by = "created_at"
    sort_order = "desc"
    if sort:
        if sort == "created_at_desc":
            sort_by = "created_at"
            sort_order = "desc"
        elif sort == "created_at_asc":
            sort_by = "created_at"
            sort_order = "asc"
        elif sort == "score_desc":
            sort_by = "score"
            sort_order = "desc"
        elif sort == "score_asc":
            sort_by = "score"
            sort_order = "asc"
        else:
            # Default to created_at_desc if invalid sort
            sort_by = "created_at"
            sort_order = "desc"
    
    data = await get_tasks_with_solutions(
        session=session,
        page=page,
        limit=limit,
        task_id=taskId,
        website=website,
        use_case=useCase,
        web_version=webVersion,
        miner_uid=minerUid,
        agent_id=agentId,
        validator_id=validatorId,
        round_id=roundId,
        min_score=minScore,
        max_score=maxScore,
        status=status,
        success=success,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    
    return {"success": True, "data": data}



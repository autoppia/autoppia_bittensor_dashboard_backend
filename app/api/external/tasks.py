from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.external_tasks_query import get_tasks_with_solutions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["external-tasks"])


class TasksWithSolutionsQuery(BaseModel):
    """Query parameters for GET /with-solutions (keeps endpoint under Sonar param limit)."""

    key: str
    page: int = 1
    limit: int = 50
    taskId: Optional[str] = None
    website: Optional[str] = None
    useCase: Optional[str] = None
    webVersion: Optional[str] = None
    minerUid: Optional[int] = None
    agentId: Optional[str] = None
    validatorId: Optional[str] = None
    roundId: Optional[int] = None
    minScore: Optional[float] = None
    maxScore: Optional[float] = None
    status: Optional[str] = None
    success: Optional[bool] = None
    sort: str = "created_at_desc"

    model_config = {"extra": "forbid"}


def get_tasks_query(
    key: Annotated[str, Query(description="API key for authentication")],
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(50, ge=1, le=500)] = 50,
    taskId: Annotated[Optional[str], Query(None, alias="taskId")] = None,
    website: Annotated[Optional[str], Query(None)] = None,
    useCase: Annotated[Optional[str], Query(None, alias="useCase")] = None,
    webVersion: Annotated[
        Optional[str], Query(None, alias="webVersion", description="Filter by web demo version (e.g., '0.1.0+d2e4029e')")
    ] = None,
    minerUid: Annotated[Optional[int], Query(None, alias="minerUid")] = None,
    agentId: Annotated[Optional[str], Query(None, alias="agentId")] = None,
    validatorId: Annotated[Optional[str], Query(None, alias="validatorId")] = None,
    roundId: Annotated[Optional[int], Query(None, alias="roundId")] = None,
    minScore: Annotated[Optional[float], Query(None, alias="minScore")] = None,
    maxScore: Annotated[Optional[float], Query(None, alias="maxScore")] = None,
    status: Annotated[Optional[str], Query(None)] = None,
    success: Annotated[Optional[bool], Query(None)] = None,
    sort: Annotated[
        Optional[str],
        Query("created_at_desc", description="Sort order: created_at_desc, created_at_asc, score_desc, score_asc"),
    ] = "created_at_desc",
) -> TasksWithSolutionsQuery:
    return TasksWithSolutionsQuery(
        key=key,
        page=page,
        limit=limit,
        taskId=taskId,
        website=website,
        useCase=useCase,
        webVersion=webVersion,
        minerUid=minerUid,
        agentId=agentId,
        validatorId=validatorId,
        roundId=roundId,
        minScore=minScore,
        maxScore=maxScore,
        status=status,
        success=success,
        sort=sort or "created_at_desc",
    )


@router.get("/with-solutions")
async def get_tasks_with_solutions_endpoint(
    session: Annotated[AsyncSession, Depends(get_session)],
    query: Annotated[TasksWithSolutionsQuery, Depends(get_tasks_query)],
):
    """
    Get tasks with their solutions, applying multiple filters.

    This endpoint requires an API key and supports pagination, filtering, and sorting.
    """
    if query.key != "AIagent2025":
        raise HTTPException(status_code=422, detail="Invalid API key")

    sort_by = "created_at"
    sort_order = "desc"
    if query.sort:
        if query.sort == "created_at_desc":
            sort_by = "created_at"
            sort_order = "desc"
        elif query.sort == "created_at_asc":
            sort_by = "created_at"
            sort_order = "asc"
        elif query.sort == "score_desc":
            sort_by = "score"
            sort_order = "desc"
        elif query.sort == "score_asc":
            sort_by = "score"
            sort_order = "asc"
        else:
            sort_by = "created_at"
            sort_order = "desc"

    data = await get_tasks_with_solutions(
        session=session,
        page=query.page,
        limit=query.limit,
        task_id=query.taskId,
        website=query.website,
        use_case=query.useCase,
        web_version=query.webVersion,
        miner_uid=query.minerUid,
        agent_id=query.agentId,
        validator_id=query.validatorId,
        round_id=query.roundId,
        min_score=query.minScore,
        max_score=query.maxScore,
        status=query.status,
        success=query.success,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return {"success": True, "data": data}

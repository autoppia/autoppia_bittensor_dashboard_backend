from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ui.external_tasks_query import (
    TaskSolutionsQueryParams,
    get_tasks_with_solutions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["external-tasks"])


class TasksWithSolutionsQuery(BaseModel):
    """Query parameters for GET /with-solutions (keeps endpoint under Sonar param limit)."""

    key: str
    page: int = 1
    limit: int = 50
    taskId: str | None = None
    website: str | None = None
    useCase: str | None = None
    webVersion: str | None = None
    minerUid: int | None = None
    agentId: str | None = None
    validatorId: str | None = None
    roundId: int | None = None
    minScore: float | None = None
    maxScore: float | None = None
    status: str | None = None
    success: bool | None = None
    sort: str = "created_at_desc"

    model_config = {"extra": "forbid"}


def get_tasks_query(
    key: Annotated[str, Query(description="API key for authentication")],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    task_id: Annotated[str | None, Query(alias="taskId")] = None,
    website: Annotated[str | None, Query()] = None,
    use_case: Annotated[str | None, Query(alias="useCase")] = None,
    web_version: Annotated[str | None, Query(alias="webVersion", description="Filter by web demo version (e.g., '0.1.0+d2e4029e')")] = None,
    miner_uid: Annotated[int | None, Query(alias="minerUid")] = None,
    agent_id: Annotated[str | None, Query(alias="agentId")] = None,
    validator_id: Annotated[str | None, Query(alias="validatorId")] = None,
    round_id: Annotated[int | None, Query(alias="roundId")] = None,
    min_score: Annotated[float | None, Query(alias="minScore")] = None,
    max_score: Annotated[float | None, Query(alias="maxScore")] = None,
    status: Annotated[str | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    sort: Annotated[
        str | None,
        Query(description="Sort order: created_at_desc, created_at_asc, score_desc, score_asc"),
    ] = "created_at_desc",
) -> TasksWithSolutionsQuery:
    return TasksWithSolutionsQuery(
        key=key,
        page=page,
        limit=limit,
        taskId=task_id,
        website=website,
        useCase=use_case,
        webVersion=web_version,
        minerUid=miner_uid,
        agentId=agent_id,
        validatorId=validator_id,
        roundId=round_id,
        minScore=min_score,
        maxScore=max_score,
        status=status,
        success=success,
        sort=sort or "created_at_desc",
    )


@router.get(
    "/with-solutions",
    responses={422: {"description": "Invalid API key"}},
)
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
    if query.sort == "created_at_asc":
        sort_by, sort_order = "created_at", "asc"
    elif query.sort == "score_desc":
        sort_by, sort_order = "score", "desc"
    elif query.sort == "score_asc":
        sort_by, sort_order = "score", "asc"
    # created_at_desc or unknown: keep default

    query_params = TaskSolutionsQueryParams(
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
    data = await get_tasks_with_solutions(session=session, params=query_params)

    return {"success": True, "data": data}

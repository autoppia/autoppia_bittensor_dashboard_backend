from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.agent_runs import (
    AgentRunDetailResponse,
    AgentRunsListResponse,
    ComparisonResponse,
    LogsResponse,
    MetricsResponse,
    PersonasResponse,
    StatisticsResponse,
    SummaryResponse,
    TasksResponse,
    TimelineResponse,
)
from app.services.redis_cache import cache
from app.services.ui.ui_data_service import UIDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-runs", tags=["agent-runs"])

NOT_FOUND_ERROR = {"message": "Agent run not found", "code": "AGENT_RUN_NOT_FOUND"}


def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query/body models to keep endpoint signatures compact and typed
# ---------------------------------------------------------------------------


class AgentRunsListQuery(BaseModel):
    """Query params for list endpoint."""

    page: int = 1
    limit: int = 20
    round_id: str | None = None
    validator_id: str | None = None
    agent_id: str | None = None
    query: str | None = None
    status: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    include_unfinished: bool = False
    sort_by: str = "startTime"
    sort_order: str = "desc"

    model_config = {"extra": "forbid"}


def get_agent_runs_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    round_id: Annotated[str | None, Query(None, alias="roundId")] = None,
    validator_id: Annotated[str | None, Query(None, alias="validatorId")] = None,
    agent_id: Annotated[str | None, Query(None, alias="agentId")] = None,
    query: Annotated[str | None, Query(None)] = None,
    status: Annotated[str | None, Query(None)] = None,
    start_date: Annotated[datetime | None, Query(None, alias="startDate")] = None,
    end_date: Annotated[datetime | None, Query(None, alias="endDate")] = None,
    include_unfinished: Annotated[bool, Query(False, description="Include runs from active/non-finalized rounds", alias="includeUnfinished")] = False,
    sort_by: Annotated[str, Query("startTime", alias="sortBy")] = "startTime",
    sort_order: Annotated[str, Query("desc", alias="sortOrder")] = "desc",
) -> AgentRunsListQuery:
    return AgentRunsListQuery(
        page=page,
        limit=limit,
        round_id=round_id,
        validator_id=validator_id,
        agent_id=agent_id,
        query=query,
        status=status,
        start_date=start_date,
        end_date=end_date,
        include_unfinished=include_unfinished,
        sort_by=sort_by,
        sort_order=sort_order,
    )


class CompareRunsRequest(BaseModel):
    """Body for POST /compare."""

    runIds: list[str] = Field(..., min_length=1, description="Non-empty list of run IDs to compare")


# ---------------------------------------------------------------------------
# Helper: get run data or 404
# ---------------------------------------------------------------------------


async def _fetch_run_or_404(
    session: AsyncSession,
    run_id: str,
    fetch: Callable[[UIDataService, str], Awaitable[Any]],
) -> Any:
    service = _service(session)
    try:
        return await fetch(service, run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
@cache("agent_runs_list_v7", ttl=600)
async def list_agent_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[AgentRunsListQuery, Depends(get_agent_runs_list_query)],
) -> AgentRunsListResponse:
    service = _service(session)
    data = await service.list_agent_runs_catalog(
        page=q.page,
        limit=q.limit,
        round_id=q.round_id,
        validator_id=q.validator_id,
        agent_id=q.agent_id,
        query=q.query,
        status=q.status,
        start_date=q.start_date,
        end_date=q.end_date,
        include_unfinished=q.include_unfinished,
        sort_by=q.sort_by,
        sort_order=q.sort_order,
    )
    return AgentRunsListResponse(success=True, data=data)


# ---------------------------------------------------------------------------
# Get-by-run_id endpoints (same behavior, shared helper to reduce duplication)
# ---------------------------------------------------------------------------


@router.get("/{run_id}/get-agent-run", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_complete(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_complete_data(rid))
    return {"success": True, "data": result}


@router.get("/{run_id}", responses={404: {"description": "Agent run not found"}})
async def get_agent_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRunDetailResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_detail_data(rid))
    return AgentRunDetailResponse(success=True, data={"run": result})


@router.get("/{run_id}/personas", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_personas(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PersonasResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_personas_data(rid))
    return PersonasResponse(success=True, data={"personas": result})


@router.get("/{run_id}/stats", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_statistics(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatisticsResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_statistics_data(rid))
    return StatisticsResponse(success=True, data={"stats": result})


@router.get("/{run_id}/summary", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_summary(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SummaryResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_summary_data(rid))
    return SummaryResponse(success=True, data={"summary": result})


@router.get("/{run_id}/tasks", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_tasks(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TasksResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_tasks_data(rid))
    return TasksResponse(success=True, data=result)


@router.get("/{run_id}/timeline", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_timeline(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_timeline_data(rid))
    return TimelineResponse(success=True, data={"timeline": result})


@router.get("/{run_id}/logs", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_logs(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogsResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_logs_data(rid))
    return LogsResponse(success=True, data={"logs": result})


@router.get("/{run_id}/metrics", responses={404: {"description": "Agent run not found"}})
async def get_agent_run_metrics(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetricsResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_metrics_data(rid))
    return MetricsResponse(success=True, data={"metrics": result})


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


@router.post("/compare")
async def compare_agent_runs(
    payload: CompareRunsRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ComparisonResponse:
    service = _service(session)
    comparison = await service.compare_agent_runs_data(payload.runIds)
    return ComparisonResponse(success=True, data=comparison)

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable, Optional

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


async def _service(session: AsyncSession) -> UIDataService:
    return UIDataService(session)


# ---------------------------------------------------------------------------
# Query/body models (Sonar: reduce params, typed body)
# ---------------------------------------------------------------------------


class AgentRunsListQuery(BaseModel):
    """Query params for list endpoint (keeps endpoint under Sonar param limit)."""

    page: int = 1
    limit: int = 20
    roundId: Optional[str] = None
    validatorId: Optional[str] = None
    agentId: Optional[str] = None
    query: Optional[str] = None
    status: Optional[str] = None
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    includeUnfinished: bool = False
    sortBy: str = "startTime"
    sortOrder: str = "desc"

    model_config = {"extra": "forbid"}


def get_agent_runs_list_query(
    page: Annotated[int, Query(1, ge=1)] = 1,
    limit: Annotated[int, Query(20, ge=1, le=100)] = 20,
    roundId: Annotated[Optional[str], Query(None)] = None,
    validatorId: Annotated[Optional[str], Query(None)] = None,
    agentId: Annotated[Optional[str], Query(None)] = None,
    query: Annotated[Optional[str], Query(None)] = None,
    status: Annotated[Optional[str], Query(None)] = None,
    startDate: Annotated[Optional[datetime], Query(None)] = None,
    endDate: Annotated[Optional[datetime], Query(None)] = None,
    includeUnfinished: Annotated[
        bool, Query(False, description="Include runs from active/non-finalized rounds")
    ] = False,
    sortBy: Annotated[str, Query("startTime")] = "startTime",
    sortOrder: Annotated[str, Query("desc")] = "desc",
) -> AgentRunsListQuery:
    return AgentRunsListQuery(
        page=page,
        limit=limit,
        roundId=roundId,
        validatorId=validatorId,
        agentId=agentId,
        query=query,
        status=status,
        startDate=startDate,
        endDate=endDate,
        includeUnfinished=includeUnfinished,
        sortBy=sortBy,
        sortOrder=sortOrder,
    )


class CompareRunsRequest(BaseModel):
    """Body for POST /compare (Sonar: avoid raw dict)."""

    runIds: list[str] = Field(..., min_length=1, description="Non-empty list of run IDs to compare")


# ---------------------------------------------------------------------------
# Helper: get run data or 404 (Sonar: deduplicate repeated try/except pattern)
# ---------------------------------------------------------------------------


async def _fetch_run_or_404(
    session: AsyncSession,
    run_id: str,
    fetch: Callable[[UIDataService, str], Awaitable[Any]],
) -> Any:
    service = await _service(session)
    try:
        return await fetch(service, run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("")
@cache("agent_runs_list_v5", ttl=600)
async def list_agent_runs(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[AgentRunsListQuery, Depends(get_agent_runs_list_query)],
) -> AgentRunsListResponse:
    service = await _service(session)
    data = await service.list_agent_runs_catalog(
        page=q.page,
        limit=q.limit,
        round_id=q.roundId,
        validator_id=q.validatorId,
        agent_id=q.agentId,
        query=q.query,
        status=q.status,
        start_date=q.startDate,
        end_date=q.endDate,
        include_unfinished=q.includeUnfinished,
        sort_by=q.sortBy,
        sort_order=q.sortOrder,
    )
    return AgentRunsListResponse(success=True, data=data)


# ---------------------------------------------------------------------------
# Get-by-run_id endpoints (same behavior, shared helper to reduce duplication)
# ---------------------------------------------------------------------------


@router.get("/{run_id}/get-agent-run")
async def get_agent_run_complete(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_complete_data(rid))
    return {"success": True, "data": result}


@router.get("/{run_id}")
async def get_agent_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRunDetailResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_detail_data(rid))
    return AgentRunDetailResponse(success=True, data={"run": result})


@router.get("/{run_id}/personas")
async def get_agent_run_personas(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PersonasResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_personas_data(rid))
    return PersonasResponse(success=True, data={"personas": result})


@router.get("/{run_id}/stats")
async def get_agent_run_statistics(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatisticsResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_statistics_data(rid))
    return StatisticsResponse(success=True, data={"stats": result})


@router.get("/{run_id}/summary")
async def get_agent_run_summary(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SummaryResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_summary_data(rid))
    return SummaryResponse(success=True, data={"summary": result})


@router.get("/{run_id}/tasks")
async def get_agent_run_tasks(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TasksResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_tasks_data(rid))
    return TasksResponse(success=True, data=result)


@router.get("/{run_id}/timeline")
async def get_agent_run_timeline(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_timeline_data(rid))
    return TimelineResponse(success=True, data={"timeline": result})


@router.get("/{run_id}/logs")
async def get_agent_run_logs(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogsResponse:
    result = await _fetch_run_or_404(session, run_id, lambda s, rid: s.get_agent_run_logs_data(rid))
    return LogsResponse(success=True, data={"logs": result})


@router.get("/{run_id}/metrics")
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
    service = await _service(session)
    comparison = await service.compare_agent_runs_data(payload.runIds)
    return ComparisonResponse(success=True, data=comparison)

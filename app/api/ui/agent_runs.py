import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.get("")
@cache("agent_runs_list_v6", ttl=600)
async def list_agent_runs(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    roundId: Optional[str] = Query(None),
    validatorId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    includeUnfinished: bool = Query(False, description="Include runs from active/non-finalized rounds"),
    sortBy: str = Query("startTime"),
    sortOrder: str = Query("desc"),
) -> AgentRunsListResponse:
    service = await _service(session)
    data = await service.list_agent_runs_catalog(
        page=page,
        limit=limit,
        round_id=roundId,
        validator_id=validatorId,
        agent_id=agentId,
        query=query,
        status=status,
        start_date=startDate,
        end_date=endDate,
        include_unfinished=includeUnfinished,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return AgentRunsListResponse(success=True, data=data)


@router.get("/{run_id}/get-agent-run")
async def get_agent_run_complete(
    run_id: str,
    session: AsyncSession = Depends(get_session),
):
    service = await _service(session)
    try:
        result = await service.get_agent_run_complete_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return {"success": True, "data": result}


@router.get("/{run_id}")
async def get_agent_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentRunDetailResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_detail_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return AgentRunDetailResponse(success=True, data={"run": result})


@router.get("/{run_id}/personas")
async def get_agent_run_personas(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> PersonasResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_personas_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return PersonasResponse(success=True, data={"personas": result})


@router.get("/{run_id}/stats")
async def get_agent_run_statistics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> StatisticsResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_statistics_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return StatisticsResponse(success=True, data={"stats": result})


@router.get("/{run_id}/summary")
async def get_agent_run_summary(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> SummaryResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_summary_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return SummaryResponse(success=True, data={"summary": result})


@router.get("/{run_id}/tasks")
async def get_agent_run_tasks(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> TasksResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_tasks_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return TasksResponse(success=True, data=result)


@router.get("/{run_id}/timeline")
async def get_agent_run_timeline(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> TimelineResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_timeline_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return TimelineResponse(success=True, data={"timeline": result})


@router.get("/{run_id}/logs")
async def get_agent_run_logs(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> LogsResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_logs_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return LogsResponse(success=True, data={"logs": result})


@router.get("/{run_id}/metrics")
async def get_agent_run_metrics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> MetricsResponse:
    service = await _service(session)
    try:
        result = await service.get_agent_run_metrics_data(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return MetricsResponse(success=True, data={"metrics": result})


@router.post("/compare")
async def compare_agent_runs(payload: dict, session: AsyncSession = Depends(get_session)) -> ComparisonResponse:
    run_ids = payload.get("runIds", [])
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=400, detail="runIds must be a non-empty list")
    service = await _service(session)
    comparison = await service.compare_agent_runs_data(run_ids)
    return ComparisonResponse(success=True, data=comparison)

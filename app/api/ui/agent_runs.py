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
from app.services.ui.agent_runs_service import AgentRunsService
from app.services.redis_cache import cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-runs", tags=["agent-runs"])

NOT_FOUND_ERROR = {"message": "Agent run not found", "code": "AGENT_RUN_NOT_FOUND"}


async def _service(session: AsyncSession) -> AgentRunsService:
    return AgentRunsService(session)


@router.get("", response_model=AgentRunsListResponse)
@cache("agent_runs_list", ttl=600)  # Cache 10 minutes - pre-warmed by background worker
async def list_agent_runs(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    roundId: Optional[int] = Query(None),
    validatorId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    startDate: Optional[datetime] = Query(None),
    endDate: Optional[datetime] = Query(None),
    sortBy: str = Query("startTime"),
    sortOrder: str = Query("desc"),
) -> AgentRunsListResponse:
    service = await _service(session)
    data = await service.list_agent_runs(
        page=page,
        limit=limit,
        round_number=roundId,
        validator_id=validatorId,
        agent_id=agentId,
        query=query,
        status=status,
        start_date=startDate,
        end_date=endDate,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return AgentRunsListResponse(success=True, data=data)


@router.get("/{run_id}", response_model=AgentRunDetailResponse)
async def get_agent_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentRunDetailResponse:
    service = await _service(session)
    result = await service.get_agent_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return AgentRunDetailResponse(success=True, data={"run": result})


@router.get("/{run_id}/personas", response_model=PersonasResponse)
async def get_agent_run_personas(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> PersonasResponse:
    service = await _service(session)
    result = await service.get_personas(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return PersonasResponse(success=True, data={"personas": result})


@router.get("/{run_id}/stats", response_model=StatisticsResponse)
async def get_agent_run_statistics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> StatisticsResponse:
    service = await _service(session)
    result = await service.get_statistics(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return StatisticsResponse(success=True, data={"stats": result})


@router.get("/{run_id}/summary", response_model=SummaryResponse)
async def get_agent_run_summary(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> SummaryResponse:
    service = await _service(session)
    result = await service.get_summary(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return SummaryResponse(success=True, data={"summary": result})


@router.get("/{run_id}/tasks", response_model=TasksResponse)
async def get_agent_run_tasks(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> TasksResponse:
    service = await _service(session)
    result = await service.get_tasks(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return TasksResponse(success=True, data={"tasks": result})


@router.get("/{run_id}/timeline", response_model=TimelineResponse)
async def get_agent_run_timeline(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> TimelineResponse:
    service = await _service(session)
    result = await service.get_timeline(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return TimelineResponse(success=True, data={"timeline": result})


@router.get("/{run_id}/logs", response_model=LogsResponse)
async def get_agent_run_logs(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> LogsResponse:
    service = await _service(session)
    result = await service.get_logs(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return LogsResponse(success=True, data={"logs": result})


@router.get("/{run_id}/metrics", response_model=MetricsResponse)
async def get_agent_run_metrics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> MetricsResponse:
    service = await _service(session)
    result = await service.get_metrics(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_ERROR)
    return MetricsResponse(success=True, data={"metrics": result})


@router.post("/compare", response_model=ComparisonResponse)
async def compare_agent_runs(payload: dict, session: AsyncSession = Depends(get_session)) -> ComparisonResponse:
    run_ids = payload.get("runIds", [])
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=400, detail="runIds must be a non-empty list")
    service = await _service(session)
    comparison = await service.compare_runs(run_ids)
    return ComparisonResponse(success=True, data=comparison)

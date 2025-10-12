"""
Agent Runs API endpoints for the AutoPPIA Bittensor Dashboard.
These endpoints match the specifications provided by the frontend team.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Path
from datetime import datetime, timezone
import logging

from app.models.agent_runs import (
    AgentRunDetailResponse, PersonasResponse, StatisticsResponse, SummaryResponse,
    TasksResponse, AgentRunsListResponse, ComparisonRequest, ComparisonResponse,
    TimelineResponse, LogsResponse, MetricsResponse
)
from app.services.agent_runs_service import AgentRunsService
from app.services.cache import cached, CACHE_TTL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agent-runs", tags=["agent-runs"])

# Initialize service
agent_runs_service = AgentRunsService()


@router.get("")
async def get_agent_runs_list(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    roundId: Optional[int] = Query(None, description="Filter by round ID"),
    validatorId: Optional[str] = Query(None, description="Filter by validator ID"),
    agentId: Optional[str] = Query(None, description="Filter by agent ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """
    Get list of agent runs with filtering and pagination.
    """
    try:
        logger.info(f"Fetching agent runs list with page={page}, limit={limit}")
        
        # Mock data for now - in production this would come from the service
        mock_runs = [
            {
                "runId": "run-001",
                "agentId": "anthropic-cua",
                "roundId": 20,
                "validatorId": "validator_1",
                "status": "completed",
                "startTime": "2025-10-10T22:30:42.132661+00:00",
                "endTime": "2025-10-10T23:14:39.132661+00:00",
                "totalTasks": 11,
                "completedTasks": 9,
                "averageScore": 0.84,
                "successRate": 81.8
            },
            {
                "runId": "run-002", 
                "agentId": "autoppia-bittensor",
                "roundId": 20,
                "validatorId": "validator_2",
                "status": "completed",
                "startTime": "2025-10-10T22:30:42.132661+00:00",
                "endTime": "2025-10-10T23:14:39.132661+00:00",
                "totalTasks": 11,
                "completedTasks": 8,
                "averageScore": 0.87,
                "successRate": 72.7
            }
        ]
        
        # Apply filters
        filtered_runs = mock_runs
        if roundId:
            filtered_runs = [r for r in filtered_runs if r["roundId"] == roundId]
        if validatorId:
            filtered_runs = [r for r in filtered_runs if r["validatorId"] == validatorId]
        if agentId:
            filtered_runs = [r for r in filtered_runs if r["agentId"] == agentId]
        if status:
            filtered_runs = [r for r in filtered_runs if r["status"] == status]
        
        # Apply pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_runs = filtered_runs[start_idx:end_idx]
        
        return {
            "success": True,
            "data": {
                "runs": paginated_runs,
                "total": len(filtered_runs),
                "page": page,
                "limit": limit
            }
        }
        
    except Exception as e:
        logger.error(f"Error fetching agent runs list: {e}")
        return {
            "success": False,
            "error": f"Failed to fetch agent runs: {str(e)}",
            "code": "AGENT_RUNS_LIST_FETCH_ERROR"
        }


@router.get("/{runId}", response_model=AgentRunDetailResponse)
@cached("agent_run_detail", CACHE_TTL.get("agent_run_detail", 60))
async def get_agent_run_details(
    runId: str = Path(..., description="The unique identifier of the agent run"),
    includeTasks: bool = Query(False, description="Include task details"),
    includeStats: bool = Query(False, description="Include statistics"),
    includeSummary: bool = Query(False, description="Include summary"),
    includePersonas: bool = Query(False, description="Include personas data")
):
    """
    Get comprehensive details for a specific agent run.
    """
    try:
        logger.info(f"Fetching agent run details for {runId}")
        
        agent_run = await agent_runs_service.get_agent_run_details(
            runId, includeTasks, includeStats, includeSummary, includePersonas
        )
        
        if not agent_run:
            return AgentRunDetailResponse(
                success=False,
                error=f"Agent run with ID '{runId}' not found",
                code="AGENT_RUN_NOT_FOUND"
            )
        
        return AgentRunDetailResponse(
            success=True,
            data={"run": agent_run}
        )
        
    except Exception as e:
        logger.error(f"Error fetching agent run details: {e}")
        return AgentRunDetailResponse(
            success=False,
            error=f"Failed to fetch agent run details: {str(e)}",
            code="AGENT_RUN_DETAIL_FETCH_ERROR"
        )


@router.get("/{runId}/personas", response_model=PersonasResponse)
@cached("agent_run_personas", CACHE_TTL.get("agent_run_personas", 300))
async def get_agent_run_personas(
    runId: str = Path(..., description="The unique identifier of the agent run")
):
    """
    Get personas data (round, validator, agent information) for an agent run.
    """
    try:
        logger.info(f"Fetching personas for agent run {runId}")
        
        personas = await agent_runs_service.get_agent_run_personas(runId)
        
        if not personas:
            return PersonasResponse(
                success=False,
                error=f"Agent run with ID '{runId}' not found",
                code="AGENT_RUN_NOT_FOUND"
            )
        
        return PersonasResponse(
            success=True,
            data={"personas": personas}
        )
        
    except Exception as e:
        logger.error(f"Error fetching personas: {e}")
        return PersonasResponse(
            success=False,
            error=f"Failed to fetch personas: {str(e)}",
            code="PERSONAS_FETCH_ERROR"
        )


@router.get("/{runId}/stats", response_model=StatisticsResponse)
@cached("agent_run_stats", CACHE_TTL.get("agent_run_stats", 120))
async def get_agent_run_statistics(
    runId: str = Path(..., description="The unique identifier of the agent run")
):
    """
    Get detailed statistics for an agent run.
    """
    try:
        logger.info(f"Fetching statistics for agent run {runId}")
        
        statistics = await agent_runs_service.get_agent_run_statistics(runId)
        
        if not statistics:
            return StatisticsResponse(
                success=False,
                error=f"Agent run with ID '{runId}' not found",
                code="AGENT_RUN_NOT_FOUND"
            )
        
        return StatisticsResponse(
            success=True,
            data={"stats": statistics}
        )
        
    except Exception as e:
        logger.error(f"Error fetching statistics: {e}")
        return StatisticsResponse(
            success=False,
            error=f"Failed to fetch statistics: {str(e)}",
            code="STATISTICS_FETCH_ERROR"
        )


@router.get("/{runId}/summary", response_model=SummaryResponse)
@cached("agent_run_summary", CACHE_TTL.get("agent_run_summary", 60))
async def get_agent_run_summary(
    runId: str = Path(..., description="The unique identifier of the agent run")
):
    """
    Get summary information for an agent run.
    """
    try:
        logger.info(f"Fetching summary for agent run {runId}")
        
        summary = await agent_runs_service.get_agent_run_summary(runId)
        
        if not summary:
            return SummaryResponse(
                success=False,
                error=f"Agent run with ID '{runId}' not found",
                code="AGENT_RUN_NOT_FOUND"
            )
        
        return SummaryResponse(
            success=True,
            data={"summary": summary}
        )
        
    except Exception as e:
        logger.error(f"Error fetching summary: {e}")
        return SummaryResponse(
            success=False,
            error=f"Failed to fetch summary: {str(e)}",
            code="SUMMARY_FETCH_ERROR"
        )


@router.get("/{runId}/tasks", response_model=TasksResponse)
@cached("agent_run_tasks", CACHE_TTL.get("agent_run_tasks", 30))
async def get_agent_run_tasks(
    runId: str = Path(..., description="The unique identifier of the agent run"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    website: Optional[str] = Query(None, description="Filter by website"),
    useCase: Optional[str] = Query(None, description="Filter by use case"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """
    Get tasks for an agent run with pagination and filtering.
    """
    try:
        logger.info(f"Fetching tasks for agent run {runId}")
        
        tasks_data = await agent_runs_service.get_agent_run_tasks(
            runId, page, limit, website, useCase, status, sortBy, sortOrder
        )
        
        return TasksResponse(
            success=True,
            data=tasks_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return TasksResponse(
            success=False,
            error=f"Failed to fetch tasks: {str(e)}",
            code="TASKS_FETCH_ERROR"
        )


@router.get("/agents/{agentId}/runs", response_model=AgentRunsListResponse)
@cached("agent_runs_by_agent", CACHE_TTL.get("agent_runs_by_agent", 60))
async def get_agent_runs_by_agent(
    agentId: str = Path(..., description="The unique identifier of the agent"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    roundId: Optional[int] = Query(None, description="Filter by round ID"),
    validatorId: Optional[str] = Query(None, description="Filter by validator ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """
    Get all agent runs for a specific agent.
    """
    try:
        logger.info(f"Fetching agent runs for agent {agentId}")
        
        runs_data = await agent_runs_service.get_agent_runs_by_agent(
            agentId, page, limit, roundId, validatorId, status, sortBy, sortOrder
        )
        
        return AgentRunsListResponse(
            success=True,
            data=runs_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching agent runs by agent: {e}")
        return AgentRunsListResponse(
            success=False,
            error=f"Failed to fetch agent runs: {str(e)}",
            code="AGENT_RUNS_FETCH_ERROR"
        )


@router.get("/rounds/{roundId}/agent-runs", response_model=AgentRunsListResponse)
@cached("agent_runs_by_round", CACHE_TTL.get("agent_runs_by_round", 60))
async def get_agent_runs_by_round(
    roundId: int = Path(..., description="The unique identifier of the round"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    validatorId: Optional[str] = Query(None, description="Filter by validator ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """
    Get all agent runs for a specific round.
    """
    try:
        logger.info(f"Fetching agent runs for round {roundId}")
        
        runs_data = await agent_runs_service.get_agent_runs_by_round(
            roundId, page, limit, validatorId, status, sortBy, sortOrder
        )
        
        return AgentRunsListResponse(
            success=True,
            data=runs_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching agent runs by round: {e}")
        return AgentRunsListResponse(
            success=False,
            error=f"Failed to fetch agent runs: {str(e)}",
            code="AGENT_RUNS_FETCH_ERROR"
        )


@router.get("/validators/{validatorId}/agent-runs", response_model=AgentRunsListResponse)
@cached("agent_runs_by_validator", CACHE_TTL.get("agent_runs_by_validator", 60))
async def get_agent_runs_by_validator(
    validatorId: str = Path(..., description="The unique identifier of the validator"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    roundId: Optional[int] = Query(None, description="Filter by round ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("startTime", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort order")
):
    """
    Get all agent runs for a specific validator.
    """
    try:
        logger.info(f"Fetching agent runs for validator {validatorId}")
        
        runs_data = await agent_runs_service.get_agent_runs_by_validator(
            validatorId, page, limit, roundId, status, sortBy, sortOrder
        )
        
        return AgentRunsListResponse(
            success=True,
            data=runs_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching agent runs by validator: {e}")
        return AgentRunsListResponse(
            success=False,
            error=f"Failed to fetch agent runs: {str(e)}",
            code="AGENT_RUNS_FETCH_ERROR"
        )


@router.post("/compare", response_model=ComparisonResponse)
async def compare_agent_runs(request: ComparisonRequest):
    """
    Compare multiple agent runs.
    """
    try:
        logger.info(f"Comparing agent runs: {request.runIds}")
        
        comparison_data = await agent_runs_service.compare_agent_runs(request.runIds)
        
        return ComparisonResponse(
            success=True,
            data=comparison_data
        )
        
    except Exception as e:
        logger.error(f"Error comparing agent runs: {e}")
        return ComparisonResponse(
            success=False,
            error=f"Failed to compare agent runs: {str(e)}",
            code="COMPARISON_ERROR"
        )


@router.get("/{runId}/timeline", response_model=TimelineResponse)
@cached("agent_run_timeline", CACHE_TTL.get("agent_run_timeline", 0))  # No caching for timeline
async def get_agent_run_timeline(
    runId: str = Path(..., description="The unique identifier of the agent run")
):
    """
    Get timeline of events for an agent run.
    """
    try:
        logger.info(f"Fetching timeline for agent run {runId}")
        
        events = await agent_runs_service.get_agent_run_timeline(runId)
        
        return TimelineResponse(
            success=True,
            data={"events": events}
        )
        
    except Exception as e:
        logger.error(f"Error fetching timeline: {e}")
        return TimelineResponse(
            success=False,
            error=f"Failed to fetch timeline: {str(e)}",
            code="TIMELINE_FETCH_ERROR"
        )


@router.get("/{runId}/logs", response_model=LogsResponse)
@cached("agent_run_logs", CACHE_TTL.get("agent_run_logs", 0))  # No caching for logs
async def get_agent_run_logs(
    runId: str = Path(..., description="The unique identifier of the agent run"),
    level: Optional[str] = Query(None, description="Log level"),
    limit: int = Query(100, ge=1, le=1000, description="Number of logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip")
):
    """
    Get logs for an agent run.
    """
    try:
        logger.info(f"Fetching logs for agent run {runId}")
        
        logs_data = await agent_runs_service.get_agent_run_logs(runId, level, limit, offset)
        
        return LogsResponse(
            success=True,
            data=logs_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return LogsResponse(
            success=False,
            error=f"Failed to fetch logs: {str(e)}",
            code="LOGS_FETCH_ERROR"
        )


@router.get("/{runId}/metrics", response_model=MetricsResponse)
@cached("agent_run_metrics", CACHE_TTL.get("agent_run_metrics", 30))
async def get_agent_run_metrics(
    runId: str = Path(..., description="The unique identifier of the agent run")
):
    """
    Get performance metrics for an agent run.
    """
    try:
        logger.info(f"Fetching metrics for agent run {runId}")
        
        metrics = await agent_runs_service.get_agent_run_metrics(runId)
        
        if not metrics:
            return MetricsResponse(
                success=False,
                error=f"Agent run with ID '{runId}' not found",
                code="AGENT_RUN_NOT_FOUND"
            )
        
        return MetricsResponse(
            success=True,
            data={"metrics": metrics}
        )
        
    except Exception as e:
        logger.error(f"Error fetching metrics: {e}")
        return MetricsResponse(
            success=False,
            error=f"Failed to fetch metrics: {str(e)}",
            code="METRICS_FETCH_ERROR"
        )

from fastapi import APIRouter, HTTPException, Query, Depends, status
from typing import Optional, List
from datetime import datetime

from app.models.ui.agents import (
    AgentListQuery, AgentRunsQuery, 
    AgentActivityQuery, AllAgentActivityQuery, AgentCompareRequest,
    AgentListResponse, AgentDetailResponse,
    AgentRunsResponse, AgentRunDetailResponse, AgentActivityResponse,
    AgentStatisticsResponse, AgentComparisonResponse, APIResponse,
    TimeRange, Granularity, AgentType, AgentStatus, RunStatus, ActivityType
)
from app.services.ui.agents_service import AgentsService

# Create router
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# Initialize service
agents_service = AgentsService()


# --- Helper Functions ---
def create_api_response(data: any, success: bool = True, message: str = None, error: str = None) -> APIResponse:
    """Create standardized API response."""
    return APIResponse(
        data=data,
        success=success,
        message=message,
        error=error
    )


def parse_query_params(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    type: Optional[AgentType] = Query(None, description="Filter by agent type"),
    status: Optional[AgentStatus] = Query(None, description="Filter by status"),
    sortBy: str = Query("averageScore", description="Sort field"),
    sortOrder: str = Query("desc", description="Sort order"),
    search: Optional[str] = Query(None, description="Search term")
) -> AgentListQuery:
    """Parse agent list query parameters."""
    return AgentListQuery(
        page=page,
        limit=limit,
        type=type,
        status=status,
        sortBy=sortBy,
        sortOrder=sortOrder,
        search=search
    )


def parse_runs_query_params(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    roundId: Optional[int] = Query(None, description="Filter by round ID"),
    validatorId: Optional[str] = Query(None, description="Filter by validator ID"),
    status: Optional[RunStatus] = Query(None, description="Filter by status"),
    sortBy: str = Query("startTime", description="Sort field"),
    sortOrder: str = Query("desc", description="Sort order"),
    startDate: Optional[datetime] = Query(None, description="Start date filter"),
    endDate: Optional[datetime] = Query(None, description="End date filter")
) -> AgentRunsQuery:
    """Parse runs query parameters."""
    return AgentRunsQuery(
        page=page,
        limit=limit,
        roundId=roundId,
        validatorId=validatorId,
        status=status,
        sortBy=sortBy,
        sortOrder=sortOrder,
        startDate=startDate,
        endDate=endDate
    )


def parse_activity_query_params(
    limit: int = Query(20, ge=1, le=100, description="Number of activities"),
    offset: int = Query(0, ge=0, description="Number of activities to skip"),
    type: Optional[ActivityType] = Query(None, description="Filter by activity type"),
    since: Optional[datetime] = Query(None, description="Filter activities after timestamp")
) -> AgentActivityQuery:
    """Parse activity query parameters."""
    return AgentActivityQuery(
        limit=limit,
        offset=offset,
        type=type,
        since=since
    )


def parse_all_activity_query_params(
    limit: int = Query(20, ge=1, le=100, description="Number of activities"),
    offset: int = Query(0, ge=0, description="Number of activities to skip"),
    type: Optional[ActivityType] = Query(None, description="Filter by activity type"),
    since: Optional[datetime] = Query(None, description="Filter activities after timestamp"),
    agentId: Optional[str] = Query(None, description="Filter by specific agent ID")
) -> AllAgentActivityQuery:
    """Parse all activity query parameters."""
    return AllAgentActivityQuery(
        limit=limit,
        offset=offset,
        type=type,
        since=since,
        agentId=agentId
    )


# --- Endpoints ---

@router.get("", response_model=APIResponse)
async def get_all_agents(
    query: AgentListQuery = Depends(parse_query_params)
):
    """
    Get all agents with pagination, filtering, and sorting.
    
    - **page**: Page number for pagination (default: 1)
    - **limit**: Number of items per page (default: 20, max: 100)
    - **type**: Filter by agent type (autoppia, openai, anthropic, browser-use, custom)
    - **status**: Filter by status (active, inactive, maintenance)
    - **sortBy**: Sort field (name, currentScore, totalRuns, lastSeen)
    - **sortOrder**: Sort order (asc, desc)
    - **search**: Search by agent name or description
    """
    try:
        agents, total = agents_service.get_agents(query)
        
        response_data = AgentListResponse(
            agents=agents,
            total=total,
            page=query.page,
            limit=query.limit
        )
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved {len(agents)} agents"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agents: {str(e)}"
        )


@router.post("/compare", response_model=APIResponse)
async def compare_agents(request: AgentCompareRequest):
    """
    Compare multiple agents across various metrics.
    
    - **agentIds**: List of agent IDs to compare
    - **timeRange**: Time range for comparison (optional)
    - **startDate**: Start date for comparison (optional)
    - **endDate**: End date for comparison (optional)
    - **metrics**: List of metrics to compare (optional)
    """
    try:
        if not request.agentIds or len(request.agentIds) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least 2 agent IDs are required for comparison"
            )
        
        if len(request.agentIds) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum 10 agents can be compared at once"
            )
        
        comparison = agents_service.compare_agents(request)
        
        if not comparison:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more agent IDs not found"
            )
        
        return create_api_response(
            data=comparison.dict(),
            success=True,
            message=f"Compared {len(request.agentIds)} agents"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compare agents: {str(e)}"
        )


@router.get("/statistics", response_model=APIResponse)
async def get_agent_statistics():
    """
    Get overall statistics for all agents.
    """
    try:
        statistics = agents_service.get_agent_statistics()
        
        response_data = AgentStatisticsResponse(statistics=statistics)
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message="Retrieved agent statistics"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent statistics: {str(e)}"
        )


@router.get("/activity", response_model=APIResponse)
async def get_all_agent_activity(
    query: AllAgentActivityQuery = Depends(parse_all_activity_query_params)
):
    """
    Get activity across all agents.
    
    - **limit**: Number of activities to return (default: 20, max: 100)
    - **offset**: Number of activities to skip (default: 0)
    - **type**: Filter by activity type
    - **since**: Return activities after this timestamp
    - **agentId**: Filter by specific agent ID
    """
    try:
        activities, total = agents_service.get_all_agent_activity(query)
        
        response_data = AgentActivityResponse(
            activities=activities,
            total=total
        )
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved {len(activities)} activities"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent activity: {str(e)}"
        )


@router.get("/{agent_id}", response_model=APIResponse)
async def get_agent_details(agent_id: str):
    """
    Get detailed information for a specific agent with score vs round data points.
    
    - **agent_id**: Agent ID
    """
    try:
        agent = agents_service.get_agent_by_id(agent_id)
        
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id}' not found"
            )
        
        # Get score vs round data points
        score_round_data = agents_service.get_agent_score_round_data(agent_id, limit=50)
        
        response_data = AgentDetailResponse(
            agent=agent,
            scoreRoundData=score_round_data
        )
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved agent details with score vs round data for {agent_id}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent details: {str(e)}"
        )


@router.get("/{agent_id}/runs", response_model=APIResponse)
async def get_agent_runs(
    agent_id: str,
    query: AgentRunsQuery = Depends(parse_runs_query_params)
):
    """
    Get a paginated list of runs for a specific agent.
    
    - **agent_id**: Agent ID
    - **page**: Page number (default: 1)
    - **limit**: Items per page (default: 20, max: 100)
    - **roundId**: Filter by round ID
    - **validatorId**: Filter by validator ID
    - **status**: Filter by status (running, completed, failed, timeout)
    - **sortBy**: Sort field (startTime, score, duration, ranking)
    - **sortOrder**: Sort order (asc, desc)
    - **startDate**: Filter runs after this date
    - **endDate**: Filter runs before this date
    """
    try:
        # Check if agent exists
        agent = agents_service.get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id}' not found"
            )
        
        runs, total = agents_service.get_agent_runs(agent_id, query)
        
        response_data = AgentRunsResponse(
            runs=runs,
            total=total,
            page=query.page,
            limit=query.limit
        )
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved {len(runs)} runs for agent {agent_id}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent runs: {str(e)}"
        )


@router.get("/{agent_id}/runs/{run_id}", response_model=APIResponse)
async def get_agent_run_details(agent_id: str, run_id: str):
    """
    Get detailed information for a specific agent run.
    
    - **agent_id**: Agent ID
    - **run_id**: Run ID
    """
    try:
        # Check if agent exists
        agent = agents_service.get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id}' not found"
            )
        
        run = agents_service.get_agent_run_by_id(agent_id, run_id)
        
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run with ID '{run_id}' not found for agent '{agent_id}'"
            )
        
        response_data = AgentRunDetailResponse(run=run)
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved run details for {run_id}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve run details: {str(e)}"
        )


@router.get("/{agent_id}/activity", response_model=APIResponse)
async def get_agent_activity(
    agent_id: str,
    query: AgentActivityQuery = Depends(parse_activity_query_params)
):
    """
    Get recent activity for a specific agent.
    
    - **agent_id**: Agent ID
    - **limit**: Number of activities to return (default: 20, max: 100)
    - **offset**: Number of activities to skip (default: 0)
    - **type**: Filter by activity type
    - **since**: Return activities after this timestamp
    """
    try:
        # Check if agent exists
        agent = agents_service.get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent with ID '{agent_id}' not found"
            )
        
        activities, total = agents_service.get_agent_activity(agent_id, query)
        
        response_data = AgentActivityResponse(
            activities=activities,
            total=total
        )
        
        return create_api_response(
            data=response_data.dict(),
            success=True,
            message=f"Retrieved {len(activities)} activities for agent {agent_id}"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve agent activity: {str(e)}"
        )

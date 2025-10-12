from fastapi import APIRouter, HTTPException, Query, Depends, status
from typing import Optional, List
from datetime import datetime

from app.models.miners import (
    MinerListQuery, MinerPerformanceQuery, MinerRunsQuery,
    MinerListResponse, MinerPerformanceResponse, MinerRunsResponse, APIResponse,
    TimeRange, Granularity, MinerStatus, RunStatus
)
from app.services.miners_service import MinersService

# Create router
router = APIRouter(prefix="/api/v1/miners", tags=["miners"])

# Initialize service
miners_service = MinersService()


# --- Helper Functions ---
def create_api_response(data: any, success: bool = True) -> APIResponse:
    """Create standardized API response."""
    return APIResponse(
        success=success,
        data=data
    )


def create_error_response(error_code: str, message: str, details: dict = None) -> APIResponse:
    """Create error response."""
    from app.models.miners import ErrorDetail
    return APIResponse(
        success=False,
        error=ErrorDetail(
            code=error_code,
            message=message,
            details=details or {}
        )
    )


def parse_query_params(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    isSota: Optional[bool] = Query(None, description="Filter by SOTA status"),
    status: Optional[MinerStatus] = Query(None, description="Filter by status"),
    sortBy: str = Query("averageScore", description="Sort field"),
    sortOrder: str = Query("desc", description="Sort order"),
    search: Optional[str] = Query(None, description="Search term")
) -> MinerListQuery:
    """Parse miner list query parameters."""
    return MinerListQuery(
        page=page,
        limit=limit,
        isSota=isSota,
        status=status,
        sortBy=sortBy,
        sortOrder=sortOrder,
        search=search
    )


def parse_performance_query_params(
    timeRange: TimeRange = Query(TimeRange.SEVEN_DAYS, description="Time range"),
    granularity: Granularity = Query(Granularity.DAY, description="Data granularity")
) -> MinerPerformanceQuery:
    """Parse miner performance query parameters."""
    return MinerPerformanceQuery(
        timeRange=timeRange,
        granularity=granularity
    )


def parse_runs_query_params(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    roundId: Optional[int] = Query(None, description="Filter by round ID"),
    status: Optional[RunStatus] = Query(None, description="Filter by run status"),
    sortBy: str = Query("startTime", description="Sort field"),
    sortOrder: str = Query("desc", description="Sort order")
) -> MinerRunsQuery:
    """Parse miner runs query parameters."""
    return MinerRunsQuery(
        page=page,
        limit=limit,
        roundId=roundId,
        status=status,
        sortBy=sortBy,
        sortOrder=sortOrder
    )




# --- Endpoints ---

@router.get("", response_model=APIResponse)
async def get_all_miners(
    query: MinerListQuery = Depends(parse_query_params)
):
    """
    Get all miners with pagination, filtering, and sorting.
    
    - **page**: Page number for pagination (default: 1)
    - **limit**: Number of items per page (default: 50, max: 100)
    - **isSota**: Filter by SOTA status (true for SOTA agents only, false for regular miners only)
    - **status**: Filter by status (active, inactive, maintenance)
    - **sortBy**: Sort field (name, uid, averageScore, successRate, totalRuns, lastSeen)
    - **sortOrder**: Sort order (asc, desc)
    - **search**: Search by miner name, UID, or hotkey
    """
    try:
        miners, total = miners_service.get_miners(query)
        
        # Calculate pagination
        total_pages = (total + query.limit - 1) // query.limit
        
        response_data = MinerListResponse(
            miners=miners,
            pagination={
                "page": query.page,
                "limit": query.limit,
                "total": total,
                "totalPages": total_pages
            }
        )
        
        return create_api_response(data=response_data.dict())
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_response("INTERNAL_SERVER_ERROR", f"Failed to retrieve miners: {str(e)}").dict()
        )


@router.get("/{uid}", response_model=APIResponse)
async def get_miner_details(uid: int):
    """
    Get detailed information for a specific miner.
    
    - **uid**: Miner UID
    """
    try:
        miner = miners_service.get_miner_by_uid(uid)
        
        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=create_error_response("MINER_NOT_FOUND", f"Miner with UID {uid} not found").dict()
            )
        
        return create_api_response(data=miner.dict())
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_response("INTERNAL_SERVER_ERROR", f"Failed to retrieve miner details: {str(e)}").dict()
        )


@router.get("/{uid}/performance", response_model=APIResponse)
async def get_miner_performance(
    uid: int,
    query: MinerPerformanceQuery = Depends(parse_performance_query_params)
):
    """
    Get performance trends for a specific miner over a specified time range.
    
    - **uid**: Miner UID
    - **timeRange**: Time range (7d, 30d, 90d)
    - **granularity**: Data granularity (hour, day)
    """
    try:
        # Check if miner exists
        miner = miners_service.get_miner_by_uid(uid)
        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=create_error_response("MINER_NOT_FOUND", f"Miner with UID {uid} not found").dict()
            )
        
        performance_trend = miners_service.get_miner_performance(uid, query)
        
        response_data = MinerPerformanceResponse(performanceTrend=performance_trend)
        
        return create_api_response(data=response_data.dict())
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_response("INTERNAL_SERVER_ERROR", f"Failed to retrieve miner performance: {str(e)}").dict()
        )


@router.get("/{uid}/runs", response_model=APIResponse)
async def get_miner_runs(
    uid: int,
    query: MinerRunsQuery = Depends(parse_runs_query_params)
):
    """
    Get a paginated list of runs for a specific miner.
    
    - **uid**: Miner UID
    - **page**: Page number
    - **limit**: Items per page
    - **roundId**: Filter by round ID
    - **status**: Filter by run status
    - **sortBy**: Sort field (startTime, score, duration)
    - **sortOrder**: Sort order (asc, desc)
    """
    try:
        # Check if miner exists
        miner = miners_service.get_miner_by_uid(uid)
        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=create_error_response("MINER_NOT_FOUND", f"Miner with UID {uid} not found").dict()
            )
        
        runs, total = miners_service.get_miner_runs(uid, query)
        
        # Calculate pagination
        total_pages = (total + query.limit - 1) // query.limit
        
        response_data = MinerRunsResponse(
            runs=runs,
            pagination={
                "page": query.page,
                "limit": query.limit,
                "total": total,
                "totalPages": total_pages
            }
        )
        
        return create_api_response(data=response_data.dict())
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_response("INTERNAL_SERVER_ERROR", f"Failed to retrieve miner runs: {str(e)}").dict()
        )


@router.get("/{uid}/runs/{run_id}", response_model=APIResponse)
async def get_miner_run_details(uid: int, run_id: str):
    """
    Get detailed information for a specific miner run.
    
    - **uid**: Miner UID
    - **run_id**: Run ID
    """
    try:
        # Check if miner exists
        miner = miners_service.get_miner_by_uid(uid)
        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=create_error_response("MINER_NOT_FOUND", f"Miner with UID {uid} not found").dict()
            )
        
        run = miners_service.get_miner_run_by_id(uid, run_id)
        
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=create_error_response("RUN_NOT_FOUND", f"Run {run_id} not found for miner {uid}").dict()
            )
        
        return create_api_response(data=run.dict())
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_response("INTERNAL_SERVER_ERROR", f"Failed to retrieve miner run details: {str(e)}").dict()
        )




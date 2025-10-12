from fastapi import APIRouter, HTTPException, Query, status
from typing import Optional
from app.models.miner_list import MinerListResponse, MinerDetailResponse, MinerListItem, MinerDetail
from app.services.miners_service import MinersService

# Create router
router = APIRouter(prefix="/api/v1/miner-list", tags=["miner-list"])

# Initialize service
miners_service = MinersService()


@router.get("/", response_model=MinerListResponse)
async def get_miner_list(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    isSota: Optional[bool] = Query(None, description="Filter by SOTA status"),
    search: Optional[str] = Query(None, description="Search by name or UID")
):
    """
    Get a minimal list of miners for efficient listing.
    
    Returns only essential fields: UID, name, ranking, isSota, imageUrl
    """
    try:
        # Get all miners
        all_miners = miners_service._mock_miners.copy()
        
        # Apply filters
        if isSota is not None:
            all_miners = [m for m in all_miners if m.isSota == isSota]
        
        if search:
            search_lower = search.lower()
            all_miners = [m for m in all_miners if 
                         search_lower in m.name.lower() or 
                         search_lower in str(m.uid)]
        
        # Sort by average score (descending) to determine ranking
        all_miners.sort(key=lambda x: x.averageScore, reverse=True)
        
        # Apply pagination
        total = len(all_miners)
        start = (page - 1) * limit
        end = start + limit
        paginated_miners = all_miners[start:end]
        
        # Convert to minimal format with ranking
        miner_items = []
        for i, miner in enumerate(paginated_miners, start + 1):
            miner_item = MinerListItem(
                uid=miner.uid,
                name=miner.name,
                ranking=i,
                score=miner.averageScore,
                isSota=miner.isSota,
                imageUrl=miner.imageUrl
            )
            miner_items.append(miner_item)
        
        return MinerListResponse(
            miners=miner_items,
            total=total,
            page=page,
            limit=limit
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve miner list: {str(e)}"
        )


@router.get("/{uid}", response_model=MinerDetailResponse)
async def get_miner_detail(uid: int):
    """
    Get complete details for a specific miner by UID.
    
    Returns all available information about the miner.
    """
    try:
        # Find miner by UID
        miner = next((m for m in miners_service._mock_miners if m.uid == uid), None)
        
        if not miner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Miner with UID '{uid}' not found"
            )
        
        # Convert to detail format
        miner_detail = MinerDetail(
            uid=miner.uid,
            name=miner.name,
            hotkey=miner.hotkey,
            imageUrl=miner.imageUrl,
            githubUrl=miner.githubUrl,
            taostatsUrl=miner.taostatsUrl,
            isSota=miner.isSota,
            status=miner.status.value,
            description=miner.description,
            totalRuns=miner.totalRuns,
            successfulRuns=miner.successfulRuns,
            averageScore=miner.averageScore,
            bestScore=miner.bestScore,
            successRate=miner.successRate,
            averageDuration=miner.averageDuration,
            totalTasks=miner.totalTasks,
            completedTasks=miner.completedTasks,
            lastSeen=miner.lastSeen,
            createdAt=miner.createdAt,
            updatedAt=miner.updatedAt
        )
        
        return MinerDetailResponse(miner=miner_detail)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve miner details: {str(e)}"
        )

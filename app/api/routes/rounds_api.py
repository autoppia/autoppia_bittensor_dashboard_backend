from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Path
from app.models.rounds import (
    RoundsListResponse, RoundDetailResponse, RoundStatisticsResponse,
    RoundMinersResponse, RoundValidatorsResponse, RoundActivityResponse,
    RoundProgressResponse, RoundSummaryResponse, RoundComparisonRequest,
    RoundComparisonResponse, RoundTimelineResponse,
    RoundInfo, RoundStatistics, MinerPerformance, ValidatorPerformance,
    ActivityItem, RoundProgress, TimeRemaining, RoundSummary,
    TimelinePoint, RoundComparison, TopMiner
)
from app.services.data_builder import DataBuilder
from app.db.mock_mongo import get_mock_db
import logging
import random

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rounds", tags=["rounds"])

@router.get("", response_model=RoundsListResponse)
async def get_rounds(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("id", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort direction")
):
    """
    Returns list of all rounds with optional filtering and pagination.
    """
    try:
        logger.info(f"Fetching rounds with page={page}, limit={limit}, status={status}")

        # Handle FastAPI Query object quirk
        if hasattr(status, 'annotation'):
            status = None
        if hasattr(page, 'annotation'):
            page = 1
        if hasattr(limit, 'annotation'):
            limit = 20

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get all rounds
        rounds_data = data_builder.get_rounds()
        
        # Filter by status if provided
        if status:
            rounds_data = [r for r in rounds_data if r.status == status]
        
        # Sort rounds
        if sortBy == "id":
            rounds_data.sort(key=lambda x: x.round_id, reverse=(sortOrder == "desc"))
        elif sortBy == "startTime":
            rounds_data.sort(key=lambda x: x.started_at or 0, reverse=(sortOrder == "desc"))
        elif sortBy == "endTime":
            rounds_data.sort(key=lambda x: x.ended_at or 0, reverse=(sortOrder == "desc"))
        elif sortBy == "totalTasks":
            rounds_data.sort(key=lambda x: x.n_tasks, reverse=(sortOrder == "desc"))
        elif sortBy == "averageScore":
            rounds_data.sort(key=lambda x: x.average_score or 0, reverse=(sortOrder == "desc"))
        
        # Pagination
        total = len(rounds_data)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_rounds = rounds_data[start_idx:end_idx]
        
        # Convert to API format
        rounds = []
        for round_data in paginated_rounds:
            # Calculate progress (mock calculation)
            current_block = round_data.started_at + int((round_data.ended_at or round_data.started_at + 3600) - round_data.started_at) * 0.75
            start_block = round_data.started_at
            end_block = round_data.ended_at or round_data.started_at + 3600
            blocks_remaining = max(0, end_block - current_block)
            progress = min(1.0, (current_block - start_block) / (end_block - start_block))
            
            rounds.append({
                "id": int(round_data.round_id.split('_')[1]),
                "startBlock": start_block,
                "endBlock": end_block,
                "current": round_data.round_id == "round_020",  # Make round 20 current
                "startTime": datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
                "endTime": datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None,
                "status": "active" if round_data.round_id == "round_020" else "completed",
                "totalTasks": round_data.n_tasks,
                "completedTasks": int(round_data.n_tasks * 0.75),
                "averageScore": round_data.average_score or 0.0,
                "topScore": round_data.top_score or 0.0,
                "currentBlock": int(current_block),
                "blocksRemaining": int(blocks_remaining),
                "progress": progress
            })
        
        return RoundsListResponse(
            success=True,
            data={
                "rounds": rounds,
                "total": total,
                "page": page,
                "limit": limit
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching rounds: {e}")
        return RoundsListResponse(
            success=False,
            error=f"Failed to fetch rounds: {str(e)}",
            code="ROUNDS_FETCH_ERROR"
        )

@router.get("/{round_id}", response_model=RoundDetailResponse)
async def get_round_detail(round_id: int = Path(..., description="Round ID")):
    """
    Returns detailed information for a specific round.
    """
    try:
        logger.info(f"Fetching round detail for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Calculate progress
        current_block = round_data.started_at + int((round_data.ended_at or round_data.started_at + 3600) - round_data.started_at) * 0.75
        start_block = round_data.started_at
        end_block = round_data.ended_at or round_data.started_at + 3600
        blocks_remaining = max(0, end_block - current_block)
        progress = min(1.0, (current_block - start_block) / (end_block - start_block))
        
        round_info = {
            "id": round_id,
            "startBlock": start_block,
            "endBlock": end_block,
            "current": round_data.round_id == "round_020",
            "startTime": datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
            "endTime": datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None,
            "status": "active" if round_data.round_id == "round_020" else "completed",
            "totalTasks": round_data.n_tasks,
            "completedTasks": int(round_data.n_tasks * 0.75),
            "averageScore": round_data.average_score or 0.0,
            "topScore": round_data.top_score or 0.0,
            "currentBlock": int(current_block),
            "blocksRemaining": int(blocks_remaining),
            "progress": progress
        }
        
        return RoundDetailResponse(
            success=True,
            data={"round": round_info}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round detail: {e}")
        return RoundDetailResponse(
            success=False,
            error=f"Failed to fetch round detail: {str(e)}",
            code="ROUND_DETAIL_ERROR"
        )

@router.get("/current", response_model=RoundDetailResponse)
async def get_current_round():
    """
    Returns information about the current active round.
    """
    try:
        logger.info("Fetching current round")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get current round (round 20)
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == "round_020"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="No current round found")
        
        # Calculate progress
        current_block = round_data.started_at + int((round_data.ended_at or round_data.started_at + 3600) - round_data.started_at) * 0.75
        start_block = round_data.started_at
        end_block = round_data.ended_at or round_data.started_at + 3600
        blocks_remaining = max(0, end_block - current_block)
        progress = min(1.0, (current_block - start_block) / (end_block - start_block))
        
        round_info = {
            "id": 20,
            "startBlock": start_block,
            "endBlock": end_block,
            "current": True,
            "startTime": datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
            "endTime": datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None,
            "status": "active",
            "totalTasks": round_data.n_tasks,
            "completedTasks": int(round_data.n_tasks * 0.75),
            "averageScore": round_data.average_score or 0.0,
            "topScore": round_data.top_score or 0.0,
            "currentBlock": int(current_block),
            "blocksRemaining": int(blocks_remaining),
            "progress": progress
        }
        
        return RoundDetailResponse(
            success=True,
            data={"round": round_info}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching current round: {e}")
        return RoundDetailResponse(
            success=False,
            error=f"Failed to fetch current round: {str(e)}",
            code="CURRENT_ROUND_ERROR"
        )

@router.get("/{round_id}/statistics", response_model=RoundStatisticsResponse)
async def get_round_statistics(round_id: int = Path(..., description="Round ID")):
    """
    Returns comprehensive statistics for a specific round.
    """
    try:
        logger.info(f"Fetching statistics for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Mock statistics calculation
        total_miners = 50
        active_miners = 45
        total_tasks = round_data.n_tasks
        completed_tasks = int(total_tasks * 0.75)
        average_score = round_data.average_score or 0.0
        top_score = round_data.top_score or 0.0
        success_rate = 0.75
        average_duration = 32.5
        total_stake = 5000000
        total_emission = 250000
        
        statistics = {
            "roundId": round_id,
            "totalMiners": total_miners,
            "activeMiners": active_miners,
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "averageScore": average_score,
            "topScore": top_score,
            "successRate": success_rate,
            "averageDuration": average_duration,
            "totalStake": total_stake,
            "totalEmission": total_emission,
            "lastUpdated": datetime.now(timezone.utc).isoformat()
        }
        
        return RoundStatisticsResponse(
            success=True,
            data={"statistics": statistics}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round statistics: {e}")
        return RoundStatisticsResponse(
            success=False,
            error=f"Failed to fetch round statistics: {str(e)}",
            code="ROUND_STATISTICS_ERROR"
        )

@router.get("/{round_id}/miners", response_model=RoundMinersResponse)
async def get_round_miners(
    round_id: int = Path(..., description="Round ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    sortBy: Optional[str] = Query("score", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort direction"),
    success: Optional[bool] = Query(None, description="Filter by success status"),
    minScore: Optional[float] = Query(None, description="Minimum score filter"),
    maxScore: Optional[float] = Query(None, description="Maximum score filter")
):
    """
    Returns list of miners and their performance for a specific round.
    """
    try:
        logger.info(f"Fetching miners for round_id={round_id}")

        # Handle FastAPI Query object quirk
        if hasattr(page, 'annotation'):
            page = 1
        if hasattr(limit, 'annotation'):
            limit = 20
        if hasattr(success, 'annotation'):
            success = None
        if hasattr(minScore, 'annotation'):
            minScore = None
        if hasattr(maxScore, 'annotation'):
            maxScore = None

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Mock miners data
        miners = []
        for i in range(50):  # 50 miners
            score = round(random.uniform(0.3, 0.95), 3)
            miner_success = score > 0.5
            
            # Apply filters
            if success is not None and miner_success != success:
                continue
            if minScore is not None and score < minScore:
                continue
            if maxScore is not None and score > maxScore:
                continue
            
            miners.append({
                "uid": 42 + i,
                "hotkey": f"5GHrA5gqhWVm1Cp92jXaoH7caxtE7xsFHxJooL5h8aE9mdTe{i:02d}",
                "success": miner_success,
                "score": score,
                "duration": round(random.uniform(20, 60), 1),
                "ranking": i + 1,
                "tasksCompleted": random.randint(10, 20),
                "tasksTotal": 20,
                "stake": random.randint(50000, 200000),
                "emission": random.randint(2500, 10000),
                "lastSeen": datetime.now(timezone.utc).isoformat(),
                "validatorId": f"validator_{round_data.validator_info.uid}"
            })
        
        # Sort miners
        if sortBy == "score":
            miners.sort(key=lambda x: x["score"], reverse=(sortOrder == "desc"))
        elif sortBy == "duration":
            miners.sort(key=lambda x: x["duration"], reverse=(sortOrder == "desc"))
        elif sortBy == "ranking":
            miners.sort(key=lambda x: x["ranking"], reverse=(sortOrder == "desc"))
        elif sortBy == "uid":
            miners.sort(key=lambda x: x["uid"], reverse=(sortOrder == "desc"))
        
        # Pagination
        total = len(miners)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_miners = miners[start_idx:end_idx]
        
        return RoundMinersResponse(
            success=True,
            data={
                "miners": paginated_miners,
                "total": total,
                "page": page,
                "limit": limit
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round miners: {e}")
        return RoundMinersResponse(
            success=False,
            error=f"Failed to fetch round miners: {str(e)}",
            code="ROUND_MINERS_ERROR"
        )

@router.get("/{round_id}/miners/top", response_model=RoundMinersResponse)
async def get_round_top_miners(
    round_id: int = Path(..., description="Round ID"),
    limit: int = Query(10, ge=1, le=50, description="Number of top miners")
):
    """
    Returns top performing miners for a round.
    """
    try:
        logger.info(f"Fetching top miners for round_id={round_id}")

        # Handle FastAPI Query object quirk
        if hasattr(limit, 'annotation'):
            limit = 10

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Mock top miners data
        miners = []
        for i in range(limit):
            score = round(random.uniform(0.8, 0.95), 3)
            miners.append({
                "uid": 42 + i,
                "hotkey": f"5GHrA5gqhWVm1Cp92jXaoH7caxtE7xsFHxJooL5h8aE9mdTe{i:02d}",
                "success": True,
                "score": score,
                "duration": round(random.uniform(20, 40), 1),
                "ranking": i + 1,
                "tasksCompleted": random.randint(15, 20),
                "tasksTotal": 20,
                "stake": random.randint(100000, 200000),
                "emission": random.randint(5000, 10000),
                "lastSeen": datetime.now(timezone.utc).isoformat(),
                "validatorId": f"validator_{round_data.validator_info.uid}"
            })
        
        # Sort by score descending
        miners.sort(key=lambda x: x["score"], reverse=True)
        
        return RoundMinersResponse(
            success=True,
            data={
                "miners": miners,
                "total": limit,
                "page": 1,
                "limit": limit
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching top miners: {e}")
        return RoundMinersResponse(
            success=False,
            error=f"Failed to fetch top miners: {str(e)}",
            code="TOP_MINERS_ERROR"
        )

@router.get("/{round_id}/miners/{uid}", response_model=RoundMinersResponse)
async def get_round_miner_detail(
    round_id: int = Path(..., description="Round ID"),
    uid: int = Path(..., description="Miner UID")
):
    """
    Returns detailed performance data for a specific miner in a round.
    """
    try:
        logger.info(f"Fetching miner detail for round_id={round_id}, uid={uid}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Mock miner detail
        score = round(random.uniform(0.3, 0.95), 3)
        miner = {
            "uid": uid,
            "hotkey": f"5GHrA5gqhWVm1Cp92jXaoH7caxtE7xsFHxJooL5h8aE9mdTe{uid:02d}",
            "success": score > 0.5,
            "score": score,
            "duration": round(random.uniform(20, 60), 1),
            "ranking": random.randint(1, 50),
            "tasksCompleted": random.randint(10, 20),
            "tasksTotal": 20,
            "stake": random.randint(50000, 200000),
            "emission": random.randint(2500, 10000),
            "lastSeen": datetime.now(timezone.utc).isoformat(),
            "validatorId": f"validator_{round_data.validator_info.uid}"
        }
        
        return RoundMinersResponse(
            success=True,
            data={"miner": miner}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching miner detail: {e}")
        return RoundMinersResponse(
            success=False,
            error=f"Failed to fetch miner detail: {str(e)}",
            code="MINER_DETAIL_ERROR"
        )

@router.get("/{round_id}/validators", response_model=RoundValidatorsResponse)
async def get_round_validators(round_id: int = Path(..., description="Round ID")):
    """
    Returns validators and their performance for a specific round.
    """
    try:
        logger.info(f"Fetching validators for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get validator info from round
        validator = round_data.validator_info
        
        validators = [{
            "id": f"validator_{validator.uid}",
            "name": validator.name or f"Validator {validator.uid}",
            "hotkey": validator.hotkey,
            "icon": f"/validators/{validator.name or f'validator_{validator.uid}'}.png",
            "status": "active",
            "totalTasks": round_data.n_tasks,
            "completedTasks": int(round_data.n_tasks * 0.95),
            "averageScore": round_data.average_score or 0.0,
            "weight": int(validator.stake),
            "trust": validator.vtrust,
            "version": int(validator.version.split('.')[0]) if validator.version else 7,
            "stake": int(validator.stake),
            "emission": int(validator.stake * 0.05),
            "lastSeen": datetime.now(timezone.utc).isoformat(),
            "uptime": 99.5
        }]
        
        return RoundValidatorsResponse(
            success=True,
            data={
                "validators": validators,
                "total": 1
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round validators: {e}")
        return RoundValidatorsResponse(
            success=False,
            error=f"Failed to fetch round validators: {str(e)}",
            code="ROUND_VALIDATORS_ERROR"
        )

@router.get("/{round_id}/validators/{validator_id}", response_model=RoundValidatorsResponse)
async def get_round_validator_detail(
    round_id: int = Path(..., description="Round ID"),
    validator_id: str = Path(..., description="Validator ID")
):
    """
    Returns detailed performance data for a specific validator in a round.
    """
    try:
        logger.info(f"Fetching validator detail for round_id={round_id}, validator_id={validator_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get validator info from round
        validator = round_data.validator_info
        
        validator_detail = {
            "id": validator_id,
            "name": validator.name or f"Validator {validator.uid}",
            "hotkey": validator.hotkey,
            "icon": f"/validators/{validator.name or f'validator_{validator.uid}'}.png",
            "status": "active",
            "totalTasks": round_data.n_tasks,
            "completedTasks": int(round_data.n_tasks * 0.95),
            "averageScore": round_data.average_score or 0.0,
            "weight": int(validator.stake),
            "trust": validator.vtrust,
            "version": int(validator.version.split('.')[0]) if validator.version else 7,
            "stake": int(validator.stake),
            "emission": int(validator.stake * 0.05),
            "lastSeen": datetime.now(timezone.utc).isoformat(),
            "uptime": 99.5
        }
        
        return RoundValidatorsResponse(
            success=True,
            data={"validator": validator_detail}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching validator detail: {e}")
        return RoundValidatorsResponse(
            success=False,
            error=f"Failed to fetch validator detail: {str(e)}",
            code="VALIDATOR_DETAIL_ERROR"
        )

@router.get("/{round_id}/activity", response_model=RoundActivityResponse)
async def get_round_activity(
    round_id: int = Path(..., description="Round ID"),
    limit: int = Query(10, ge=1, le=100, description="Number of activities"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    type: Optional[str] = Query(None, description="Filter by activity type"),
    since: Optional[str] = Query(None, description="ISO timestamp to filter activities since")
):
    """
    Returns recent activity feed for a specific round.
    """
    try:
        logger.info(f"Fetching activity for round_id={round_id}")

        # Handle FastAPI Query object quirk
        if hasattr(limit, 'annotation'):
            limit = 10
        if hasattr(offset, 'annotation'):
            offset = 0
        if hasattr(type, 'annotation'):
            type = None
        if hasattr(since, 'annotation'):
            since = None

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Mock activity data
        activities = []
        activity_types = ["task_completed", "miner_joined", "validator_updated", "round_progress"]
        
        for i in range(25):  # 25 total activities
            activity_type = random.choice(activity_types)
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 1440))
            
            # Apply type filter
            if type and activity_type != type:
                continue
            
            # Apply since filter
            if since:
                since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
                if timestamp < since_dt:
                    continue
            
            activities.append({
                "id": f"activity_{i+1}",
                "type": activity_type,
                "message": f"Activity {i+1}: {activity_type.replace('_', ' ').title()}",
                "timestamp": timestamp.isoformat(),
                "metadata": {
                    "minerUid": random.randint(42, 92),
                    "taskId": f"task_{random.randint(1, 100)}",
                    "score": round(random.uniform(0.3, 0.95), 3),
                    "duration": random.randint(20, 60)
                }
            })
        
        # Sort by timestamp descending
        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        
        # Apply pagination
        paginated_activities = activities[offset:offset + limit]
        
        return RoundActivityResponse(
            success=True,
            data={
                "activities": paginated_activities,
                "total": len(activities)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round activity: {e}")
        return RoundActivityResponse(
            success=False,
            error=f"Failed to fetch round activity: {str(e)}",
            code="ROUND_ACTIVITY_ERROR"
        )

@router.get("/{round_id}/progress", response_model=RoundProgressResponse)
async def get_round_progress(round_id: int = Path(..., description="Round ID")):
    """
    Returns real-time progress information for a round.
    """
    try:
        logger.info(f"Fetching progress for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Calculate progress
        current_block = round_data.started_at + int((round_data.ended_at or round_data.started_at + 3600) - round_data.started_at) * 0.75
        start_block = round_data.started_at
        end_block = round_data.ended_at or round_data.started_at + 3600
        blocks_remaining = max(0, end_block - current_block)
        progress = min(1.0, (current_block - start_block) / (end_block - start_block))
        
        # Calculate estimated time remaining
        hours_remaining = int(blocks_remaining / 100)  # Mock calculation
        days = hours_remaining // 24
        hours = hours_remaining % 24
        minutes = int((blocks_remaining % 100) * 0.6)  # Mock calculation
        seconds = 0
        
        progress_info = {
            "roundId": round_id,
            "currentBlock": int(current_block),
            "startBlock": start_block,
            "endBlock": end_block,
            "blocksRemaining": int(blocks_remaining),
            "progress": progress,
            "estimatedTimeRemaining": {
                "days": days,
                "hours": hours,
                "minutes": minutes,
                "seconds": seconds
            },
            "lastUpdated": datetime.now(timezone.utc).isoformat()
        }
        
        return RoundProgressResponse(
            success=True,
            data={"progress": progress_info}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round progress: {e}")
        return RoundProgressResponse(
            success=False,
            error=f"Failed to fetch round progress: {str(e)}",
            code="ROUND_PROGRESS_ERROR"
        )

@router.get("/{round_id}/summary", response_model=RoundSummaryResponse)
async def get_round_summary(round_id: int = Path(..., description="Round ID")):
    """
    Returns quick summary statistics for a round.
    """
    try:
        logger.info(f"Fetching summary for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Calculate progress
        current_block = round_data.started_at + int((round_data.ended_at or round_data.started_at + 3600) - round_data.started_at) * 0.75
        start_block = round_data.started_at
        end_block = round_data.ended_at or round_data.started_at + 3600
        blocks_remaining = max(0, end_block - current_block)
        progress = min(1.0, (current_block - start_block) / (end_block - start_block))
        
        # Calculate time remaining
        hours_remaining = int(blocks_remaining / 100)
        if hours_remaining >= 24:
            time_remaining = f"{hours_remaining // 24}d {hours_remaining % 24}h"
        elif hours_remaining >= 1:
            time_remaining = f"{hours_remaining}h {int((blocks_remaining % 100) * 0.6)}m"
        else:
            time_remaining = f"{int((blocks_remaining % 100) * 0.6)}m"
        
        summary = {
            "roundId": round_id,
            "status": "active" if round_data.round_id == "round_020" else "completed",
            "progress": progress,
            "totalMiners": 50,
            "averageScore": round_data.average_score or 0.0,
            "topScore": round_data.top_score or 0.0,
            "timeRemaining": time_remaining
        }
        
        return RoundSummaryResponse(
            success=True,
            data=summary
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round summary: {e}")
        return RoundSummaryResponse(
            success=False,
            error=f"Failed to fetch round summary: {str(e)}",
            code="ROUND_SUMMARY_ERROR"
        )

@router.post("/compare", response_model=RoundComparisonResponse)
async def compare_rounds(request: RoundComparisonRequest):
    """
    Compares multiple rounds and returns comparative data.
    """
    try:
        logger.info(f"Comparing rounds: {request.roundIds}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get rounds data
        rounds_data = data_builder.get_rounds()
        
        comparisons = []
        for round_id in request.roundIds:
            round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
            
            if not round_data:
                continue
            
            # Mock statistics
            statistics = {
                "roundId": round_id,
                "totalMiners": 50,
                "activeMiners": 45,
                "totalTasks": round_data.n_tasks,
                "completedTasks": int(round_data.n_tasks * 0.75),
                "averageScore": round_data.average_score or 0.0,
                "topScore": round_data.top_score or 0.0,
                "successRate": 0.75,
                "averageDuration": 32.5,
                "totalStake": 5000000,
                "totalEmission": 250000,
                "lastUpdated": datetime.now(timezone.utc).isoformat()
            }
            
            # Mock top miners
            top_miners = []
            for i in range(3):
                top_miners.append({
                    "uid": 42 + i,
                    "score": round(random.uniform(0.8, 0.95), 3),
                    "ranking": i + 1
                })
            
            comparisons.append({
                "roundId": round_id,
                "statistics": statistics,
                "topMiners": top_miners
            })
        
        return RoundComparisonResponse(
            success=True,
            data={"rounds": comparisons}
        )
        
    except Exception as e:
        logger.error(f"Error comparing rounds: {e}")
        return RoundComparisonResponse(
            success=False,
            error=f"Failed to compare rounds: {str(e)}",
            code="ROUND_COMPARISON_ERROR"
        )

@router.get("/{round_id}/timeline", response_model=RoundTimelineResponse)
async def get_round_timeline(round_id: int = Path(..., description="Round ID")):
    """
    Returns timeline data showing progress over time.
    """
    try:
        logger.info(f"Fetching timeline for round_id={round_id}")

        db = get_mock_db()
        data_builder = DataBuilder(db)
        
        # Get round data
        rounds_data = data_builder.get_rounds()
        round_data = next((r for r in rounds_data if r.round_id == f"round_{round_id:03d}"), None)
        
        if not round_data:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Generate timeline data
        timeline = []
        start_time = datetime.fromtimestamp(round_data.started_at, tz=timezone.utc)
        end_time = datetime.fromtimestamp(round_data.ended_at or round_data.started_at + 3600, tz=timezone.utc)
        
        # Create hourly timeline points
        current_time = start_time
        while current_time <= end_time:
            # Mock progress calculation
            elapsed_hours = (current_time - start_time).total_seconds() / 3600
            total_hours = (end_time - start_time).total_seconds() / 3600
            progress = min(1.0, elapsed_hours / total_hours)
            
            timeline.append({
                "timestamp": current_time.isoformat(),
                "block": round_data.started_at + int(elapsed_hours * 100),
                "completedTasks": int(round_data.n_tasks * progress * 0.75),
                "averageScore": round_data.average_score or 0.0,
                "activeMiners": int(50 * (1 - progress * 0.1))  # Slight decrease over time
            })
            
            current_time += timedelta(hours=1)
        
        return RoundTimelineResponse(
            success=True,
            data={"timeline": timeline}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round timeline: {e}")
        return RoundTimelineResponse(
            success=False,
            error=f"Failed to fetch round timeline: {str(e)}",
            code="ROUND_TIMELINE_ERROR"
        )

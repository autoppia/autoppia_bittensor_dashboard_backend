"""
Overview section API endpoints for the AutoPPIA Bittensor Dashboard.
These endpoints match the specifications provided by the frontend team.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timezone
import logging

from app.models.overview import (
    OverviewMetricsResponse, ValidatorsListResponse, ValidatorDetailResponse,
    CurrentRoundResponse, RoundsListResponse, RoundDetailResponse,
    LeaderboardResponse, StatisticsResponse, NetworkStatusResponse,
    RecentActivityResponse, PerformanceTrendsResponse,
    OverviewMetrics, ValidatorInfo, RoundInfo, LeaderboardEntry,
    SubnetStatistics, NetworkStatus, RecentActivity, PerformanceTrend
)
from app.services.data_builder import DataBuilder
from app.db.mock_mongo import get_mock_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/overview", tags=["overview"])


@router.get("/metrics", response_model=OverviewMetricsResponse)
async def get_overview_metrics():
    """
    Returns high-level metrics for the overview dashboard.
    """
    try:
        logger.info("Fetching overview metrics")
        
        # Get recent rounds data
        rounds = await DataBuilder.build_rounds_list(limit=10, skip=0)
        
        # Calculate metrics
        current_round = len(rounds) if rounds else 0
        total_validators = len(set(round.validator_info.uid for round in rounds))
        total_miners = len(set(miner.uid for round in rounds for miner in round.miners))
        
        # Calculate top score from latest round
        top_score = 0.0
        if rounds and rounds[0].winners:
            top_score = rounds[0].winners[0].get('score', 0.0)
        
        # Get version from validator info (use the most recent validator)
        subnet_version = "1.0.0"  # Default fallback
        if rounds and rounds[0].validator_info.version:
            subnet_version = rounds[0].validator_info.version
        
        # Mock data for websites (this could be calculated from tasks if needed)
        total_websites = 11
        
        metrics = OverviewMetrics(
            topScore=top_score,
            totalWebsites=total_websites,
            totalValidators=total_validators,
            totalMiners=total_miners,
            currentRound=current_round,
            subnetVersion=subnet_version,
            lastUpdated=datetime.now(timezone.utc).isoformat()
        )
        
        return OverviewMetricsResponse(
            success=True,
            data={"metrics": metrics}
        )
        
    except Exception as e:
        logger.error(f"Error fetching overview metrics: {e}")
        return OverviewMetricsResponse(
            success=False,
            error=f"Failed to fetch overview metrics: {str(e)}",
            code="METRICS_FETCH_ERROR"
        )


@router.get("/validators", response_model=ValidatorsListResponse)
async def get_validators(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    sortBy: Optional[str] = Query("weight", description="Sort field"),
    sortOrder: Optional[str] = Query("desc", description="Sort direction")
):
    """
    Returns list of validators with optional filtering and pagination.
    """
    try:
        logger.info(f"Fetching validators with page={page}, limit={limit}, status={status}")
        
        # Handle FastAPI Query object quirk
        if hasattr(status, 'annotation'):
            status = None
        if hasattr(page, 'annotation'):
            page = 1
        if hasattr(limit, 'annotation'):
            limit = 10
        
        # Get rounds data to extract validators
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Extract unique validators
        validators_data = {}
        for round_data in rounds:
            validator = round_data.validator_info
            if validator.uid not in validators_data:
                validators_data[validator.uid] = {
                    "id": f"validator_{validator.uid}",
                    "name": validator.name or f"Validator {validator.uid}",  # Use real name or fallback
                    "hotkey": validator.hotkey,
                    "icon": f"/validators/{validator.name or f'validator_{validator.uid}'}.png",
                    "currentTask": "Login for the following username:user<web_agent_id> and password:password123...",
                    "status": "Sending Tasks",
                    "totalTasks": round_data.n_tasks,
                    "weight": validator.stake,
                    "trust": validator.vtrust,
                    "version": int(validator.version.split('.')[0]) if validator.version else 7,  # Extract major version
                    "lastSeen": datetime.fromtimestamp(round_data.ended_at or round_data.started_at, tz=timezone.utc).isoformat(),
                    "stake": int(validator.stake),
                    "emission": int(validator.stake * 0.05)  # Mock emission calculation
                }
        
        # Convert to list and apply filters
        validators_list = list(validators_data.values())
        
        if status is not None and status != "":
            validators_list = [v for v in validators_list if v["status"] == status]
        
        # Apply sorting
        if sortBy == "weight":
            validators_list.sort(key=lambda x: x["weight"], reverse=(sortOrder == "desc"))
        elif sortBy == "trust":
            validators_list.sort(key=lambda x: x["trust"], reverse=(sortOrder == "desc"))
        elif sortBy == "totalTasks":
            validators_list.sort(key=lambda x: x["totalTasks"], reverse=(sortOrder == "desc"))
        elif sortBy == "name":
            validators_list.sort(key=lambda x: x["name"], reverse=(sortOrder == "desc"))
        
        # Apply pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_validators = validators_list[start_idx:end_idx]
        
        return ValidatorsListResponse(
            success=True,
            data={
                "validators": paginated_validators,
                "total": len(validators_list),
                "page": page,
                "limit": limit
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching validators: {e}")
        return ValidatorsListResponse(
            success=False,
            error=f"Failed to fetch validators: {str(e)}",
            code="VALIDATORS_FETCH_ERROR"
        )


@router.get("/validators/{validator_id}", response_model=ValidatorDetailResponse)
async def get_validator_detail(validator_id: str):
    """
    Returns details for a specific validator.
    """
    try:
        logger.info(f"Fetching validator details for {validator_id}")
        
        # Get rounds data to find the validator
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Find the validator
        validator_data = None
        for round_data in rounds:
            if f"validator_{round_data.validator_info.uid}" == validator_id:
                validator = round_data.validator_info
                validator_data = {
                    "id": validator_id,
                    "name": validator.name or f"Validator {validator.uid}",  # Use real name or fallback
                    "hotkey": validator.hotkey,
                    "icon": f"/validators/{validator.name or f'validator_{validator.uid}'}.png",
                    "currentTask": "Login for the following username:user<web_agent_id> and password:password123...",
                    "status": "Sending Tasks",
                    "totalTasks": round_data.n_tasks,
                    "weight": validator.stake,
                    "trust": validator.vtrust,
                    "version": int(validator.version.split('.')[0]) if validator.version else 7,  # Extract major version
                    "lastSeen": datetime.fromtimestamp(round_data.ended_at or round_data.started_at, tz=timezone.utc).isoformat(),
                    "stake": int(validator.stake),
                    "emission": int(validator.stake * 0.05)
                }
                break
        
        if not validator_data:
            return ValidatorDetailResponse(
                success=False,
                error=f"Validator {validator_id} not found",
                code="VALIDATOR_NOT_FOUND"
            )
        
        return ValidatorDetailResponse(
            success=True,
            data={"validator": validator_data}
        )
        
    except Exception as e:
        logger.error(f"Error fetching validator details: {e}")
        return ValidatorDetailResponse(
            success=False,
            error=f"Failed to fetch validator details: {str(e)}",
            code="VALIDATOR_DETAIL_FETCH_ERROR"
        )


@router.get("/rounds/current", response_model=CurrentRoundResponse)
async def get_current_round():
    """
    Returns information about the current round.
    """
    try:
        logger.info("Fetching current round information")
        
        # Get the latest round
        rounds = await DataBuilder.build_rounds_list(limit=1, skip=0)
        
        if not rounds:
            return CurrentRoundResponse(
                success=False,
                error="No rounds found",
                code="NO_ROUNDS_FOUND"
            )
        
        latest_round = rounds[0]
        
        # Calculate round metrics
        total_tasks = latest_round.n_tasks
        completed_tasks = latest_round.n_winners
        average_score = 0.0
        top_score = 0.0
        
        if latest_round.winners:
            scores = [winner.get('score', 0.0) for winner in latest_round.winners]
            average_score = sum(scores) / len(scores) if scores else 0.0
            top_score = max(scores) if scores else 0.0
        
        round_info = RoundInfo(
            id=int(latest_round.round_id) if latest_round.round_id.isdigit() else 20,
            startBlock=latest_round.start_block,
            endBlock=latest_round.end_block or latest_round.start_block + 1000,
            current=True,
            startTime=datetime.fromtimestamp(latest_round.started_at, tz=timezone.utc).isoformat(),
            endTime=datetime.fromtimestamp(latest_round.ended_at, tz=timezone.utc).isoformat() if latest_round.ended_at else None,
            status="active" if not latest_round.ended_at else "completed",
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            averageScore=average_score,
            topScore=top_score
        )
        
        return CurrentRoundResponse(
            success=True,
            data={"round": round_info}
        )
        
    except Exception as e:
        logger.error(f"Error fetching current round: {e}")
        return CurrentRoundResponse(
            success=False,
            error=f"Failed to fetch current round: {str(e)}",
            code="CURRENT_ROUND_FETCH_ERROR"
        )


@router.get("/rounds", response_model=RoundsListResponse)
async def get_rounds(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    includeCurrent: bool = Query(True, description="Include current round")
):
    """
    Returns list of rounds with optional filtering.
    """
    try:
        logger.info(f"Fetching rounds with page={page}, limit={limit}, status={status}")
        
        # Handle FastAPI Query object quirk
        if hasattr(status, 'annotation'):
            status = None
        if hasattr(page, 'annotation'):
            page = 1
        if hasattr(limit, 'annotation'):
            limit = 10
        
        # Get rounds data
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Convert to round info format
        rounds_list = []
        current_round = None
        
        for i, round_data in enumerate(rounds):
            # Calculate round metrics
            total_tasks = round_data.n_tasks
            completed_tasks = round_data.n_winners
            average_score = 0.0
            top_score = 0.0
            
            if round_data.winners:
                scores = [winner.get('score', 0.0) for winner in round_data.winners]
                average_score = sum(scores) / len(scores) if scores else 0.0
                top_score = max(scores) if scores else 0.0
            
            round_info = RoundInfo(
                id=int(round_data.round_id) if round_data.round_id.isdigit() else 20 - i,
                startBlock=round_data.start_block,
                endBlock=round_data.end_block or round_data.start_block + 1000,
                current=(i == 0),  # First round is current
                startTime=datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
                endTime=datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None,
                status="active" if not round_data.ended_at else "completed",
                totalTasks=total_tasks,
                completedTasks=completed_tasks,
                averageScore=average_score,
                topScore=top_score
            )
            
            if i == 0:
                current_round = round_info
            
            rounds_list.append(round_info)
        
        # Apply status filter
        if status:
            rounds_list = [r for r in rounds_list if r.status == status]
        
        # Apply pagination
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_rounds = rounds_list[start_idx:end_idx]
        
        return RoundsListResponse(
            success=True,
            data={
                "rounds": paginated_rounds,
                "currentRound": current_round if includeCurrent else None,
                "total": len(rounds_list)
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching rounds: {e}")
        return RoundsListResponse(
            success=False,
            error=f"Failed to fetch rounds: {str(e)}",
            code="ROUNDS_FETCH_ERROR"
        )


@router.get("/rounds/{round_id}", response_model=RoundDetailResponse)
async def get_round_detail(round_id: str):
    """
    Returns details for a specific round.
    """
    try:
        logger.info(f"Fetching round details for {round_id}")
        
        # Get the specific round
        round_data = await DataBuilder.build_round_with_details(round_id)
        
        if not round_data:
            return RoundDetailResponse(
                success=False,
                error=f"Round {round_id} not found",
                code="ROUND_NOT_FOUND"
            )
        
        # Calculate round metrics
        total_tasks = round_data.n_tasks
        completed_tasks = round_data.n_winners
        average_score = 0.0
        top_score = 0.0
        
        if round_data.winners:
            scores = [winner.get('score', 0.0) for winner in round_data.winners]
            average_score = sum(scores) / len(scores) if scores else 0.0
            top_score = max(scores) if scores else 0.0
        
        round_info = RoundInfo(
            id=int(round_id) if round_id.isdigit() else 20,
            startBlock=round_data.start_block,
            endBlock=round_data.end_block or round_data.start_block + 1000,
            current=False,  # Only current round endpoint returns current=True
            startTime=datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
            endTime=datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None,
            status="active" if not round_data.ended_at else "completed",
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            averageScore=average_score,
            topScore=top_score
        )
        
        return RoundDetailResponse(
            success=True,
            data={"round": round_info}
        )
        
    except Exception as e:
        logger.error(f"Error fetching round details: {e}")
        return RoundDetailResponse(
            success=False,
            error=f"Failed to fetch round details: {str(e)}",
            code="ROUND_DETAIL_FETCH_ERROR"
        )


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    timeRange: str = Query("7D", description="Time range"),
    limit: int = Query(10, ge=1, le=100, description="Number of data points"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
):
    """
    Returns performance comparison data for the leaderboard chart.
    """
    try:
        logger.info(f"Fetching leaderboard with timeRange={timeRange}, limit={limit}, offset={offset}")
        
        # Handle FastAPI Query object quirk
        if hasattr(offset, 'annotation'):
            offset = 0
        if hasattr(limit, 'annotation'):
            limit = 10
        
        # Get rounds data
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Generate leaderboard entries
        leaderboard_entries = []
        for i, round_data in enumerate(rounds[offset:offset+limit]):
            if round_data.winners:
                top_score = round_data.winners[0].get('score', 0.0)
                
                entry = LeaderboardEntry(
                    round=int(round_data.round_id) if round_data.round_id.isdigit() else 20 - i,
                    subnet36=top_score * 0.8,  # Mock subnet36 score
                    openai_cua=top_score * 0.85,  # Mock openai_cua score
                    anthropic_cua=top_score * 0.9,  # Mock anthropic_cua score
                    browser_use=top_score,  # Use actual score for browser_use
                    timestamp=datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat()
                )
                leaderboard_entries.append(entry)
        
        # Calculate time range
        start_time = datetime.fromtimestamp(rounds[-1].started_at, tz=timezone.utc).isoformat() if rounds else datetime.now(timezone.utc).isoformat()
        end_time = datetime.fromtimestamp(rounds[0].started_at, tz=timezone.utc).isoformat() if rounds else datetime.now(timezone.utc).isoformat()
        
        return LeaderboardResponse(
            success=True,
            data={
                "leaderboard": leaderboard_entries,
                "total": len(rounds),
                "timeRange": {
                    "start": start_time,
                    "end": end_time
                }
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        return LeaderboardResponse(
            success=False,
            error=f"Failed to fetch leaderboard: {str(e)}",
            code="LEADERBOARD_FETCH_ERROR"
        )


@router.get("/statistics", response_model=StatisticsResponse)
async def get_statistics():
    """
    Returns comprehensive subnet statistics and network health metrics.
    """
    try:
        logger.info("Fetching subnet statistics")
        
        # Get rounds data
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Calculate statistics
        total_stake = sum(round_data.validator_info.stake for round_data in rounds)
        total_emission = int(total_stake * 0.05)  # Mock emission calculation
        average_trust = sum(round_data.validator_info.vtrust for round_data in rounds) / len(rounds) if rounds else 0.0
        active_validators = len(set(round.validator_info.uid for round in rounds))
        registered_miners = len(set(miner.uid for round in rounds for miner in round.miners))
        total_tasks_completed = sum(round_data.n_winners for round_data in rounds)
        
        # Calculate average task score
        all_scores = []
        for round_data in rounds:
            if round_data.winners:
                all_scores.extend([winner.get('score', 0.0) for winner in round_data.winners])
        average_task_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
        
        statistics = SubnetStatistics(
            totalStake=int(total_stake),
            totalEmission=total_emission,
            averageTrust=average_trust,
            activeValidators=active_validators,
            registeredMiners=registered_miners,
            totalTasksCompleted=total_tasks_completed,
            averageTaskScore=average_task_score,
            lastUpdated=datetime.now(timezone.utc).isoformat()
        )
        
        return StatisticsResponse(
            success=True,
            data={"statistics": statistics}
        )
        
    except Exception as e:
        logger.error(f"Error fetching statistics: {e}")
        return StatisticsResponse(
            success=False,
            error=f"Failed to fetch statistics: {str(e)}",
            code="STATISTICS_FETCH_ERROR"
        )


@router.get("/network-status", response_model=NetworkStatusResponse)
async def get_network_status():
    """
    Returns real-time network status information.
    """
    try:
        logger.info("Fetching network status")
        
        # Get rounds data to determine active validators
        rounds = await DataBuilder.build_rounds_list(limit=10, skip=0)
        active_validators = len(set(round.validator_info.uid for round in rounds))
        
        # Mock network status
        network_status = NetworkStatus(
            status="healthy",
            message="All systems operational",
            lastChecked=datetime.now(timezone.utc).isoformat(),
            activeValidators=active_validators,
            networkLatency=45  # Mock latency in ms
        )
        
        return NetworkStatusResponse(
            success=True,
            data=network_status
        )
        
    except Exception as e:
        logger.error(f"Error fetching network status: {e}")
        return NetworkStatusResponse(
            success=False,
            error=f"Failed to fetch network status: {str(e)}",
            code="NETWORK_STATUS_FETCH_ERROR"
        )


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=100, description="Number of activities to return")
):
    """
    Returns recent activity feed for the dashboard.
    """
    try:
        logger.info(f"Fetching recent activity with limit={limit}")
        
        # Get recent rounds data
        rounds = await DataBuilder.build_rounds_list(limit=10, skip=0)
        
        activities = []
        for i, round_data in enumerate(rounds[:limit]):
            if round_data.winners:
                top_winner = round_data.winners[0]
                
                activity = RecentActivity(
                    id=f"activity_{i+1}",
                    type="task_completed",
                    message=f"Validator 'Autoppia {round_data.validator_info.uid}' completed task #{top_winner.get('task_id', 'unknown')}",
                    timestamp=datetime.fromtimestamp(round_data.ended_at or round_data.started_at, tz=timezone.utc).isoformat(),
                    metadata={
                        "validatorId": f"validator_{round_data.validator_info.uid}",
                        "taskId": str(top_winner.get('task_id', 'unknown')),
                        "score": top_winner.get('score', 0.0)
                    }
                )
                activities.append(activity)
        
        # Add some mock activities
        if len(activities) < limit:
            activities.append(RecentActivity(
                id="activity_round_started",
                type="round_started",
                message="Round 21 started",
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata={
                    "roundId": "21",
                    "startBlock": 6527001
                }
            ))
        
        return RecentActivityResponse(
            success=True,
            data={
                "activities": activities,
                "total": len(activities)
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching recent activity: {e}")
        return RecentActivityResponse(
            success=False,
            error=f"Failed to fetch recent activity: {str(e)}",
            code="RECENT_ACTIVITY_FETCH_ERROR"
        )


@router.get("/performance-trends", response_model=PerformanceTrendsResponse)
async def get_performance_trends(
    days: int = Query(7, ge=1, le=30, description="Number of days to include")
):
    """
    Returns performance trends data for charts.
    """
    try:
        logger.info(f"Fetching performance trends for {days} days")
        
        # Get rounds data
        rounds = await DataBuilder.build_rounds_list(limit=days, skip=0)
        
        trends = []
        for i, round_data in enumerate(rounds):
            # Calculate metrics for this round
            total_tasks = round_data.n_tasks
            average_score = 0.0
            
            if round_data.winners:
                scores = [winner.get('score', 0.0) for winner in round_data.winners]
                average_score = sum(scores) / len(scores) if scores else 0.0
            
            trend = PerformanceTrend(
                date=datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).strftime("%Y-%m-%d"),
                averageScore=average_score,
                totalTasks=total_tasks,
                activeValidators=1  # Each round has one validator
            )
            trends.append(trend)
        
        return PerformanceTrendsResponse(
            success=True,
            data={
                "trends": trends,
                "period": f"{days} days"
            }
        )
        
    except Exception as e:
        logger.error(f"Error fetching performance trends: {e}")
        return PerformanceTrendsResponse(
            success=False,
            error=f"Failed to fetch performance trends: {str(e)}",
            code="PERFORMANCE_TRENDS_FETCH_ERROR"
        )

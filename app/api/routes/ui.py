"""
UI endpoints for dashboard and frontend data.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from app.models.ui import (
    OverviewResponse, LeaderboardResponse, AgentsListResponse, 
    MinerDetailsResponse, AgentRunDetailsResponse, TaskDetailsResponse,
    AnalyticsResponse
)
from app.services.data_builder import DataBuilder
from app.db.mock_mongo import get_mock_db
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ui", tags=["ui"])


@router.get("/overview", response_model=OverviewResponse)
async def get_overview():
    """
    Get overview dashboard data including metrics, charts, and live events.
    """
    try:
        logger.info("Fetching overview dashboard data")
        
        # Get recent rounds for overview metrics
        rounds = await DataBuilder.build_rounds_list(limit=10, skip=0)
        
        # Calculate overview metrics
        total_rounds = len(rounds)
        active_validators = len(set(round.validator_info.uid for round in rounds))
        registered_miners = len(set(miner.uid for round in rounds for miner in round.miners))
        
        # Get current top score from latest round
        current_top_score = 0.0
        if rounds:
            latest_round = rounds[0]
            if latest_round.winners:
                current_top_score = latest_round.winners[0].get('score', 0.0)
        
        # Generate chart data (simplified for now)
        chart_data = {
            "scores": [
                {"day": i, "score": current_top_score + (i * 0.1), "timestamp": 0, "date": f"2024-01-{i:02d}", "formatted_date": f"Jan {i}"}
                for i in range(1, 8)
            ]
        }
        
        # Generate live events (simplified)
        live_events = []
        if rounds:
            latest_round = rounds[0]
            live_events.append({
                "type": "round_completed",
                "round_id": latest_round.round_id,
                "top_miner_uid": latest_round.winners[0].get('miner_uid', 0) if latest_round.winners else 0,
                "top_score": current_top_score,
                "timestamp": latest_round.ended_at or latest_round.started_at,
                "validator_uid": latest_round.validator_info.uid,
                "message": f"Round {latest_round.round_id} completed with top score {current_top_score:.2f}"
            })
        
        # Generate validator cards
        validator_cards = []
        for round_data in rounds[:3]:  # Top 3 validators
            validator = round_data.validator_info
            validator_cards.append({
                "validator_uid": validator.uid,
                "name": f"Validator {validator.uid}",
                "hotkey": validator.hotkey,
                "logo_url": None,
                "status_label": "Active",
                "status_color": "green",
                "current_task": {"task_id": "current_task", "status": "running"},
                "metrics": {"rounds_completed": 1, "avg_score": current_top_score},
                "stake": {"amount": validator.stake, "currency": "TAO"},
                "vtrust": validator.vtrust,
                "version": 1,
                "last_activity": round_data.ended_at or round_data.started_at,
                "uptime": 99.9
            })
        
        overview_metrics = {
            "main_chart_data": chart_data,
            "current_top_score": current_top_score,
            "target_score": 10.0,
            "active_validators": active_validators,
            "registered_miners": registered_miners,
            "total_rounds": total_rounds,
            "time_range": "7d"
        }
        
        return OverviewResponse(
            success=True,
            overview=overview_metrics,
            validator_cards=validator_cards,
            live_events=live_events
        )
        
    except Exception as e:
        logger.error(f"Error fetching overview data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch overview data: {str(e)}")


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    type: str = Query("rounds", description="Leaderboard type: rounds, miners, or validators"),
    limit: int = Query(10, ge=1, le=100, description="Number of entries to return"),
    skip: int = Query(0, ge=0, description="Number of entries to skip")
):
    """
    Get leaderboard data for rounds, miners, or validators.
    """
    try:
        logger.info(f"Fetching {type} leaderboard with limit={limit}, skip={skip}")
        
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        if type == "rounds":
            # Round leaderboard
            round_entries = []
            for round_data in rounds[skip:skip+limit]:
                round_entries.append({
                    "round_id": round_data.round_id,
                    "validator_uid": round_data.validator_info.uid,
                    "validator_hotkey": round_data.validator_info.hotkey,
                    "started_at": round_data.started_at,
                    "ended_at": round_data.ended_at,
                    "n_tasks": round_data.n_tasks,
                    "n_miners": round_data.n_miners,
                    "n_winners": round_data.n_winners,
                    "top_score": round_data.winners[0].get('score', 0.0) if round_data.winners else 0.0,
                    "status": round_data.status
                })
            
            leaderboard_data = {
                "rounds": round_entries,
                "miners": [],
                "validators": []
            }
            
        elif type == "miners":
            # Miner leaderboard
            miner_scores = {}
            for round_data in rounds:
                for winner in round_data.winners:
                    miner_uid = winner.get('miner_uid')
                    if miner_uid:
                        if miner_uid not in miner_scores:
                            miner_scores[miner_uid] = {"total_score": 0, "rounds": 0}
                        miner_scores[miner_uid]["total_score"] += winner.get('score', 0)
                        miner_scores[miner_uid]["rounds"] += 1
            
            miner_entries = []
            for miner_uid, stats in sorted(miner_scores.items(), key=lambda x: x[1]["total_score"], reverse=True)[skip:skip+limit]:
                miner_entries.append({
                    "miner_uid": miner_uid,
                    "total_score": stats["total_score"],
                    "avg_score": stats["total_score"] / stats["rounds"],
                    "rounds_participated": stats["rounds"],
                    "rank": len(miner_entries) + 1
                })
            
            leaderboard_data = {
                "rounds": [],
                "miners": miner_entries,
                "validators": []
            }
            
        elif type == "validators":
            # Validator leaderboard
            validator_stats = {}
            for round_data in rounds:
                validator_uid = round_data.validator_info.uid
                if validator_uid not in validator_stats:
                    validator_stats[validator_uid] = {"rounds": 0, "total_miners": 0}
                validator_stats[validator_uid]["rounds"] += 1
                validator_stats[validator_uid]["total_miners"] += round_data.n_miners
            
            validator_entries = []
            for validator_uid, stats in sorted(validator_stats.items(), key=lambda x: x[1]["rounds"], reverse=True)[skip:skip+limit]:
                validator_entries.append({
                    "validator_uid": validator_uid,
                    "rounds_completed": stats["rounds"],
                    "total_miners_evaluated": stats["total_miners"],
                    "avg_miners_per_round": stats["total_miners"] / stats["rounds"],
                    "rank": len(validator_entries) + 1
                })
            
            leaderboard_data = {
                "rounds": [],
                "miners": [],
                "validators": validator_entries
            }
        
        else:
            raise HTTPException(status_code=400, detail="Invalid leaderboard type. Must be 'rounds', 'miners', or 'validators'")
        
        return LeaderboardResponse(
            success=True,
            leaderboard=leaderboard_data,
            total_entries=len(rounds),
            current_page=skip // limit + 1,
            total_pages=(len(rounds) + limit - 1) // limit
        )
        
    except Exception as e:
        logger.error(f"Error fetching leaderboard data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch leaderboard data: {str(e)}")


@router.get("/agents", response_model=AgentsListResponse)
async def get_agents_list(
    limit: int = Query(20, ge=1, le=100, description="Number of agents to return"),
    skip: int = Query(0, ge=0, description="Number of agents to skip")
):
    """
    Get list of all agents (miners) with their basic information.
    """
    try:
        logger.info(f"Fetching agents list with limit={limit}, skip={skip}")
        
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Collect unique miners
        miners_data = {}
        for round_data in rounds:
            for miner in round_data.miners:
                if miner.uid not in miners_data:
                    miners_data[miner.uid] = {
                        "miner_uid": miner.uid,
                        "hotkey": miner.hotkey,
                        "agent_name": miner.agent_name or f"Agent {miner.uid}",
                        "agent_image": miner.agent_image or "",
                        "github": miner.github or "",
                        "total_rounds": 0,
                        "avg_score": 0.0,
                        "last_activity": 0.0
                    }
                miners_data[miner.uid]["total_rounds"] += 1
        
        # Calculate average scores
        for round_data in rounds:
            for winner in round_data.winners:
                miner_uid = winner.get('miner_uid')
                if miner_uid in miners_data:
                    current_avg = miners_data[miner_uid]["avg_score"]
                    rounds_count = miners_data[miner_uid]["total_rounds"]
                    new_score = winner.get('score', 0.0)
                    miners_data[miner_uid]["avg_score"] = (current_avg * (rounds_count - 1) + new_score) / rounds_count
                    miners_data[miner_uid]["last_activity"] = max(miners_data[miner_uid]["last_activity"], round_data.ended_at or round_data.started_at)
        
        # Convert to list and sort by average score
        agents_list = list(miners_data.values())
        agents_list.sort(key=lambda x: x["avg_score"], reverse=True)
        
        # Apply pagination
        paginated_agents = agents_list[skip:skip+limit]
        
        return AgentsListResponse(
            success=True,
            agents=AgentsListData(
                agents=paginated_agents,
                total_agents=len(agents_list),
                current_page=skip // limit + 1,
                total_pages=(len(agents_list) + limit - 1) // limit
            )
        )
        
    except Exception as e:
        logger.error(f"Error fetching agents list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agents list: {str(e)}")


@router.get("/agents/{miner_uid}", response_model=MinerDetailsResponse)
async def get_miner_details(miner_uid: int):
    """
    Get detailed information about a specific miner/agent.
    """
    try:
        logger.info(f"Fetching details for miner {miner_uid}")
        
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Find miner data
        miner_info = None
        miner_rounds = []
        
        for round_data in rounds:
            for miner in round_data.miners:
                if miner.uid == miner_uid:
                    if miner_info is None:
                        miner_info = miner
                    
                    # Find this miner's performance in this round
                    miner_performance = None
                    for winner in round_data.winners:
                        if winner.get('miner_uid') == miner_uid:
                            miner_performance = {
                                "round_id": round_data.round_id,
                                "score": winner.get('score', 0.0),
                                "rank": winner.get('rank', 0),
                                "reward": winner.get('reward', 0.0)
                            }
                            break
                    
                    if miner_performance:
                        miner_rounds.append(miner_performance)
        
        if miner_info is None:
            raise HTTPException(status_code=404, detail=f"Miner {miner_uid} not found")
        
        # Calculate overall metrics
        total_score = sum(round_data["score"] for round_data in miner_rounds)
        avg_score = total_score / len(miner_rounds) if miner_rounds else 0.0
        best_score = max(round_data["score"] for round_data in miner_rounds) if miner_rounds else 0.0
        
        # Get validator cards for rounds this miner participated in
        validator_cards = []
        for round_data in rounds:
            for miner in round_data.miners:
                if miner.uid == miner_uid:
                    validator = round_data.validator_info
                    validator_cards.append({
                        "validator_uid": validator.uid,
                        "hotkey": validator.hotkey,
                        "stake": validator.stake,
                        "vtrust": validator.vtrust,
                        "rounds_with_miner": 1
                    })
                    break
        
        miner_details = {
            "miner_uid": miner_info.uid,
            "hotkey": miner_info.hotkey,
            "agent_name": miner_info.agent_name or f"Agent {miner_info.uid}",
            "agent_image": miner_info.agent_image or "",
            "github": miner_info.github or "",
            "total_rounds": len(miner_rounds),
            "avg_score": avg_score,
            "best_score": best_score,
            "total_score": total_score,
            "last_activity": max(round_data["score"] for round_data in miner_rounds) if miner_rounds else 0.0
        }
        
        return MinerDetailsResponse(
            success=True,
            miner_details=MinerDetailsData(
                miner=miner_details,
                rounds=miner_rounds,
                validator_cards=validator_cards
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching miner details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch miner details: {str(e)}")


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunDetailsResponse)
async def get_agent_run_details(agent_run_id: str):
    """
    Get detailed information about a specific agent evaluation run.
    """
    try:
        logger.info(f"Fetching details for agent run {agent_run_id}")
        
        agent_run = await DataBuilder.build_agent_run_with_details(agent_run_id)
        
        if agent_run is None:
            raise HTTPException(status_code=404, detail=f"Agent run {agent_run_id} not found")
        
        # Convert to UI format
        agent_run_info = {
            "agent_run_id": agent_run.agent_run_id,
            "round_id": agent_run.round_id,
            "validator_uid": agent_run.validator_uid,
            "miner_uid": agent_run.miner_uid,
            "started_at": agent_run.started_at,
            "ended_at": agent_run.ended_at,
            "elapsed_time": agent_run.elapsed_sec,
            "n_tasks_total": agent_run.n_tasks_total,
            "n_tasks_completed": agent_run.n_tasks_completed,
            "n_tasks_failed": agent_run.n_tasks_failed,
            "avg_eval_score": agent_run.avg_eval_score,
            "avg_execution_time": agent_run.avg_execution_time,
            "total_reward": agent_run.total_reward,
            "rank": agent_run.rank,
            "weight": agent_run.weight,
            "status": agent_run.status
        }
        
        # Convert tasks to UI format
        tasks_data = []
        for task in agent_run.tasks:
            tasks_data.append({
                "task_id": task.task_id,
                "round_id": task.round_id,
                "agent_run_id": task.agent_run_id,
                "scope": task.scope,
                "is_web_real": task.is_web_real,
                "web_project_id": task.web_project_id,
                "url": task.url,
                "prompt": task.prompt,
                "html": task.html,
                "clean_html": task.clean_html,
                "interactive_elements": task.interactive_elements,
                "screenshot": task.screenshot,
                "screenshot_description": task.screenshot_description,
                "specifications": task.specifications
            })
        
        return AgentRunDetailsResponse(
            success=True,
            agent_run_details=AgentRunDetailsData(
                agent_run=agent_run_info,
                tasks=tasks_data,
                total_tasks=len(tasks_data),
                current_page=1,
                total_pages=1
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agent run details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent run details: {str(e)}")


@router.get("/tasks/{task_id}", response_model=TaskDetailsResponse)
async def get_task_details(task_id: str):
    """
    Get detailed information about a specific task.
    """
    try:
        logger.info(f"Fetching details for task {task_id}")
        
        # Find the task in the database
        db = get_mock_db()
        task_doc = await db.tasks.find_one({"task_id": task_id})
        
        if task_doc is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        # Get the round information
        round_doc = await db.rounds.find_one({"round_id": task_doc["round_id"]})
        if round_doc is None:
            raise HTTPException(status_code=404, detail=f"Round {task_doc['round_id']} not found")
        
        # Convert to UI format
        task_info = {
            "task_id": task_doc["task_id"],
            "round_id": task_doc["round_id"],
            "agent_run_id": task_doc["agent_run_id"],
            "scope": task_doc["scope"],
            "is_web_real": task_doc["is_web_real"],
            "web_project_id": task_doc["web_project_id"],
            "url": task_doc["url"],
            "prompt": task_doc["prompt"],
            "html": task_doc["html"],
            "clean_html": task_doc["clean_html"],
            "interactive_elements": task_doc["interactive_elements"],
            "screenshot": task_doc["screenshot"],
            "screenshot_description": task_doc["screenshot_description"],
            "specifications": task_doc["specifications"]
        }
        
        round_info = {
            "round_id": round_doc["round_id"],
            "validator_uid": round_doc["validator_info"]["uid"],
            "validator_hotkey": round_doc["validator_info"]["hotkey"],
            "started_at": round_doc["started_at"],
            "ended_at": round_doc["ended_at"],
            "n_tasks": round_doc["n_tasks"],
            "n_miners": round_doc["n_miners"],
            "status": round_doc["status"]
        }
        
        return TaskDetailsResponse(
            success=True,
            task_details=TaskDetailsData(
                task=task_info,
                round=round_info,
                actions=[],  # Would need to get from task solutions
                gif_recording=None  # Would need to get from evaluation results
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching task details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch task details: {str(e)}")


@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics():
    """
    Get analytics data including performance trends and participation metrics.
    """
    try:
        logger.info("Fetching analytics data")
        
        rounds = await DataBuilder.build_rounds_list(limit=50, skip=0)
        
        # Generate performance analytics
        performance_data = []
        participation_data = []
        
        for i, round_data in enumerate(rounds):
            if round_data.winners:
                top_score = round_data.winners[0].get('score', 0.0)
                performance_data.append({
                    "round_id": round_data.round_id,
                    "avg_score": top_score,
                    "max_score": top_score,
                    "min_score": round_data.winners[-1].get('score', 0.0) if len(round_data.winners) > 1 else top_score,
                    "participants": round_data.n_miners
                })
            
            participation_data.append({
                "round_id": round_data.round_id,
                "total_participants": round_data.n_miners,
                "active_participants": round_data.n_winners,
                "completion_rate": round_data.n_winners / round_data.n_miners if round_data.n_miners > 0 else 0
            })
        
        # Generate trends analytics
        trends_data = {
            "score_trend": "increasing" if len(performance_data) > 1 and performance_data[0]["avg_score"] > performance_data[-1]["avg_score"] else "stable",
            "participation_trend": "stable",
            "completion_rate_trend": "stable"
        }
        
        analytics_data = {
            "performance": performance_data,
            "participation": participation_data,
            "trends": trends_data
        }
        
        return AnalyticsResponse(
            success=True,
            analytics=analytics_data
        )
        
    except Exception as e:
        logger.error(f"Error fetching analytics data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics data: {str(e)}")

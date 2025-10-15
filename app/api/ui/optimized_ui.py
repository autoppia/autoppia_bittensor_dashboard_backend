"""
Optimized UI endpoints using the new data builder for better performance.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from app.models.ui import (
    OverviewResponse, LeaderboardResponse, AgentsListResponse, 
    MinerDetailsResponse, AgentRunDetailsResponse, TaskDetailsResponse,
    AnalyticsResponse
)
from app.services.ui.optimized_data_builder import OptimizedDataBuilder
from app.services.cache import cached, CACHE_TTL
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ui/optimized", tags=["ui-optimized"])


@router.get("/overview", response_model=OverviewResponse)
@cached("optimized_overview", CACHE_TTL["overview_metrics"])
async def get_optimized_overview():
    """
    Get optimized overview dashboard data with minimal data loading.
    """
    try:
        logger.info("Fetching optimized overview dashboard data")
        
        # Get lightweight metrics
        metrics = await OptimizedDataBuilder.get_overview_metrics()
        
        # Get recent rounds summary (only essential fields)
        recent_rounds = await OptimizedDataBuilder.get_rounds_summary(limit=5, skip=0)
        
        # Calculate overview metrics
        current_top_score = metrics["top_score"]
        
        # Generate chart data (simplified)
        chart_data = {
            "scores": [
                {"day": i, "score": current_top_score + (i * 0.1), "timestamp": 0, "date": f"2024-01-{i:02d}", "formatted_date": f"Jan {i}"}
                for i in range(1, 8)
            ]
        }
        
        # Generate live events from recent rounds
        live_events = []
        if recent_rounds:
            latest_round = recent_rounds[0]
            if latest_round.get("winners"):
                top_winner = latest_round["winners"][0]
                live_events.append({
                    "type": "round_completed",
                    "validator_round_id": latest_round["validator_round_id"],
                    "top_miner_uid": top_winner.get("miner_uid", 0),
                    "top_score": top_winner.get("score", 0.0),
                    "timestamp": latest_round.get("ended_at", latest_round.get("started_at", 0)),
                    "validator_uid": latest_round.get("validators", [{}])[0].get("uid", 0),
                    "message": f"Round {latest_round['validator_round_id']} completed with top score {top_winner.get('score', 0.0):.2f}"
                })
        
        # Generate validator cards from recent rounds
        validator_cards = []
        for round_data in recent_rounds[:3]:
            if round_data.get("validators"):
                validator = round_data["validators"][0]
                validator_cards.append({
                    "validator_uid": validator["uid"],
                    "name": validator.get("name", f"Validator {validator['uid']}"),
                    "hotkey": validator["hotkey"],
                    "logo_url": None,
                    "status_label": "Active",
                    "status_color": "green",
                    "current_task": {"task_id": "current_task", "status": "running"},
                    "metrics": {"rounds_completed": 1, "avg_score": current_top_score},
                    "stake": {"amount": validator["stake"], "currency": "TAO"},
                    "vtrust": validator["vtrust"],
                    "version": 1,
                    "last_activity": round_data.get("ended_at", round_data.get("started_at", 0)),
                    "uptime": 99.9
                })
        
        overview_metrics = {
            "main_chart_data": chart_data,
            "current_top_score": current_top_score,
            "target_score": 10.0,
            "active_validators": metrics["total_validators"],
            "registered_miners": metrics["total_miners"],
            "available_websites": metrics["total_websites"],
            "score_to_win": 10.0,
            "live_events": live_events,
            "validator_cards": validator_cards,
            "last_updated": time.time(),
            "time_range": "7d"
        }
        
        return OverviewResponse(
            overview=overview_metrics
        )
        
    except Exception as e:
        logger.error(f"Error fetching optimized overview data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch overview data: {str(e)}")


@router.get("/leaderboard", response_model=LeaderboardResponse)
@cached("optimized_leaderboard", CACHE_TTL["leaderboard"])
async def get_optimized_leaderboard(
    type: str = Query("rounds", description="Leaderboard type: rounds, miners, or validators"),
    limit: int = Query(10, ge=1, le=100, description="Number of entries to return"),
    skip: int = Query(0, ge=0, description="Number of entries to skip")
):
    """
    Get optimized leaderboard data with efficient queries.
    """
    try:
        logger.info(f"Fetching optimized {type} leaderboard with limit={limit}, skip={skip}")
        
        if type == "rounds":
            # Get rounds summary
            rounds_summary = await OptimizedDataBuilder.get_rounds_summary(limit=limit, skip=skip)
            
            round_entries = []
            for round_data in rounds_summary:
                round_entries.append({
                    "validator_round_id": round_data["validator_round_id"],
                    "validator_uid": round_data.get("validators", [{}])[0].get("uid", 0),
                    "validator_hotkey": round_data.get("validators", [{}])[0].get("hotkey", ""),
                    "started_at": round_data.get("started_at", 0),
                    "ended_at": round_data.get("ended_at"),
                    "n_tasks": round_data.get("n_tasks", 0),
                    "n_miners": round_data.get("n_miners", 0),
                    "n_winners": round_data.get("n_winners", 0),
                    "top_score": round_data.get("top_score", 0.0),
                    "status": round_data.get("status", "completed")
                })
            
            leaderboard_data = {
                "rounds": round_entries,
                "miners": [],
                "validators": []
            }
            
        elif type == "miners":
            # Get miners summary
            miners_summary = await OptimizedDataBuilder.get_miners_summary(limit=limit, skip=skip)
            
            miner_entries = []
            for miner in miners_summary:
                miner_entries.append({
                    "miner_uid": miner["uid"],
                    "total_score": miner["avg_score"] * miner["rounds_won"],
                    "avg_score": miner["avg_score"],
                    "rounds_participated": miner["total_rounds"],
                    "rank": len(miner_entries) + 1
                })
            
            leaderboard_data = {
                "rounds": [],
                "miners": miner_entries,
                "validators": []
            }
            
        elif type == "validators":
            # Get validators summary
            validators_summary = await OptimizedDataBuilder.get_validators_summary(limit=limit, skip=skip)
            
            validator_entries = []
            for validator in validators_summary:
                validator_entries.append({
                    "validator_uid": validator["uid"],
                    "rounds_completed": validator["rounds_participated"],
                    "total_miners_evaluated": validator["total_miners_evaluated"],
                    "avg_miners_per_round": validator["avg_miners_per_round"],
                    "rank": len(validator_entries) + 1
                })
            
            leaderboard_data = {
                "rounds": [],
                "miners": [],
                "validators": validator_entries
            }
        
        else:
            raise HTTPException(status_code=400, detail="Invalid leaderboard type. Must be 'rounds', 'miners', or 'validators'")
        
        # Create proper LeaderboardData structure
        from app.models.ui import LeaderboardData
        
        leaderboard_data_obj = LeaderboardData(
            type=type,
            data=leaderboard_data.get(type, []),
            limit=limit,
            offset=skip,
            sort_by="score" if type == "miners" else "validator_round_id",
            sort_order="desc"
        )
        
        return LeaderboardResponse(
            leaderboard=leaderboard_data_obj
        )
        
    except Exception as e:
        logger.error(f"Error fetching optimized leaderboard data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch leaderboard data: {str(e)}")


@router.get("/agents", response_model=AgentsListResponse)
@cached("optimized_agents", CACHE_TTL["agents_list"])
async def get_optimized_agents_list(
    limit: int = Query(20, ge=1, le=100, description="Number of agents to return"),
    skip: int = Query(0, ge=0, description="Number of agents to skip")
):
    """
    Get optimized list of all agents (miners) with their basic information.
    """
    try:
        logger.info(f"Fetching optimized agents list with limit={limit}, skip={skip}")
        
        # Get miners summary (already aggregated and optimized)
        miners_summary = await OptimizedDataBuilder.get_miners_summary(limit=limit, skip=skip)
        
        # Convert to expected format
        agents_list = []
        for miner in miners_summary:
            agents_list.append({
                "miner_uid": miner["uid"],
                "hotkey": miner["hotkey"],
                "agent_name": miner["agent_name"],
                "agent_image": miner["agent_image"],
                "github": miner["github"],
                "total_rounds": miner["total_rounds"],
                "avg_score": miner["avg_score"],
                "last_activity": miner["last_activity"]
            })
        
        # Create proper AgentsListData structure
        from app.models.ui import AgentsListData
        
        agents_data_obj = AgentsListData(
            list=agents_list,
            total_count=len(agents_list),
            limit=limit,
            offset=skip,
            sort_by="avg_score",
            sort_order="desc"
        )
        
        return AgentsListResponse(
            agents=agents_data_obj
        )
        
    except Exception as e:
        logger.error(f"Error fetching optimized agents list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agents list: {str(e)}")


@router.get("/agents/{miner_uid}", response_model=MinerDetailsResponse)
@cached("optimized_miner_details", CACHE_TTL["agents_list"])
async def get_optimized_miner_details(miner_uid: int):
    """
    Get optimized detailed information about a specific miner/agent.
    """
    try:
        logger.info(f"Fetching optimized details for miner {miner_uid}")
        
        # Get miner from summary collection (if available) or aggregate
        miners_summary = await OptimizedDataBuilder.get_miners_summary(limit=1000, skip=0)
        miner_info = next((m for m in miners_summary if m["uid"] == miner_uid), None)
        
        if not miner_info:
            raise HTTPException(status_code=404, detail=f"Miner {miner_uid} not found")
        
        # Get rounds where this miner participated (lightweight)
        rounds_summary = await OptimizedDataBuilder.get_rounds_summary(limit=100, skip=0)
        
        miner_rounds = []
        validator_cards = []
        
        for round_data in rounds_summary:
            # Check if miner participated in this round
            if round_data.get("winners"):
                for winner in round_data["winners"]:
                    if winner.get("miner_uid") == miner_uid:
                        miner_rounds.append({
                            "validator_round_id": round_data["validator_round_id"],
                            "score": winner.get("score", 0.0),
                            "rank": winner.get("rank", 0),
                            "reward": winner.get("reward", 0.0)
                        })
                        
                        # Add validator info
                        if round_data.get("validators"):
                            validator = round_data["validators"][0]
                            validator_cards.append({
                                "validator_uid": validator["uid"],
                                "hotkey": validator["hotkey"],
                                "stake": validator["stake"],
                                "vtrust": validator["vtrust"],
                                "rounds_with_miner": 1
                            })
                        break
        
        # Calculate overall metrics
        total_score = sum(round_data["score"] for round_data in miner_rounds)
        avg_score = total_score / len(miner_rounds) if miner_rounds else 0.0
        best_score = max(round_data["score"] for round_data in miner_rounds) if miner_rounds else 0.0
        
        miner_details = {
            "miner_uid": miner_info["uid"],
            "hotkey": miner_info["hotkey"],
            "agent_name": miner_info["agent_name"],
            "agent_image": miner_info["agent_image"],
            "github": miner_info["github"],
            "total_rounds": len(miner_rounds),
            "avg_score": avg_score,
            "best_score": best_score,
            "total_score": total_score,
            "last_activity": miner_info["last_activity"]
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
        logger.error(f"Error fetching optimized miner details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch miner details: {str(e)}")


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunDetailsResponse)
@cached("optimized_agent_run_details", CACHE_TTL["agents_list"])
async def get_optimized_agent_run_details(agent_run_id: str):
    """
    Get optimized detailed information about a specific agent evaluation run.
    """
    try:
        logger.info(f"Fetching optimized details for agent run {agent_run_id}")
        
        # Get agent run details (lightweight)
        agent_run_details = await OptimizedDataBuilder.get_agent_run_details(agent_run_id)
        
        if not agent_run_details:
            raise HTTPException(status_code=404, detail=f"Agent run {agent_run_id} not found")
        
        # Get tasks summary (lightweight, no large data)
        tasks_summary = await OptimizedDataBuilder.get_tasks_summary(agent_run_id, limit=100, skip=0)
        
        # Convert to UI format
        agent_run_info = {
            "agent_run_id": agent_run_details["agent_run_id"],
            "validator_round_id": agent_run_details["validator_round_id"],
            "validator_uid": agent_run_details["validator_uid"],
            "miner_uid": agent_run_details["miner_uid"],
            "started_at": agent_run_details["started_at"],
            "ended_at": agent_run_details["ended_at"],
            "elapsed_time": agent_run_details.get("elapsed_sec"),
            "n_tasks_total": agent_run_details.get("tasks_count", 0),
            "n_tasks_completed": agent_run_details.get("solutions_count", 0),
            "n_tasks_failed": agent_run_details.get("tasks_count", 0) - agent_run_details.get("solutions_count", 0),
            "avg_eval_score": agent_run_details.get("avg_eval_score"),
            "avg_execution_time": agent_run_details.get("avg_execution_time"),
            "total_reward": agent_run_details.get("total_reward"),
            "rank": agent_run_details.get("rank"),
            "weight": agent_run_details.get("weight"),
            "status": agent_run_details.get("status", "completed")
        }
        
        # Convert tasks to UI format (lightweight)
        tasks_data = []
        for task in tasks_summary:
            tasks_data.append({
                "task_id": task["task_id"],
                "validator_round_id": task["validator_round_id"],
                "agent_run_id": task["agent_run_id"],
                "scope": task["scope"],
                "is_web_real": task["is_web_real"],
                "web_project_id": task["web_project_id"],
                "url": task["url"],
                "prompt": task["prompt"],
                "html": "",  # Large data excluded
                "clean_html": "",  # Large data excluded
                "interactive_elements": None,  # Large data excluded
                "screenshot": None,  # Large data excluded
                "screenshot_description": None,  # Large data excluded
                "specifications": {}  # Large data excluded
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
        logger.error(f"Error fetching optimized agent run details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent run details: {str(e)}")


@router.get("/tasks/{task_id}", response_model=TaskDetailsResponse)
@cached("optimized_task_details", CACHE_TTL["agents_list"])
async def get_optimized_task_details(task_id: str):
    """
    Get optimized detailed information about a specific task.
    """
    try:
        logger.info(f"Fetching optimized details for task {task_id}")
        
        # Get task details (includes large data only when specifically requested)
        task_details = await OptimizedDataBuilder.get_task_details(task_id)
        
        if not task_details:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        # Get round info (lightweight)
        round_summary = await OptimizedDataBuilder.get_round_summary(task_details["validator_round_id"])
        
        if not round_summary:
            raise HTTPException(status_code=404, detail=f"Round {task_details['validator_round_id']} not found")
        
        # Convert to UI format
        task_info = {
            "task_id": task_details["task_id"],
            "validator_round_id": task_details["validator_round_id"],
            "agent_run_id": task_details["agent_run_id"],
            "scope": task_details["scope"],
            "is_web_real": task_details["is_web_real"],
            "web_project_id": task_details["web_project_id"],
            "url": task_details["url"],
            "prompt": task_details["prompt"],
            "html": task_details.get("html", ""),
            "clean_html": task_details.get("clean_html", ""),
            "interactive_elements": task_details.get("interactive_elements"),
            "screenshot": task_details.get("screenshot"),
            "screenshot_description": task_details.get("screenshot_description"),
            "specifications": task_details.get("specifications", {})
        }
        
        round_info = {
            "validator_round_id": round_summary["validator_round_id"],
            "validator_uid": round_summary.get("validators", [{}])[0].get("uid", 0),
            "validator_hotkey": round_summary.get("validators", [{}])[0].get("hotkey", ""),
            "started_at": round_summary["started_at"],
            "ended_at": round_summary["ended_at"],
            "n_tasks": round_summary["n_tasks"],
            "n_miners": round_summary["n_miners"],
            "status": round_summary["status"]
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
        logger.error(f"Error fetching optimized task details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch task details: {str(e)}")


@router.get("/analytics", response_model=AnalyticsResponse)
@cached("optimized_analytics", CACHE_TTL["overview_metrics"])
async def get_optimized_analytics():
    """
    Get optimized analytics data with efficient aggregation.
    """
    try:
        logger.info("Fetching optimized analytics data")
        
        # Get rounds summary for analytics
        rounds_summary = await OptimizedDataBuilder.get_rounds_summary(limit=50, skip=0)
        
        # Generate performance analytics
        performance_data = []
        participation_data = []
        
        for round_data in rounds_summary:
            if round_data.get("winners"):
                top_score = round_data["winners"][0].get("score", 0.0)
                performance_data.append({
                    "validator_round_id": round_data["validator_round_id"],
                    "avg_score": round_data.get("average_score", 0.0),
                    "max_score": round_data.get("top_score", 0.0),
                    "min_score": round_data["winners"][-1].get("score", 0.0) if len(round_data["winners"]) > 1 else top_score,
                    "participants": round_data.get("n_miners", 0)
                })
            
            participation_data.append({
                "validator_round_id": round_data["validator_round_id"],
                "total_participants": round_data.get("n_miners", 0),
                "active_participants": round_data.get("n_winners", 0),
                "completion_rate": round_data.get("n_winners", 0) / max(round_data.get("n_miners", 1), 1)
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
        logger.error(f"Error fetching optimized analytics data: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics data: {str(e)}")


# Import required models
from app.models.ui import AgentsListData, MinerDetailsData, AgentRunDetailsData, TaskDetailsData

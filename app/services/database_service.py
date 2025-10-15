"""
Database Service Layer

This service provides high-level data access with caching and aggregation
for the UI endpoints. It abstracts the database layer and provides
optimized queries for each screen.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging
from functools import lru_cache
import json
from pathlib import Path

from app.db.mongo import get_db
from app.data import get_validator_metadata
from app.models.schemas import (
    Round, AgentEvaluationRun, TaskExecution, Task,
    ValidatorInfo, MinerInfo, RoundStatus, TaskStatus, EvaluationStatus
)

logger = logging.getLogger(__name__)


class DatabaseService:
    """High-level database service with caching and aggregation."""
    
    def __init__(self):
        self.db = get_db()
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes cache TTL
        self._aggregated_cache = {}
        self._aggregated_cache_ttl = 600  # 10 minutes for aggregated data
    
    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_miner_info(source: Dict[str, Any]) -> Dict[str, Any]:
        """Extract miner metadata from either legacy or new document structures."""
        info = (source or {}).get("miner_info")
        if info:
            return dict(info)

        # Fall back to legacy flat fields
        miner_uid = source.get("miner_uid")
        hotkey = source.get("miner_hotkey") or source.get("hotkey")
        agent_name = source.get("agent_name") or (
            f"Miner {miner_uid}" if miner_uid is not None else "Benchmark Agent"
        )
        return {
            "miner_uid": miner_uid,
            "miner_hotkey": hotkey,
            "agent_name": agent_name,
            "agent_image": source.get("agent_image") or "",
            "github": source.get("github") or "",
            "is_sota": source.get("is_sota", False),
            "description": source.get("description"),
            "provider": source.get("provider"),
        }
    
    def _is_cache_valid(self, cache_key: str, ttl: int) -> bool:
        """Check if cache entry is still valid."""
        if cache_key not in self._cache:
            return False
        
        cache_time, _ = self._cache[cache_key]
        return time.time() - cache_time < ttl
    
    def _get_from_cache(self, cache_key: str, ttl: int) -> Optional[Any]:
        """Get data from cache if valid."""
        if self._is_cache_valid(cache_key, ttl):
            _, data = self._cache[cache_key]
            return data
        return None
    
    def _set_cache(self, cache_key: str, data: Any, ttl: int = None):
        """Set data in cache."""
        if ttl is None:
            ttl = self._cache_ttl
        self._cache[cache_key] = (time.time(), data)
    
    def _clear_cache(self, pattern: str = None):
        """Clear cache entries matching pattern."""
        if pattern is None:
            self._cache.clear()
        else:
            keys_to_remove = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_remove:
                del self._cache[key]
    
    # ============================================================================
    # OVERVIEW SCREEN DATA
    # ============================================================================
    
    async def get_overview_metrics(self, time_range: str = "all") -> Dict[str, Any]:
        """Get aggregated metrics for overview screen."""
        cache_key = f"overview_metrics_{time_range}"
        cached_data = self._get_from_cache(cache_key, self._aggregated_cache_ttl)
        if cached_data:
            return cached_data
        
        try:
            # Calculate time range
            now = time.time()
            start_time = self._get_time_range_start(time_range, now)
            
            # Get main chart data
            main_chart_data = await self._get_main_chart_data(start_time, now)
            
            # Get aggregated metrics
            metrics = await self._get_aggregated_metrics(start_time, now)
            
            # Get validator cards
            validator_cards = await self._get_validator_cards()
            
            # Get live events
            live_events = await self._get_live_events(limit=5)
            
            result = {
                "main_chart_data": main_chart_data,
                "current_top_score": self._calculate_current_top_score(main_chart_data),
                "target_score": 0.95,
                "score_to_win": 0.95,
                "active_validators": metrics["active_validators"],
                "registered_miners": metrics["registered_miners"],
                "available_websites": metrics["available_websites"],
                "live_events": live_events,
                "validator_cards": validator_cards,
                "last_updated": now,
                "time_range": time_range
            }
            
            self._set_cache(cache_key, result, self._aggregated_cache_ttl)
            return result
            
        except Exception as e:
            logger.error(f"Error getting overview metrics: {e}")
            return self._get_fallback_overview_metrics()
    
    async def _get_main_chart_data(self, start_time: float, end_time: float) -> Dict[str, List[Dict[str, Any]]]:
        """Get main chart data with multiple agents for comparison."""
        try:
            # Simplified approach: get agent runs directly
            agent_runs = await self.db.agent_evaluation_runs.find({
                "started_at": {"$gte": start_time, "$lte": end_time}
            }).to_list(1000)
            
            # Group by miner and create time series
            chart_data = {}
            miner_data: Dict[str, List[Dict[str, Any]]] = {}
            
            for run in agent_runs:
                miner_info = self._normalize_miner_info(run)
                miner_uid = miner_info.get("miner_uid")
                is_sota = miner_info.get("is_sota", False)
                
                if is_sota:
                    base_name = miner_info.get("agent_name", "Benchmark Agent")
                    group_key = self._format_agent_name(base_name)
                else:
                    if miner_uid is None:
                        continue
                    group_key = self._format_agent_name(f"miner_{miner_uid}")
                
                if group_key not in miner_data:
                    miner_data[group_key] = []
                
                miner_data[group_key].append({
                    "score": run.get("avg_eval_score", 0.0),
                    "timestamp": run["started_at"],
                    "validator_round_id": run["validator_round_id"]
                })
            
            # Create chart data for each miner
            for agent_name, runs in miner_data.items():
                data_points = []
                
                # Sort by timestamp and create day-based data
                sorted_runs = sorted(runs, key=lambda x: x["timestamp"])
                for i, run in enumerate(sorted_runs):
                    data_points.append({
                        "day": i + 1,
                        "score": round(run["score"], 3),
                        "timestamp": run["timestamp"],
                        "date": f"Day {i + 1}",
                        "formatted_date": self._format_date(run["timestamp"])
                    })
                
                chart_data[agent_name] = data_points
            
            return chart_data
            
        except Exception as e:
            logger.error(f"Error getting main chart data: {e}")
            return self._get_fallback_chart_data()
    
    async def _get_aggregated_metrics(self, start_time: float, end_time: float) -> Dict[str, int]:
        """Get aggregated metrics for overview."""
        try:
            # Get active validators count
            active_validators = await self.db.rounds.count_documents({
                "started_at": {"$gte": start_time}
            })
            
            # Get registered miners count (supporting legacy documents without embedded info)
            registered_miners = {
                uid for uid in self.db.agent_evaluation_runs.distinct("miner_info.miner_uid")
                if uid is not None
            }
            registered_miners.update(
                uid for uid in self.db.agent_evaluation_runs.distinct("miner_uid") if uid is not None
            )
            
            # Get available websites count
            available_websites = self.db.tasks.distinct("website")
            
            return {
                "active_validators": active_validators,
                "registered_miners": len(registered_miners),
                "available_websites": len(available_websites)
            }
            
        except Exception as e:
            logger.error(f"Error getting aggregated metrics: {e}")
            return {
                "active_validators": 0,
                "registered_miners": 0,
                "available_websites": 0
            }
    
    async def _get_validator_cards(self) -> List[Dict[str, Any]]:
        """Get validator cards for overview."""
        try:
            # Get recent rounds and group by validator
            rounds = await self.db.rounds.find({}).sort("started_at", -1).to_list(100)
            
            validator_data = {}
            for round_doc in rounds:
                validator_uid = round_doc["validator_info"]["validator_uid"]
                if validator_uid not in validator_data:
                    validator_data[validator_uid] = {
                        "validator_info": round_doc["validator_info"],
                        "latest_round": round_doc,
                        "total_rounds": 0,
                        "completed_rounds": 0
                    }
                
                validator_data[validator_uid]["total_rounds"] += 1
                if round_doc.get("status") == "completed":
                    validator_data[validator_uid]["completed_rounds"] += 1
            
            validator_cards = []
            for validator_uid, data in list(validator_data.items())[:6]:
                validator_info = data["validator_info"]
                latest_round = data["latest_round"]
                
                # Get current task from latest round
                current_task = None
                if latest_round.get("tasks"):
                    current_task = {
                        "description": latest_round["tasks"][0].get("prompt", "")[:50] + "...",
                        "task_id": latest_round["tasks"][0].get("task_id", "")
                    }
                
                metadata = get_validator_metadata(validator_uid)
                hotkey = metadata.get("hotkey") or validator_info.get("validator_hotkey", "")
                image_path = metadata.get("image")

                validator_cards.append({
                    "validator_uid": validator_info["validator_uid"],
                    "name": metadata.get("name") or self._get_validator_name(validator_info["validator_uid"]),
                    "hotkey": f"{hotkey[:20]}..." if hotkey else "",
                    "logo_url": image_path,
                    "status": self._get_validator_status(latest_round["status"]),
                    "status_label": self._get_validator_status_label(latest_round["status"]),
                    "status_color": self._get_validator_status_color(latest_round["status"]),
                    "current_task": current_task,
                    "metrics": {
                        "avg_score": 0.95,  # Would calculate from actual data
                        "avg_score_percentage": 95,
                        "total_tasks": data["total_rounds"] * 10  # Estimate
                    },
                    "stake": {
                        "amount": 1000000 + (validator_info["validator_uid"] * 100000),
                        "display": f"{1000 + (validator_info['validator_uid'] * 100)}K",
                        "currency": "TAO"
                    },
                    "vtrust": 1.0 - (validator_info["validator_uid"] * 0.01),
                    "version": 7,
                    "last_activity": latest_round["started_at"],
                    "uptime": 0.99
                })
            
            return validator_cards
            
        except Exception as e:
            logger.error(f"Error getting validator cards: {e}")
            return []
    
    async def _get_live_events(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent live events."""
        try:
            # Get completed rounds
            completed_rounds = await self.db.rounds.find({
                "status": "completed"
            }).sort("ended_at", -1).to_list(limit)
            
            events = []
            for round_doc in completed_rounds:
                # Get top performer for this round
                agent_runs = await self.db.agent_evaluation_runs.find({
                    "validator_round_id": round_doc["validator_round_id"]
                }).sort("avg_eval_score", -1).to_list(1)
                
                if agent_runs:
                    top_run = agent_runs[0]
                    miner_info = self._normalize_miner_info(top_run)
                    if miner_info.get("is_sota"):
                        continue
                    top_miner_uid = miner_info.get("miner_uid")
                    if top_miner_uid is None:
                        continue
                    events.append({
                        "type": "round_completed",
                        "validator_round_id": round_doc["validator_round_id"],
                        "top_miner_uid": top_miner_uid,
                        "top_score": top_run["avg_eval_score"],
                        "timestamp": round_doc["ended_at"],
                        "validator_uid": round_doc["validator_info"]["validator_uid"],
                        "message": f"Round {round_doc['validator_round_id']} completed - Top miner {top_miner_uid} scored {top_run['avg_eval_score']:.3f}"
                    })
            
            return events
            
        except Exception as e:
            logger.error(f"Error getting live events: {e}")
            return []
    
    # ============================================================================
    # AGENTS SCREEN DATA
    # ============================================================================
    
    async def get_agents_list(self, limit: int = 50, offset: int = 0, 
                            sort_by: str = "current_rank", sort_order: str = "asc") -> Dict[str, Any]:
        """Get agents list for sidebar."""
        cache_key = f"agents_list_{limit}_{offset}_{sort_by}_{sort_order}"
        cached_data = self._get_from_cache(cache_key, self._cache_ttl)
        if cached_data:
            return cached_data
        
        try:
            # Get all agent runs and group by miner
            agent_runs = await self.db.agent_evaluation_runs.find({}).to_list(1000)
            
            miner_stats = {}
            for run in agent_runs:
                miner_info = self._normalize_miner_info(run)
                if miner_info.get("is_sota"):
                    continue
                miner_uid = miner_info.get("miner_uid")
                if miner_uid is None:
                    continue
                if miner_uid not in miner_stats:
                    miner_stats[miner_uid] = {
                        "miner_info": miner_info,
                        "rounds_participated": 0,
                        "scores": [],
                        "last_activity": 0
                    }
                
                miner_stats[miner_uid]["rounds_participated"] += 1
                if run.get("avg_eval_score"):
                    miner_stats[miner_uid]["scores"].append(run["avg_eval_score"])
                miner_stats[miner_uid]["last_activity"] = max(
                    miner_stats[miner_uid]["last_activity"], 
                    run["started_at"]
                )
            
            # Calculate stats for each miner
            agents = []
            for miner_uid, stats in miner_stats.items():
                avg_score = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0.0
                best_score = max(stats["scores"]) if stats["scores"] else 0.0
                
                miner_info = stats["miner_info"]
                agents.append({
                    "miner_uid": miner_uid,
                    "name": miner_info.get("agent_name", f"Miner {miner_uid}"),
                    "hotkey": miner_info.get("miner_hotkey"),
                    "current_rank": 0,  # Will be set after sorting
                    "current_score": avg_score,
                    "all_time_best": best_score,
                    "rounds_completed": stats["rounds_participated"],
                    "status": "active",
                    "last_activity": stats["last_activity"]
                })
            
            # Sort agents
            reverse = sort_order == "desc"
            if sort_by == "current_rank":
                agents.sort(key=lambda x: x["current_score"], reverse=reverse)
            elif sort_by == "all_time_best":
                agents.sort(key=lambda x: x["all_time_best"], reverse=reverse)
            elif sort_by == "rounds_completed":
                agents.sort(key=lambda x: x["rounds_completed"], reverse=reverse)
            elif sort_by == "current_score":
                agents.sort(key=lambda x: x["current_score"], reverse=reverse)
            
            # Set ranks and apply pagination
            for i, agent in enumerate(agents):
                agent["current_rank"] = i + 1
            
            paginated_agents = agents[offset:offset + limit]
            
            result = {
                "list": paginated_agents,
                "total_count": len(agents),
                "limit": limit,
                "offset": offset,
                "sort_by": sort_by,
                "sort_order": sort_order
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error getting agents list: {e}")
            return self._get_fallback_agents_list()
    
    async def get_miner_details(self, miner_uid: int, time_range: str = "all") -> Dict[str, Any]:
        """Get detailed miner information."""
        cache_key = f"miner_details_{miner_uid}_{time_range}"
        cached_data = self._get_from_cache(cache_key, self._cache_ttl)
        if cached_data:
            return cached_data
        
        try:
            # Get miner info
            miner_run = await self.db.agent_evaluation_runs.find_one({
                "$or": [
                    {"miner_info.miner_uid": miner_uid},
                    {"miner_uid": miner_uid}
                ]
            })
            
            if not miner_run:
                raise ValueError(f"Miner {miner_uid} not found")
            
            # Get miner statistics
            pipeline = [
                {
                    "$match": {
                        "$or": [
                            {"miner_info.miner_uid": miner_uid},
                            {"miner_uid": miner_uid}
                        ]
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_rounds": {"$sum": 1},
                        "avg_score": {"$avg": "$avg_eval_score"},
                        "best_score": {"$max": "$avg_eval_score"},
                        "total_tasks": {"$sum": "$n_tasks_total"},
                        "completed_tasks": {"$sum": "$n_tasks_completed"},
                        "best_rank": {"$min": "$rank"},
                        "total_reward": {"$sum": "$total_reward"}
                    }
                }
            ]
            
            stats_result = await self.db.agent_evaluation_runs.aggregate(pipeline).to_list(1)
            stats = stats_result[0] if stats_result else {}
            
            # Get score trend
            score_trend = await self._get_miner_score_trend(miner_uid, time_range)
            
            # Get validator cards
            validator_cards = await self._get_miner_validator_cards(miner_uid)
            
            miner_info = self._normalize_miner_info(miner_run)

            result = {
                "miner_info": {
                    "miner_uid": miner_uid,
                    "name": miner_info.get("agent_name", f"Miner {miner_uid}"),
                    "hotkey": miner_info.get("miner_hotkey"),
                    "current_rank": stats.get("best_rank", 999),
                    "all_time_best_score": stats.get("best_score", 0.0),
                    "rounds_completed": stats.get("total_rounds", 0),
                    "current_score": stats.get("avg_score", 0.0),
                    "round_best_score": stats.get("best_score", 0.0),
                    "status": "active",
                    "joined_at": time.time() - (365 * 24 * 60 * 60),
                    "last_activity": time.time() - 300
                },
                "score_trend": score_trend,
                "validator_cards": validator_cards,
                "time_range": time_range,
                "last_updated": time.time()
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error getting miner details for {miner_uid}: {e}")
            return self._get_fallback_miner_details(miner_uid)
    
    # ============================================================================
    # AGENT RUNS SCREEN DATA
    # ============================================================================
    
    async def get_agent_run_details(self, agent_run_id: str) -> Dict[str, Any]:
        """Get detailed agent run information."""
        cache_key = f"agent_run_details_{agent_run_id}"
        cached_data = self._get_from_cache(cache_key, self._cache_ttl)
        if cached_data:
            return cached_data
        
        try:
            # Get agent run
            agent_run = await self.db.agent_evaluation_runs.find_one({
                "agent_run_id": agent_run_id
            })
            
            if not agent_run:
                raise ValueError(f"Agent run {agent_run_id} not found")
            
            # Get round info
            round_info = await self.db.rounds.find_one({
                "validator_round_id": agent_run["validator_round_id"]
            })
            
            # Get tasks for this run
            tasks = await self.db.task_executions.find({
                "agent_run_id": agent_run_id
            }).to_list(1000)
            
            # Calculate website scores
            website_scores = await self._calculate_website_scores(tasks)
            
            # Get tasks data
            tasks_data = await self._format_tasks_data(tasks)
            miner_info = self._normalize_miner_info(agent_run)
            
            validator_payload = agent_run.get("validator_info") or {}
            validator_uid = validator_payload.get("validator_uid") or agent_run.get("validator_uid")
            validator_meta = get_validator_metadata(validator_uid) if validator_uid is not None else {
                "uid": validator_uid,
                "name": f"Validator {validator_uid}",
                "hotkey": "",
                "coldkey": "",
                "image": "images/Autoppia.png",
            }
            validator_hotkey = validator_meta.get("hotkey") or validator_payload.get("validator_hotkey", "")

            result = {
                "run_info": {
                    "agent_run_id": agent_run_id,
                    "validator_round_id": agent_run["validator_round_id"],
                    "round_number": self._extract_round_number(agent_run["validator_round_id"]),
                    "status": agent_run["status"],
                    "started_at": agent_run["started_at"],
                    "completed_at": agent_run["ended_at"],
                    "elapsed_time": agent_run["elapsed_sec"]
                },
                "validator_info": {
                    "validator_uid": validator_uid,
                    "validator_name": validator_meta.get("name", self._get_validator_name(validator_uid)),
                    "validator_hotkey": f"{validator_hotkey[:20]}..." if validator_hotkey else "",
                    "validator_coldkey": validator_meta.get("coldkey", ""),
                    "validator_image": validator_meta.get("image"),
                    "version": "v7.2.1",
                    "status": "Running",
                    "stake": 1722000,
                    "stake_display": "1722K",
                    "vtrust": 1.0
                },
                "miner_info": {
                    "miner_uid": miner_info.get("miner_uid"),
                    "miner_name": miner_info.get(
                        "agent_name",
                        f"Miner {miner_info.get('miner_uid', 'benchmark')}"
                    ),
                    "miner_hotkey": (
                        miner_info["miner_hotkey"][:20] + "..."
                        if miner_info.get("miner_hotkey") else None
                    ),
                    "miner_image": miner_info.get(
                        "agent_image",
                        f"https://autoppia.com/logos/miner_{miner_info.get('miner_uid', 'benchmark')}.png"
                    ),
                    "rank": agent_run["rank"],
                    "status": "active"
                },
                "overall_metrics": {
                    "total_tasks": agent_run["n_tasks_total"],
                    "successful_tasks": agent_run["n_tasks_completed"],
                    "failed_tasks": agent_run["n_tasks_failed"],
                    "overall_score": agent_run["avg_eval_score"],
                    "overall_score_percentage": int((agent_run["avg_eval_score"] or 0) * 100),
                    "average_solution_time": agent_run["avg_execution_time"],
                    "total_websites": len(set(task["task"]["website"] for task in tasks)),
                    "success_rate": agent_run["n_tasks_completed"] / agent_run["n_tasks_total"] if agent_run["n_tasks_total"] > 0 else 0
                },
                "website_scores": website_scores,
                "tasks": tasks_data,
                "last_updated": time.time()
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error getting agent run details for {agent_run_id}: {e}")
            return self._get_fallback_agent_run_details(agent_run_id)
    
    # ============================================================================
    # TASKS SCREEN DATA
    # ============================================================================
    
    def _build_task_miner_info(self, miner_info: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize miner information for task-centric responses."""
        info = miner_info or {}
        miner_uid = info.get("miner_uid")
        agent_name = info.get(
            "agent_name",
            f"Miner {miner_uid}" if miner_uid is not None else "Benchmark Agent"
        )
        hotkey = info.get("miner_hotkey")
        image = info.get(
            "agent_image",
            f"/logos/miner_{miner_uid}.png" if miner_uid is not None else "/logos/benchmark_agent.png"
        )
        return {
            "miner_uid": miner_uid,
            "miner_name": agent_name,
            "miner_hotkey": f"{hotkey[:20]}..." if hotkey else None,
            "miner_image": image,
            "rank": None if miner_uid is None else info.get("rank", 1),
            "uid": f"{miner_uid:03d}" if miner_uid is not None else None,
            "status": "benchmark" if miner_uid is None else info.get("status", "active")
        }
    
    async def get_task_details(self, task_id: str) -> Dict[str, Any]:
        """Get detailed task information."""
        cache_key = f"task_details_{task_id}"
        cached_data = self._get_from_cache(cache_key, self._cache_ttl)
        if cached_data:
            return cached_data
        
        try:
            # Get task execution
            task_execution = await self.db.task_executions.find_one({
                "task_id": task_id
            })
            
            if not task_execution:
                raise ValueError(f"Task {task_id} not found")
            
            # Get actions performed
            actions_performed = await self._get_task_actions(task_execution)
            
            validator_payload = task_execution.get("validator_info") or {}
            validator_uid = validator_payload.get("validator_uid") or task_execution.get("validator_uid")
            validator_meta = get_validator_metadata(validator_uid) if validator_uid is not None else {
                "uid": validator_uid,
                "name": f"Validator {validator_uid}",
                "hotkey": "",
                "coldkey": "",
                "image": "images/Autoppia.png",
            }
            validator_hotkey = validator_meta.get("hotkey") or validator_payload.get("validator_hotkey", "")

            result = {
                "task_info": {
                    "task_id": task_id,
                    "task_prompt": task_execution["task"]["prompt"],
                    "website_name": task_execution["task"]["website"],
                    "use_case": task_execution["task"]["use_case"],
                    "score": task_execution["eval_score"] or 0.0,
                    "response_time_seconds": task_execution["execution_time"] or 0,
                    "status": task_execution["status"],
                    "started_at": task_execution["started_at"],
                    "completed_at": task_execution["completed_at"],
                    "difficulty": task_execution["task"].get("difficulty", "medium")
                },
                "round_info": {
                    "round_number": self._extract_round_number(task_execution["validator_round_id"]),
                    "validator_round_id": task_execution["validator_round_id"],
                    "round_status": "Current evaluation round",
                    "started_at": time.time() - 7200,
                    "ended_at": time.time() - 300
                },
                "validator_info": {
                    "validator_uid": validator_uid,
                    "validator_name": validator_meta.get("name", self._get_validator_name(validator_uid)),
                    "validator_hotkey": f"{validator_hotkey[:20]}..." if validator_hotkey else "",
                    "validator_coldkey": validator_meta.get("coldkey", ""),
                    "validator_image": validator_meta.get("image"),
                    "version": "v7.2.1",
                    "status": "Running",
                    "stake": 1722000,
                    "stake_display": "1722K",
                    "vtrust": 1.0
                },
                "miner_info": self._build_task_miner_info(task_execution["miner_info"]),
                "actions_performed": actions_performed,
                "generated_gif": {
                    "gif_url": None,
                    "status": "generating",
                    "estimated_completion": time.time() + 300,
                    "placeholder_text": "Coming Soon"
                },
                "last_updated": time.time()
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Error getting task details for {task_id}: {e}")
            return self._get_fallback_task_details(task_id)
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    def _get_time_range_start(self, time_range: str, now: float) -> float:
        """Get start time for time range."""
        if time_range == "7d":
            return now - (7 * 24 * 60 * 60)
        elif time_range == "15d":
            return now - (15 * 24 * 60 * 60)
        elif time_range == "30d":
            return now - (30 * 24 * 60 * 60)
        else:  # all
            return 0
    
    def _format_agent_name(self, hotkey: str) -> str:
        """Format agent name from hotkey."""
        # Map hotkeys to friendly names
        agent_names = {
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY": "Autoppia",
            "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty": "OpenAI CUA",
            "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy": "Anthropic CUA",
            "5HGjWAeFDfFCWPsjFQdVV2Msvz2XtMktvgocEYSj2FQjYq9c": "Browser Use"
        }
        return agent_names.get(hotkey, f"Agent {hotkey[:8]}")
    
    def _format_date(self, timestamp: float) -> str:
        """Format timestamp to date string."""
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%d/%m")
    
    def _calculate_current_top_score(self, chart_data: Dict[str, List[Dict[str, Any]]]) -> float:
        """Calculate current top score from chart data."""
        max_score = 0.0
        for agent_name, data_points in chart_data.items():
            if data_points:
                latest_score = data_points[-1].get("score", 0.0)
                max_score = max(max_score, latest_score)
        return max_score
    
    def _get_validator_name(self, validator_uid: int) -> str:
        """Get validator name from UID using the canonical directory."""
        return get_validator_metadata(validator_uid)["name"]

    def _get_validator_hotkey(self, validator_uid: int) -> str:
        """Get validator hotkey from UID."""
        return get_validator_metadata(validator_uid)["hotkey"]

    def _get_validator_coldkey(self, validator_uid: int) -> str:
        """Get validator coldkey from UID."""
        return get_validator_metadata(validator_uid)["coldkey"]

    def _get_validator_image(self, validator_uid: int) -> str:
        """Get validator image from UID."""
        return get_validator_metadata(validator_uid)["image"]
    
    def _get_validator_status(self, round_status: str) -> str:
        """Get validator status from round status."""
        status_map = {
            "initializing": "waiting",
            "task_generation": "sending_tasks",
            "task_distribution": "sending_tasks",
            "evaluation": "evaluating",
            "scoring": "evaluating",
            "weight_assignment": "evaluating",
            "completed": "waiting",
            "failed": "waiting"
        }
        return status_map.get(round_status, "waiting")
    
    def _get_validator_status_label(self, round_status: str) -> str:
        """Get validator status label."""
        label_map = {
            "initializing": "Waiting",
            "task_generation": "Sending Tasks",
            "task_distribution": "Sending Tasks",
            "evaluation": "Evaluating",
            "scoring": "Evaluating",
            "weight_assignment": "Evaluating",
            "completed": "Waiting",
            "failed": "Waiting"
        }
        return label_map.get(round_status, "Waiting")
    
    def _get_validator_status_color(self, round_status: str) -> str:
        """Get validator status color."""
        color_map = {
            "initializing": "blue",
            "task_generation": "green",
            "task_distribution": "green",
            "evaluation": "orange",
            "scoring": "orange",
            "weight_assignment": "orange",
            "completed": "blue",
            "failed": "red"
        }
        return color_map.get(round_status, "blue")
    
    def _extract_round_number(self, validator_round_id: str) -> int:
        """Extract round number from round ID."""
        try:
            # Extract number from validator_round_id like "round_1759955216_000"
            parts = validator_round_id.split("_")
            if len(parts) >= 3:
                return int(parts[2])
            return 1
        except:
            return 1
    
    async def _get_miner_score_trend(self, miner_uid: int, time_range: str) -> List[Dict[str, Any]]:
        """Get miner score trend data."""
        try:
            start_time = self._get_time_range_start(time_range, time.time())
            
            pipeline = [
                {
                    "$match": {
                        "$or": [
                            {"miner_info.miner_uid": miner_uid},
                            {"miner_uid": miner_uid}
                        ],
                        "started_at": {"$gte": start_time}
                    }
                },
                {
                    "$sort": {"started_at": 1}
                }
            ]
            
            results = await self.db.agent_evaluation_runs.aggregate(pipeline).to_list(100)
            
            data_points = []
            for i, result in enumerate(results):
                data_points.append({
                    "day": i + 1,
                    "score": round(result["avg_eval_score"], 3),
                    "timestamp": result["started_at"],
                    "date": f"Day {i + 1}",
                    "formatted_date": self._format_date(result["started_at"])
                })
            
            return data_points
            
        except Exception as e:
            logger.error(f"Error getting miner score trend: {e}")
            return []
    
    async def _get_miner_validator_cards(self, miner_uid: int) -> List[Dict[str, Any]]:
        """Get validator cards for a specific miner."""
        try:
            pipeline = [
                {
                    "$match": {
                        "$or": [
                            {"miner_info.miner_uid": miner_uid},
                            {"miner_uid": miner_uid}
                        ]
                    }
                },
                {
                    "$sort": {"started_at": -1}
                },
                {
                    "$limit": 6
                }
            ]
            
            results = await self.db.agent_evaluation_runs.aggregate(pipeline).to_list(6)
            
            validator_cards = []
            for result in results:
                validator_payload = result.get("validator_info") or {}
                validator_uid = validator_payload.get("validator_uid") or result.get("validator_uid")
                validator_meta = get_validator_metadata(validator_uid) if validator_uid is not None else {
                    "uid": validator_uid,
                    "name": f"Validator {validator_uid}",
                    "hotkey": "",
                    "coldkey": "",
                    "image": "images/Autoppia.png",
                }
                validator_hotkey = validator_meta.get("hotkey") or validator_payload.get("validator_hotkey", "")

                validator_cards.append({
                    "validator_uid": validator_uid,
                    "validator_name": validator_meta.get("name", self._get_validator_name(validator_uid)),
                    "validator_image": validator_meta.get("image"),
                    "validator_hotkey": f"{validator_hotkey[:20]}..." if validator_hotkey else "",
                    "agent_run_id": result["agent_run_id"],
                    "score": result["avg_eval_score"],
                    "stake": 1000000 + (result["validator_info"]["validator_uid"] * 100000),
                    "stake_display": f"{1000 + (result['validator_info']['validator_uid'] * 100)}K",
                    "vtrust": 1.0 - (result["validator_info"]["validator_uid"] * 0.01),
                    "miner_uid": miner_uid,
                    "is_winner": result["rank"] == 1 if result["rank"] else False,
                    "validator_round_id": result["validator_round_id"],
                    "completed_at": result["ended_at"],
                    "rank": result["rank"]
                })
            
            return validator_cards
            
        except Exception as e:
            logger.error(f"Error getting miner validator cards: {e}")
            return []
    
    async def _calculate_website_scores(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Calculate website scores from tasks."""
        try:
            website_stats = {}
            
            for task in tasks:
                website = task["task"]["website"]
                if website not in website_stats:
                    website_stats[website] = {
                        "total_requests": 0,
                        "successful_requests": 0,
                        "total_score": 0.0,
                        "tasks": []
                    }
                
                website_stats[website]["total_requests"] += 1
                website_stats[website]["total_score"] += task["eval_score"] or 0.0
                website_stats[website]["tasks"].append(task)
                
                if (task["eval_score"] or 0.0) >= 0.7:  # Consider 0.7+ as successful
                    website_stats[website]["successful_requests"] += 1
            
            website_scores = []
            for website, stats in website_stats.items():
                success_rate = stats["successful_requests"] / stats["total_requests"] if stats["total_requests"] > 0 else 0
                avg_score = stats["total_score"] / stats["total_requests"] if stats["total_requests"] > 0 else 0
                
                website_scores.append({
                    "website_name": website,
                    "website_display_name": self._format_website_name(website),
                    "description": self._get_website_description(website),
                    "success_rate": success_rate,
                    "success_rate_percentage": round(success_rate * 100, 1),
                    "total_requests": stats["total_requests"],
                    "successful_requests": stats["successful_requests"],
                    "failed_requests": stats["total_requests"] - stats["successful_requests"],
                    "average_score": round(avg_score, 2),
                    "difficulty_breakdown": self._calculate_difficulty_breakdown(stats["tasks"]),
                    "color": self._get_website_color(website)
                })
            
            return website_scores
            
        except Exception as e:
            logger.error(f"Error calculating website scores: {e}")
            return []
    
    async def _format_tasks_data(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format tasks data for display."""
        try:
            formatted_tasks = []
            for task in tasks[:10]:  # First 10 tasks
                formatted_tasks.append({
                    "task_id": task["task_id"],
                    "prompt": task["task"]["prompt"],
                    "website": task["task"]["website"],
                    "use_case": task["task"]["use_case"],
                    "score": task["eval_score"] or 0.0,
                    "solution_time": int(task["execution_time"] or 0),
                    "status": task["status"],
                    "difficulty": task["task"].get("difficulty", "medium"),
                    "started_at": task["started_at"],
                    "completed_at": task["completed_at"]
                })
            
            return {
                "tasks": formatted_tasks,
                "pagination": {
                    "total_tasks": len(tasks),
                    "page_size": 10,
                    "current_page": 1,
                    "total_pages": (len(tasks) + 9) // 10,
                    "has_next": len(tasks) > 10,
                    "has_previous": False
                },
                "summary": {
                    "total_tasks": len(tasks),
                    "successful_tasks": sum(1 for task in tasks if (task["eval_score"] or 0.0) >= 0.7),
                    "failed_tasks": sum(1 for task in tasks if (task["eval_score"] or 0.0) < 0.7),
                    "average_score": round(sum(task["eval_score"] or 0.0 for task in tasks) / len(tasks), 3) if tasks else 0.0,
                    "average_solution_time": round(sum(task["execution_time"] or 0 for task in tasks) / len(tasks), 1) if tasks else 0.0
                }
            }
            
        except Exception as e:
            logger.error(f"Error formatting tasks data: {e}")
            return {"tasks": [], "pagination": {}, "summary": {}}
    
    async def _get_task_actions(self, task_execution: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get actions performed during task execution."""
        try:
            web_actions = task_execution.get("web_actions", [])
            actions = []
            
            for i, action in enumerate(web_actions):
                actions.append({
                    "action_id": i + 1,
                    "action_type": action.get("type", "UnknownAction"),
                    "action_name": self._format_action_name(action),
                    "details": action,
                    "timestamp": task_execution["started_at"] + (i * 5),
                    "duration_ms": action.get("duration", 1000),
                    "status": "completed",
                    "order": i + 1
                })
            
            return actions
            
        except Exception as e:
            logger.error(f"Error getting task actions: {e}")
            return []
    
    def _format_action_name(self, action: Dict[str, Any]) -> str:
        """Format action name for display."""
        action_type = action.get("type", "unknown")
        if action_type == "click":
            return "Click element"
        elif action_type == "type":
            return f"Type text: {action.get('text', '')[:20]}..."
        elif action_type == "wait":
            return f"Wait {action.get('duration', 0)}s"
        elif action_type == "navigate":
            return f"Navigate to {action.get('url', '')[:30]}..."
        else:
            return f"{action_type.title()} action"
    
    def _format_website_name(self, website: str) -> str:
        """Format website name for display."""
        website_names = {
            "facebook.com": "AutoCRM",
            "youtube.com": "AutoMail",
            "google.com": "AutoConnect",
            "amazon.com": "Autozone",
            "netflix.com": "AutoDining",
            "twitter.com": "AutoDelivery",
            "instagram.com": "AutoLodge"
        }
        return website_names.get(website, website)
    
    def _get_website_description(self, website: str) -> str:
        """Get website description."""
        descriptions = {
            "facebook.com": "Lightweight CRM for leads, pipelines, and reporting.",
            "youtube.com": "Webmail client for composing, searching, and labeling messages.",
            "google.com": "Professional networking for profiles, jobs, and messaging.",
            "amazon.com": "E-commerce storefront for product search, carts, and checkout.",
            "netflix.com": "Food discovery and ordering with filters and scheduling.",
            "twitter.com": "Delivery portal for orders, tracking, and address management.",
            "instagram.com": "Lodging search and booking with rich filters."
        }
        return descriptions.get(website, f"Website for {website}")
    
    def _calculate_difficulty_breakdown(self, tasks: List[Dict[str, Any]]) -> Dict[str, int]:
        """Calculate difficulty breakdown for tasks."""
        easy = sum(1 for task in tasks if task["task"].get("difficulty", 0.5) < 0.4)
        medium = sum(1 for task in tasks if 0.4 <= task["task"].get("difficulty", 0.5) < 0.7)
        hard = sum(1 for task in tasks if task["task"].get("difficulty", 0.5) >= 0.7)
        
        return {"easy": easy, "medium": medium, "hard": hard}
    
    def _get_website_color(self, website: str) -> str:
        """Get website color for UI."""
        colors = {
            "facebook.com": "red",
            "youtube.com": "orange",
            "google.com": "yellow",
            "amazon.com": "green",
            "netflix.com": "blue",
            "twitter.com": "purple",
            "instagram.com": "teal"
        }
        return colors.get(website, "gray")
    
    # ============================================================================
    # FALLBACK METHODS (for when database fails)
    # ============================================================================
    
    def _get_fallback_overview_metrics(self) -> Dict[str, Any]:
        """Fallback overview metrics when database fails."""
        return {
            "main_chart_data": {
                "Autoppia": [{"day": i, "score": 0.8 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
                "OpenAI CUA": [{"day": i, "score": 0.75 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
                "Anthropic CUA": [{"day": i, "score": 0.78 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
                "Browser Use": [{"day": i, "score": 0.72 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)]
            },
            "current_top_score": 0.9,
            "target_score": 0.95,
            "score_to_win": 0.95,
            "active_validators": 6,
            "registered_miners": 25,
            "available_websites": 7,
            "live_events": [],
            "validator_cards": [],
            "last_updated": time.time(),
            "time_range": "all"
        }
    
    def _get_fallback_chart_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fallback chart data when database fails."""
        return {
            "Autoppia": [{"day": i, "score": 0.8 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
            "OpenAI CUA": [{"day": i, "score": 0.75 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
            "Anthropic CUA": [{"day": i, "score": 0.78 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
            "Browser Use": [{"day": i, "score": 0.72 + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)]
        }
    
    def _get_fallback_agents_list(self) -> Dict[str, Any]:
        """Fallback agents list when database fails."""
        return {
            "list": [
                {
                    "miner_uid": i,
                    "name": f"Miner {i}",
                    "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{i}",
                    "current_rank": i,
                    "current_score": 0.9 - (i * 0.02),
                    "all_time_best": 0.95 - (i * 0.01),
                    "rounds_completed": 20 + (i * 2),
                    "status": "active",
                    "last_activity": time.time() - (i * 300)
                }
                for i in range(1, 7)
            ],
            "total_count": 6,
            "limit": 50,
            "offset": 0,
            "sort_by": "current_rank",
            "sort_order": "asc"
        }
    
    def _get_fallback_miner_details(self, miner_uid: int) -> Dict[str, Any]:
        """Fallback miner details when database fails."""
        return {
            "miner_info": {
                "miner_uid": miner_uid,
                "name": f"Miner {miner_uid}",
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{miner_uid}",
                "current_rank": miner_uid,
                "all_time_best_score": 0.95 + (miner_uid * 0.01),
                "rounds_completed": 20 + (miner_uid * 2),
                "current_score": 0.90 + (miner_uid * 0.01),
                "round_best_score": 0.92 + (miner_uid * 0.01),
                "status": "active",
                "joined_at": time.time() - (365 * 24 * 60 * 60),
                "last_activity": time.time() - (miner_uid * 300)
            },
            "score_trend": [{"day": i, "score": 0.7 + (miner_uid * 0.05) + (i * 0.001), "timestamp": time.time() - (100-i)*86400, "date": f"Day {i}", "formatted_date": f"{i%30+1:02d}/{(i//30)+1:02d}"} for i in range(1, 101)],
            "validator_cards": [],
            "time_range": "all",
            "last_updated": time.time()
        }
    
    def _get_fallback_agent_run_details(self, agent_run_id: str) -> Dict[str, Any]:
        """Fallback agent run details when database fails."""
        validator_meta = get_validator_metadata(124)
        return {
            "run_info": {
                "agent_run_id": agent_run_id,
                "validator_round_id": "round_11",
                "round_number": 11,
                "status": "completed",
                "started_at": time.time() - 3600,
                "completed_at": time.time() - 300,
                "elapsed_time": 3300
            },
            "validator_info": {
                "validator_uid": validator_meta["uid"],
                "validator_name": validator_meta["name"],
                "validator_hotkey": validator_meta["hotkey"],
                "validator_coldkey": validator_meta["coldkey"],
                "validator_image": validator_meta["image"],
                "version": "v7.2.1",
                "status": "Running",
                "stake": 1722000,
                "stake_display": "1722K",
                "vtrust": 1.0
            },
            "miner_info": {
                "miner_uid": 1,
                "miner_name": "Miner 1",
                "miner_hotkey": "5GHrA5gqhWVm1Cp92jXa...",
                "miner_image": "/logos/miner1.png",
                "rank": 1,
                "status": "active"
            },
            "overall_metrics": {
                "total_tasks": 360,
                "successful_tasks": 233,
                "failed_tasks": 127,
                "overall_score": 0.91,
                "overall_score_percentage": 91,
                "average_solution_time": 52.3,
                "total_websites": 3,
                "success_rate": 0.647
            },
            "website_scores": [],
            "tasks": {"tasks": [], "pagination": {}, "summary": {}},
            "last_updated": time.time()
        }
    
    def _get_fallback_task_details(self, task_id: str) -> Dict[str, Any]:
        """Fallback task details when database fails."""
        validator_meta = get_validator_metadata(124)
        return {
            "task_info": {
                "task_id": task_id,
                "task_prompt": "Create a new customer profile with contact information",
                "website_name": "AutoCRM",
                "use_case": "create_customer",
                "score": 0.93,
                "response_time_seconds": 32,
                "status": "completed",
                "started_at": time.time() - 3600,
                "completed_at": time.time() - 3568,
                "difficulty": "easy"
            },
            "round_info": {
                "round_number": 11,
                "validator_round_id": "round_11",
                "round_status": "Current evaluation round",
                "started_at": time.time() - 7200,
                "ended_at": time.time() - 300
            },
            "validator_info": {
                "validator_uid": validator_meta["uid"],
                "validator_name": validator_meta["name"],
                "validator_hotkey": validator_meta["hotkey"],
                "validator_coldkey": validator_meta["coldkey"],
                "validator_image": validator_meta["image"],
                "version": "v7.2.1",
                "status": "Running",
                "stake": 1722000,
                "stake_display": "1722K",
                "vtrust": 1.0
            },
            "miner_info": {
                "miner_uid": 1,
                "miner_name": "Miner 1",
                "miner_hotkey": "5GHrA5gqhWVm1Cp92jXa...",
                "miner_image": "/logos/miner1.png",
                "rank": 1,
                "uid": "001",
                "status": "active"
            },
            "actions_performed": [],
            "generated_gif": {
                "gif_url": None,
                "status": "generating",
                "estimated_completion": time.time() + 300,
                "placeholder_text": "Coming Soon"
            },
            "last_updated": time.time()
        }


# Global database service instance
_db_service: Optional[DatabaseService] = None


def get_database_service() -> DatabaseService:
    """Get the global database service instance."""
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service

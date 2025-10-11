"""
Optimized Data Builder Service

This service provides efficient data access patterns optimized for UI endpoints.
It separates concerns between summary data and detailed data, implements proper
pagination, and reduces object sizes significantly.
"""
from typing import List, Optional, Dict, Any, Tuple
from app.models.schemas import (
    Round, AgentEvaluationRun, Task, TaskSolution, EvaluationResult
)
from app.db.mock_mongo import get_mock_db
import logging

logger = logging.getLogger(__name__)


class OptimizedDataBuilder:
    """Optimized data builder with efficient query patterns."""
    
    @staticmethod
    async def get_rounds_summary(limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight round summaries for UI endpoints.
        Only loads essential fields needed for lists and overviews.
        """
        db = get_mock_db()
        
        # Get all rounds and filter fields in Python (mock DB doesn't support projection)
        rounds_docs = await db.rounds.find({}).sort("round_id", -1).skip(skip).limit(limit).to_list()
        
        # Filter to essential fields
        filtered_rounds = []
        for doc in rounds_docs:
            filtered_doc = {
                "round_id": doc.get("round_id"),
                "started_at": doc.get("started_at"),
                "ended_at": doc.get("ended_at"),
                "n_tasks": doc.get("n_tasks"),
                "n_miners": doc.get("n_miners"),
                "n_winners": doc.get("n_winners"),
                "average_score": doc.get("average_score"),
                "top_score": doc.get("top_score"),
                "status": doc.get("status"),
                "validators": doc.get("validators", []),
                "winners": doc.get("winners", [])
            }
            filtered_rounds.append(filtered_doc)
        
        rounds_docs = filtered_rounds
        
        return rounds_docs
    
    @staticmethod
    async def get_round_summary(round_id: str) -> Optional[Dict[str, Any]]:
        """
        Get lightweight round summary for a specific round.
        """
        db = get_mock_db()
        
        round_doc = await db.rounds.find_one({"round_id": round_id})
        
        if round_doc:
            # Filter to essential fields
            round_doc = {
                "round_id": round_doc.get("round_id"),
                "started_at": round_doc.get("started_at"),
                "ended_at": round_doc.get("ended_at"),
                "n_tasks": round_doc.get("n_tasks"),
                "n_miners": round_doc.get("n_miners"),
                "n_winners": round_doc.get("n_winners"),
                "average_score": round_doc.get("average_score"),
                "top_score": round_doc.get("top_score"),
                "status": round_doc.get("status"),
                "validators": round_doc.get("validators", []),
                "winners": round_doc.get("winners", [])
            }
        
        return round_doc
    
    @staticmethod
    async def get_round_miners_summary(round_id: str, limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight miner summaries for a round.
        """
        db = get_mock_db()
        
        # Get round to access winners data
        round_doc = await db.rounds.find_one({"round_id": round_id})
        
        if not round_doc:
            return []
        
        # Build miner summaries from round data
        miners_summary = []
        winners_map = {w.get('miner_uid'): w for w in round_doc.get('winners', [])}
        
        for miner in round_doc.get('miners', []):
            miner_uid = miner['uid']
            winner_data = winners_map.get(miner_uid, {})
            
            miners_summary.append({
                "uid": miner_uid,
                "hotkey": miner['hotkey'],
                "agent_name": miner.get('agent_name', f"Agent {miner_uid}"),
                "score": winner_data.get('score', 0.0),
                "rank": winner_data.get('rank'),
                "success": winner_data.get('score', 0.0) > 0.5
            })
        
        # Sort by score descending
        miners_summary.sort(key=lambda x: x['score'], reverse=True)
        
        return miners_summary[skip:skip + limit]
    
    @staticmethod
    async def get_round_validators_summary(round_id: str) -> List[Dict[str, Any]]:
        """
        Get lightweight validator summaries for a round.
        """
        db = get_mock_db()
        
        round_doc = await db.rounds.find_one({"round_id": round_id})
        
        if not round_doc:
            return []
        
        validators_summary = []
        for validator in round_doc.get('validators', []):
            validators_summary.append({
                "uid": validator['uid'],
                "name": validator.get('name', f"Validator {validator['uid']}"),
                "hotkey": validator['hotkey'],
                "stake": validator['stake'],
                "vtrust": validator['vtrust'],
                "version": validator.get('version', '1.0.0'),
                "total_tasks": round_doc['n_tasks']
            })
        
        return validators_summary
    
    @staticmethod
    async def get_agent_runs_summary(round_id: str, limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight agent run summaries for a round.
        """
        db = get_mock_db()
        
        agent_runs_docs = await db.agent_evaluation_runs.find({"round_id": round_id}).skip(skip).limit(limit).to_list()
        
        # Filter to essential fields
        filtered_agent_runs = []
        for doc in agent_runs_docs:
            filtered_doc = {
                "agent_run_id": doc.get("agent_run_id"),
                "miner_uid": doc.get("miner_uid"),
                "validator_uid": doc.get("validator_uid"),
                "started_at": doc.get("started_at"),
                "ended_at": doc.get("ended_at"),
                "avg_eval_score": doc.get("avg_eval_score"),
                "rank": doc.get("rank"),
                "weight": doc.get("weight")
            }
            filtered_agent_runs.append(filtered_doc)
        
        agent_runs_docs = filtered_agent_runs
        
        return agent_runs_docs
    
    @staticmethod
    async def get_agent_run_details(agent_run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed agent run data when specifically requested.
        """
        db = get_mock_db()
        
        # Get agent run
        agent_run_doc = await db.agent_evaluation_runs.find_one(
            {"agent_run_id": agent_run_id}
        )
        
        if not agent_run_doc:
            return None
        
        # Get tasks count and summary (not full task data)
        tasks_count = await db.tasks.count_documents({"agent_run_id": agent_run_id})
        
        # Get task solutions count
        solutions_count = await db.task_solutions.count_documents({"agent_run_id": agent_run_id})
        
        # Get evaluation results count
        evaluations_count = await db.evaluation_results.count_documents({"agent_run_id": agent_run_id})
        
        return {
            **agent_run_doc,
            "tasks_count": tasks_count,
            "solutions_count": solutions_count,
            "evaluations_count": evaluations_count
        }
    
    @staticmethod
    async def get_tasks_summary(agent_run_id: str, limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight task summaries for an agent run.
        Excludes large fields like html, screenshot, etc.
        """
        db = get_mock_db()
        
        tasks_docs = await db.tasks.find({"agent_run_id": agent_run_id}).skip(skip).limit(limit).to_list()
        
        # Filter to essential fields (exclude large data)
        filtered_tasks = []
        for doc in tasks_docs:
            filtered_doc = {
                "task_id": doc.get("task_id"),
                "round_id": doc.get("round_id"),
                "agent_run_id": doc.get("agent_run_id"),
                "url": doc.get("url"),
                "prompt": doc.get("prompt"),
                "scope": doc.get("scope"),
                "is_web_real": doc.get("is_web_real"),
                "web_project_id": doc.get("web_project_id"),
                "should_record": doc.get("should_record")
                # Excluded: html, clean_html, screenshot, interactive_elements, etc.
            }
            filtered_tasks.append(filtered_doc)
        
        tasks_docs = filtered_tasks
        
        return tasks_docs
    
    @staticmethod
    async def get_task_details(task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full task details when specifically requested.
        """
        db = get_mock_db()
        
        task_doc = await db.tasks.find_one({"task_id": task_id})
        return task_doc
    
    @staticmethod
    async def get_task_solutions_summary(agent_run_id: str, limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight task solution summaries.
        Excludes large fields like recording data.
        """
        db = get_mock_db()
        
        solutions_docs = await db.task_solutions.find({"agent_run_id": agent_run_id}).skip(skip).limit(limit).to_list()
        
        # Filter to essential fields (exclude large data)
        filtered_solutions = []
        for doc in solutions_docs:
            filtered_doc = {
                "solution_id": doc.get("solution_id"),
                "task_id": doc.get("task_id"),
                "round_id": doc.get("round_id"),
                "agent_run_id": doc.get("agent_run_id"),
                "miner_uid": doc.get("miner_uid"),
                "validator_uid": doc.get("validator_uid"),
                "web_agent_id": doc.get("web_agent_id"),
                "actions": doc.get("actions", [])
                # Excluded: recording (large binary data)
            }
            filtered_solutions.append(filtered_doc)
        
        solutions_docs = filtered_solutions
        
        return solutions_docs
    
    @staticmethod
    async def get_evaluation_results_summary(agent_run_id: str, limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight evaluation result summaries.
        Excludes large fields like execution_history, gif_recording.
        """
        db = get_mock_db()
        
        results_docs = await db.evaluation_results.find({"agent_run_id": agent_run_id}).skip(skip).limit(limit).to_list()
        
        # Filter to essential fields (exclude large data)
        filtered_results = []
        for doc in results_docs:
            filtered_doc = {
                "evaluation_id": doc.get("evaluation_id"),
                "task_id": doc.get("task_id"),
                "task_solution_id": doc.get("task_solution_id"),
                "round_id": doc.get("round_id"),
                "agent_run_id": doc.get("agent_run_id"),
                "miner_uid": doc.get("miner_uid"),
                "validator_uid": doc.get("validator_uid"),
                "final_score": doc.get("final_score"),
                "raw_score": doc.get("raw_score"),
                "evaluation_time": doc.get("evaluation_time"),
                "web_agent_id": doc.get("web_agent_id")
                # Excluded: execution_history, gif_recording, test_results_matrix
            }
            filtered_results.append(filtered_doc)
        
        results_docs = filtered_results
        
        return results_docs
    
    @staticmethod
    async def get_miners_summary(limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight miner summaries across all rounds.
        """
        db = get_mock_db()
        
        # Aggregate miner data from rounds
        pipeline = [
            {"$unwind": "$miners"},
            {"$unwind": {"path": "$winners", "preserveNullAndEmptyArrays": True}},
            {
                "$group": {
                    "_id": "$miners.uid",
                    "hotkey": {"$first": "$miners.hotkey"},
                    "agent_name": {"$first": "$miners.agent_name"},
                    "agent_image": {"$first": "$miners.agent_image"},
                    "github": {"$first": "$miners.github"},
                    "total_rounds": {"$sum": 1},
                    "total_score": {"$sum": {"$cond": [{"$eq": ["$winners.miner_uid", "$miners.uid"]}, "$winners.score", 0]}},
                    "rounds_won": {"$sum": {"$cond": [{"$eq": ["$winners.miner_uid", "$miners.uid"]}, 1, 0]}},
                    "last_activity": {"$max": "$ended_at"}
                }
            },
            {
                "$addFields": {
                    "avg_score": {"$cond": [{"$gt": ["$rounds_won", 0]}, {"$divide": ["$total_score", "$rounds_won"]}, 0]}
                }
            },
            {"$sort": {"avg_score": -1}},
            {"$skip": skip},
            {"$limit": limit}
        ]
        
        result = await db.rounds.aggregate(pipeline).to_list()
        
        # Convert to expected format
        miners_summary = []
        for doc in result:
            miners_summary.append({
                "uid": doc["_id"],
                "hotkey": doc["hotkey"],
                "agent_name": doc.get("agent_name", f"Agent {doc['_id']}"),
                "agent_image": doc.get("agent_image", ""),
                "github": doc.get("github", ""),
                "total_rounds": doc["total_rounds"],
                "avg_score": doc["avg_score"],
                "rounds_won": doc["rounds_won"],
                "last_activity": doc["last_activity"]
            })
        
        return miners_summary
    
    @staticmethod
    async def get_validators_summary(limit: int = 20, skip: int = 0) -> List[Dict[str, Any]]:
        """
        Get lightweight validator summaries across all rounds.
        """
        db = get_mock_db()
        
        # Aggregate validator data from rounds
        pipeline = [
            {"$unwind": "$validators"},
            {
                "$group": {
                    "_id": "$validators.uid",
                    "name": {"$first": "$validators.name"},
                    "hotkey": {"$first": "$validators.hotkey"},
                    "stake": {"$first": "$validators.stake"},
                    "vtrust": {"$first": "$validators.vtrust"},
                    "version": {"$first": "$validators.version"},
                    "rounds_participated": {"$sum": 1},
                    "total_tasks": {"$sum": "$n_tasks"},
                    "last_activity": {"$max": "$ended_at"}
                }
            },
            {
                "$addFields": {
                    "avg_tasks_per_round": {"$divide": ["$total_tasks", "$rounds_participated"]}
                }
            },
            {"$sort": {"stake": -1}},
            {"$skip": skip},
            {"$limit": limit}
        ]
        
        result = await db.rounds.aggregate(pipeline).to_list()
        
        # Convert to expected format
        validators_summary = []
        for doc in result:
            validators_summary.append({
                "uid": doc["_id"],
                "name": doc.get("name", f"Validator {doc['_id']}"),
                "hotkey": doc["hotkey"],
                "stake": doc["stake"],
                "vtrust": doc["vtrust"],
                "version": doc.get("version", "1.0.0"),
                "rounds_participated": doc["rounds_participated"],
                "total_tasks": doc["total_tasks"],
                "avg_tasks_per_round": doc["avg_tasks_per_round"],
                "last_activity": doc["last_activity"]
            })
        
        return validators_summary
    
    @staticmethod
    async def get_overview_metrics() -> Dict[str, Any]:
        """
        Get lightweight overview metrics for dashboard.
        """
        db = get_mock_db()
        
        # Get basic counts
        total_rounds = await db.rounds.count_documents({})
        
        # Get unique validators and miners
        unique_validators = db.rounds.distinct("validators.uid")
        unique_miners = db.rounds.distinct("miners.uid")
        
        # Get latest round stats
        latest_round = await db.rounds.find_one({})
        
        top_score = 0.0
        subnet_version = "1.0.0"
        
        if latest_round:
            if latest_round.get("winners"):
                top_score = latest_round["winners"][0].get("score", 0.0)
            
            if latest_round.get("validators"):
                subnet_version = latest_round["validators"][0].get("version", "1.0.0")
        
        return {
            "total_rounds": total_rounds,
            "total_validators": len(unique_validators),
            "total_miners": len(unique_miners),
            "top_score": top_score,
            "subnet_version": subnet_version,
            "total_websites": 11  # Mock value
        }

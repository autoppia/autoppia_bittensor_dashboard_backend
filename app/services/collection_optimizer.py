"""
Collection Optimizer Service

This service provides optimized collection designs and indexing strategies
to improve performance and reduce data duplication.
"""
from typing import Dict, List, Any, Optional
from app.db.mock_mongo import get_mock_db
import logging

logger = logging.getLogger(__name__)


class CollectionOptimizer:
    """Service for optimizing collection design and data storage."""
    
    @staticmethod
    async def create_optimized_indexes():
        """
        Create optimized indexes for better query performance.
        Note: Mock database doesn't support indexes, so this is a no-op for development.
        """
        db = get_mock_db()
        
        # Mock database doesn't support indexes, so we'll just log this
        logger.info("Mock database - skipping index creation (not supported)")
        logger.info("In production, the following indexes would be created:")
        logger.info("- rounds: validator_round_id (unique), started_at, ended_at, status, validators.uid")
        logger.info("- agent_evaluation_runs: agent_run_id (unique), validator_round_id, miner_uid, validator_uid")
        logger.info("- tasks: task_id (unique), validator_round_id, agent_run_id, url")
        logger.info("- task_solutions: solution_id (unique), task_id, validator_round_id, agent_run_id, miner_uid")
        logger.info("- evaluation_results: evaluation_id (unique), task_id, task_solution_id, validator_round_id, agent_run_id, miner_uid, final_score")
    
    @staticmethod
    async def optimize_rounds_collection():
        """
        Optimize rounds collection by removing redundant data and adding computed fields.
        """
        db = get_mock_db()
        
        # Add computed fields to rounds for faster queries
        rounds = await db.rounds.find({}).to_list()
        
        for round_doc in rounds:
            # Calculate computed fields
            validator_round_id = round_doc["validator_round_id"]
            
            # Count agent runs for this round
            agent_runs_count = await db.agent_evaluation_runs.count_documents({"validator_round_id": validator_round_id})
            
            # Count tasks for this round
            tasks_count = await db.tasks.count_documents({"validator_round_id": validator_round_id})
            
            # Calculate average score from winners
            average_score = 0.0
            top_score = 0.0
            if round_doc.get("winners"):
                scores = [w.get("score", 0.0) for w in round_doc["winners"]]
                if scores:
                    average_score = sum(scores) / len(scores)
                    top_score = max(scores)
            
            # Update round with computed fields
            await db.rounds.update_one(
                {"validator_round_id": validator_round_id},
                {
                    "$set": {
                        "agent_runs_count": agent_runs_count,
                        "tasks_count": tasks_count,
                        "average_score": average_score,
                        "top_score": top_score,
                        "computed_at": time.time()
                    }
                }
            )
        
        logger.info(f"Optimized {len(rounds)} rounds with computed fields")
    
    @staticmethod
    async def create_summary_collections():
        """
        Create summary collections for frequently accessed aggregated data.
        """
        db = get_mock_db()
        
        # Create miners summary collection
        await CollectionOptimizer._create_miners_summary()
        
        # Create validators summary collection
        await CollectionOptimizer._create_validators_summary()
        
        # Create rounds summary collection
        await CollectionOptimizer._create_rounds_summary()
        
        logger.info("Created summary collections for optimized queries")
    
    @staticmethod
    async def _create_miners_summary():
        """Create miners summary collection."""
        db = get_mock_db()
        
        # Clear existing summary
        await db.miners_summary.delete_many({})
        
        # Aggregate miner data
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
                    "best_score": {"$max": {"$cond": [{"$eq": ["$winners.miner_uid", "$miners.uid"]}, "$winners.score", 0]}},
                    "last_activity": {"$max": "$ended_at"},
                    "first_seen": {"$min": "$started_at"}
                }
            },
            {
                "$addFields": {
                    "avg_score": {"$cond": [{"$gt": ["$rounds_won", 0]}, {"$divide": ["$total_score", "$rounds_won"]}, 0]},
                    "win_rate": {"$cond": [{"$gt": ["$total_rounds", 0]}, {"$divide": ["$rounds_won", "$total_rounds"]}, 0]}
                }
            },
            {"$sort": {"avg_score": -1}}
        ]
        
        result = await db.rounds.aggregate(pipeline).to_list()
        
        # Insert into summary collection
        if result:
            await db.miners_summary.insert_many(result)
        
        logger.info(f"Created miners summary with {len(result)} entries")
    
    @staticmethod
    async def _create_validators_summary():
        """Create validators summary collection."""
        db = get_mock_db()
        
        # Clear existing summary
        await db.validators_summary.delete_many({})
        
        # Aggregate validator data
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
                    "total_miners_evaluated": {"$sum": "$n_miners"},
                    "last_activity": {"$max": "$ended_at"},
                    "first_seen": {"$min": "$started_at"}
                }
            },
            {
                "$addFields": {
                    "avg_tasks_per_round": {"$divide": ["$total_tasks", "$rounds_participated"]},
                    "avg_miners_per_round": {"$divide": ["$total_miners_evaluated", "$rounds_participated"]}
                }
            },
            {"$sort": {"stake": -1}}
        ]
        
        result = await db.rounds.aggregate(pipeline).to_list()
        
        # Insert into summary collection
        if result:
            await db.validators_summary.insert_many(result)
        
        logger.info(f"Created validators summary with {len(result)} entries")
    
    @staticmethod
    async def _create_rounds_summary():
        """Create rounds summary collection."""
        db = get_mock_db()
        
        # Clear existing summary
        await db.rounds_summary.delete_many({})
        
        # Get rounds with computed fields
        rounds = await db.rounds.find({}, {
            "validator_round_id": 1,
            "started_at": 1,
            "ended_at": 1,
            "n_tasks": 1,
            "n_miners": 1,
            "n_winners": 1,
            "average_score": 1,
            "top_score": 1,
            "status": 1,
            "validators.uid": 1,
            "validators.name": 1,
            "validators.stake": 1,
            "winners": 1
        }).to_list()
        
        # Insert into summary collection
        if rounds:
            await db.rounds_summary.insert_many(rounds)
        
        logger.info(f"Created rounds summary with {len(rounds)} entries")
    
    @staticmethod
    async def separate_large_data():
        """
        Separate large data fields into separate collections for better performance.
        """
        db = get_mock_db()
        
        # Create collections for large data
        await CollectionOptimizer._separate_task_large_data()
        await CollectionOptimizer._separate_solution_large_data()
        await CollectionOptimizer._separate_evaluation_large_data()
        
        logger.info("Separated large data into dedicated collections")
    
    @staticmethod
    async def _separate_task_large_data():
        """Separate large task data (HTML, screenshots) into separate collection."""
        db = get_mock_db()
        
        # Get tasks with large data
        tasks = await db.tasks.find({
            "$or": [
                {"html": {"$exists": True, "$ne": ""}},
                {"screenshot": {"$exists": True, "$ne": None}},
                {"interactive_elements": {"$exists": True, "$ne": None}}
            ]
        }).to_list()
        
        for task in tasks:
            task_id = task["task_id"]
            
            # Extract large data
            large_data = {
                "task_id": task_id,
                "html": task.get("html", ""),
                "clean_html": task.get("clean_html", ""),
                "screenshot": task.get("screenshot"),
                "screenshot_description": task.get("screenshot_description"),
                "interactive_elements": task.get("interactive_elements"),
                "specifications": task.get("specifications", {}),
                "relevant_data": task.get("relevant_data", {})
            }
            
            # Store in separate collection
            await db.task_large_data.update_one(
                {"task_id": task_id},
                {"$set": large_data},
                upsert=True
            )
            
            # Remove large data from main task document
            await db.tasks.update_one(
                {"task_id": task_id},
                {
                    "$unset": {
                        "html": "",
                        "clean_html": "",
                        "screenshot": "",
                        "screenshot_description": "",
                        "interactive_elements": "",
                        "specifications": "",
                        "relevant_data": ""
                    }
                }
            )
        
        logger.info(f"Separated large data for {len(tasks)} tasks")
    
    @staticmethod
    async def _separate_solution_large_data():
        """Separate large solution data (recordings) into separate collection."""
        db = get_mock_db()
        
        # Get solutions with large data
        solutions = await db.task_solutions.find({
            "recording": {"$exists": True, "$ne": None}
        }).to_list()
        
        for solution in solutions:
            solution_id = solution["solution_id"]
            
            # Extract large data
            large_data = {
                "solution_id": solution_id,
                "recording": solution.get("recording")
            }
            
            # Store in separate collection
            await db.solution_large_data.update_one(
                {"solution_id": solution_id},
                {"$set": large_data},
                upsert=True
            )
            
            # Remove large data from main solution document
            await db.task_solutions.update_one(
                {"solution_id": solution_id},
                {"$unset": {"recording": ""}}
            )
        
        logger.info(f"Separated large data for {len(solutions)} solutions")
    
    @staticmethod
    async def _separate_evaluation_large_data():
        """Separate large evaluation data (execution history, GIFs) into separate collection."""
        db = get_mock_db()
        
        # Get evaluations with large data
        evaluations = await db.evaluation_results.find({
            "$or": [
                {"execution_history": {"$exists": True, "$ne": []}},
                {"gif_recording": {"$exists": True, "$ne": None}},
                {"test_results_matrix": {"$exists": True, "$ne": []}}
            ]
        }).to_list()
        
        for evaluation in evaluations:
            evaluation_id = evaluation["evaluation_id"]
            
            # Extract large data
            large_data = {
                "evaluation_id": evaluation_id,
                "execution_history": evaluation.get("execution_history", []),
                "gif_recording": evaluation.get("gif_recording"),
                "test_results_matrix": evaluation.get("test_results_matrix", []),
                "feedback": evaluation.get("feedback")
            }
            
            # Store in separate collection
            await db.evaluation_large_data.update_one(
                {"evaluation_id": evaluation_id},
                {"$set": large_data},
                upsert=True
            )
            
            # Remove large data from main evaluation document
            await db.evaluation_results.update_one(
                {"evaluation_id": evaluation_id},
                {
                    "$unset": {
                        "execution_history": "",
                        "gif_recording": "",
                        "test_results_matrix": "",
                        "feedback": ""
                    }
                }
            )
        
        logger.info(f"Separated large data for {len(evaluations)} evaluations")
    
    @staticmethod
    async def optimize_all():
        """
        Run all optimization steps.
        """
        logger.info("Starting collection optimization...")
        
        # Create indexes
        await CollectionOptimizer.create_optimized_indexes()
        
        # Optimize existing collections
        await CollectionOptimizer.optimize_rounds_collection()
        
        # Separate large data
        await CollectionOptimizer.separate_large_data()
        
        # Create summary collections
        await CollectionOptimizer.create_summary_collections()
        
        logger.info("Collection optimization completed successfully")


# Import time for computed fields
import time

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Any, List, Optional
import logging
import time
from datetime import datetime

from app.api.deps import api_key_auth
from app.services.idempotency import idempotency_guard
from app.db.mongo import get_db
from app.models.schemas import (
    # Core models
    Round, RoundStartRequest, Task, TaskGenerationRequest, TaskDistributionRequest,
    TaskResponse, TaskExecution, EvaluationRequest, ScoringRequest, 
    WeightAssignmentRequest, RoundCompletionRequest,
    
    # Agent evaluation models
    AgentEvaluationRun, BatchTaskResponse, BatchEvaluationRequest,
    
    # Leaderboard models
    LeaderboardQuery, RoundSummary, MinerPerformance,
    
    # Response models
    SuccessResponse, ErrorResponse,
    
    # Enums
    RoundStatus, TaskStatus, EvaluationStatus
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/rounds", tags=["rounds"])


def ok_response(**extras) -> SuccessResponse:
    """Helper function to create success responses."""
    return SuccessResponse(data=extras)


def error_response(error: str, detail: str = None, code: str = None) -> ErrorResponse:
    """Helper function to create error responses."""
    return ErrorResponse(error=error, detail=detail, code=code)


# ============================================================================
# VALIDATOR PIPELINE ENDPOINTS
# ============================================================================

@router.post("/start", response_model=SuccessResponse)
async def start_round(
    payload: RoundStartRequest, 
    token: str = Depends(api_key_auth), 
    idem=Depends(idempotency_guard)
):
    """Start a new round with validator and miner information."""
    db = get_db()
    
    try:
        # Create round document
        round_doc = Round(
            round_id=payload.round_id,
            validator_info=payload.validator_info,
            status=RoundStatus.initializing,
            start_block=payload.start_block,
            start_epoch=payload.start_epoch,
            max_epochs=payload.max_epochs,
            max_blocks=payload.max_blocks,
            n_tasks=payload.n_tasks,
            n_miners=payload.n_miners,
            n_winners=payload.n_winners,
            miners=payload.miners,
            metadata=payload.metadata
        )
        
        # Store in database
        result = await db.rounds.update_one(
            {
                "round_id": payload.round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {"$setOnInsert": round_doc.model_dump()},
            upsert=True
        )
        
        if result.upserted_id:
            logger.info(f"Created new round: {payload.round_id} for validator {payload.validator_info.validator_uid}")
        else:
            logger.info(f"Round already exists: {payload.round_id} for validator {payload.validator_info.validator_uid}")
        
        return ok_response(
            round_id=payload.round_id,
            created=result.upserted_id is not None,
            status=RoundStatus.initializing
        )
        
    except Exception as e:
        logger.error(f"Error starting round {payload.round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start round: {str(e)}")


@router.post("/{round_id}/generate-tasks", response_model=SuccessResponse)
async def generate_tasks(
    round_id: str,
    payload: TaskGenerationRequest,
    token: str = Depends(api_key_auth),
    idem=Depends(idempotency_guard)
):
    """Generate N synthetic tasks for the round."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        # Generate tasks (this would typically call your task generation logic)
        tasks = []
        for i in range(payload.n_tasks):
            task = Task(
                task_id=f"{round_id}_task_{i:04d}",
                prompt=f"Generated task {i+1} for round {round_id}",
                website=f"example{i % 3}.com",  # Rotate through example websites
                web_project=f"project_{i % 2}",  # Rotate through projects
                use_case=f"use_case_{i % 4}",  # Rotate through use cases
                difficulty=0.5 + (i % 5) * 0.1,  # Vary difficulty
                metadata={"generated_at": time.time(), "generator": "synthetic"}
            )
            tasks.append(task)
        
        # Update round with generated tasks
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "tasks": [task.model_dump() for task in tasks],
                    "status": RoundStatus.task_generation,
                    "metadata.generated_tasks_at": time.time()
                }
            }
        )
        
        # Store individual tasks for easier querying
        for task in tasks:
            await db.tasks.update_one(
                {"task_id": task.task_id},
                {"$set": task.model_dump()},
                upsert=True
            )
        
        logger.info(f"Generated {len(tasks)} tasks for round {round_id}")
        return ok_response(
            round_id=round_id,
            tasks_generated=len(tasks),
            task_ids=[task.task_id for task in tasks],
            status=RoundStatus.task_generation
        )
        
    except Exception as e:
        logger.error(f"Error generating tasks for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate tasks: {str(e)}")


@router.post("/{round_id}/distribute-tasks", response_model=SuccessResponse)
async def distribute_tasks(
    round_id: str,
    payload: TaskDistributionRequest,
    token: str = Depends(api_key_auth),
    idem=Depends(idempotency_guard)
):
    """Distribute tasks to miners and create task executions."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        # Get round and task information
        round_doc = await db.rounds.find_one({
            "round_id": round_id,
            "validator_info.validator_uid": payload.validator_info.validator_uid
        })
        
        if not round_doc:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get task details
        tasks = await db.tasks.find({"task_id": {"$in": payload.task_ids}}).to_list(None)
        task_map = {task["task_id"]: task for task in tasks}
        
        # Create task executions for each task-miner combination
        task_executions = []
        agent_runs = {}  # Track agent runs by miner
        
        for task_id in payload.task_ids:
            if task_id not in task_map:
                logger.warning(f"Task {task_id} not found, skipping")
                continue
                
            task = task_map[task_id]
            
            for miner_uid in payload.miner_uids:
                # Find miner info
                miner_info = None
                for miner in round_doc["miners"]:
                    if miner["miner_uid"] == miner_uid:
                        miner_info = miner
                        break
                
                if not miner_info:
                    logger.warning(f"Miner {miner_uid} not found in round, skipping")
                    continue
                
                # Create agent run ID
                agent_run_id = f"{round_id}_{miner_uid}_{task_id}"
                
                # Create task execution
                task_execution = TaskExecution(
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    round_id=round_id,
                    validator_info=payload.validator_info,
                    miner_info=miner_info,
                    task=Task(**task),
                    status=TaskStatus.pending,
                    metadata={"distributed_at": time.time()}
                )
                
                task_executions.append(task_execution)
                
                # Track agent run
                if miner_uid not in agent_runs:
                    agent_runs[miner_uid] = {
                        "agent_run_id": f"{round_id}_{miner_uid}",
                        "miner_info": miner_info,
                        "task_ids": []
                    }
                agent_runs[miner_uid]["task_ids"].append(task_id)
        
        # Store task executions
        for execution in task_executions:
            await db.task_executions.update_one(
                {
                    "task_id": execution.task_id,
                    "miner_info.miner_uid": execution.miner_info.miner_uid,
                    "round_id": round_id
                },
                {"$set": execution.model_dump()},
                upsert=True
            )
        
        # Create agent evaluation runs
        for miner_uid, run_data in agent_runs.items():
            agent_run = AgentEvaluationRun(
                agent_run_id=run_data["agent_run_id"],
                round_id=round_id,
                validator_info=payload.validator_info,
                miner_info=run_data["miner_info"],
                task_ids=run_data["task_ids"],
                n_tasks_total=len(run_data["task_ids"]),
                status=EvaluationStatus.pending,
                metadata={"created_at": time.time()}
            )
            
            await db.agent_evaluation_runs.update_one(
                {"agent_run_id": agent_run.agent_run_id},
                {"$set": agent_run.model_dump()},
                upsert=True
            )
        
        # Update round status
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "status": RoundStatus.task_distribution,
                    "metadata.tasks_distributed_at": time.time()
                }
            }
        )
        
        logger.info(f"Distributed {len(task_executions)} task executions for round {round_id}")
        return ok_response(
            round_id=round_id,
            task_executions_created=len(task_executions),
            agent_runs_created=len(agent_runs),
            status=RoundStatus.task_distribution
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error distributing tasks for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to distribute tasks: {str(e)}")


@router.post("/{round_id}/task-responses", response_model=SuccessResponse)
async def submit_task_responses(
    round_id: str,
    payload: BatchTaskResponse,
    token: str = Depends(api_key_auth)
):
    """Submit task responses from miners."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        processed = 0
        errors = []
        
        for response in payload.responses:
            try:
                # Update task execution with miner response
                await db.task_executions.update_one(
                    {
                        "task_id": response.task_id,
                        "miner_info.miner_uid": response.miner_info.miner_uid,
                        "round_id": round_id
                    },
                    {
                        "$set": {
                            "miner_response": response.response,
                            "received_at": response.received_at,
                            "status": TaskStatus.completed,
                            "metadata.response_received_at": time.time()
                        }
                    }
                )
                processed += 1
                
            except Exception as e:
                errors.append(f"Task {response.task_id} for miner {response.miner_info.miner_uid}: {str(e)}")
        
        logger.info(f"Processed {processed} task responses for round {round_id}")
        return ok_response(
            round_id=round_id,
            responses_processed=processed,
            errors=errors if errors else None
        )
        
    except Exception as e:
        logger.error(f"Error processing task responses for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process task responses: {str(e)}")


@router.post("/{round_id}/evaluate", response_model=SuccessResponse)
async def evaluate_tasks(
    round_id: str,
    payload: EvaluationRequest,
    token: str = Depends(api_key_auth)
):
    """Evaluate task responses and assign scores."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        # Get task executions to evaluate
        task_executions = await db.task_executions.find({
            "_id": {"$in": payload.task_execution_ids}
        }).to_list(None)
        
        evaluated = 0
        errors = []
        
        for execution_doc in task_executions:
            try:
                # Simulate evaluation logic (replace with your actual evaluation)
                eval_score = 0.7 + (hash(execution_doc["task_id"]) % 30) / 100  # 0.7-1.0
                time_score = 0.8 + (hash(execution_doc["miner_info"]["miner_uid"]) % 20) / 100  # 0.8-1.0
                total_score = (eval_score + time_score) / 2
                reward = total_score * 10  # Scale to reward
                
                # Update task execution with evaluation results
                await db.task_executions.update_one(
                    {"_id": execution_doc["_id"]},
                    {
                        "$set": {
                            "eval_score": eval_score,
                            "time_score": time_score,
                            "total_score": total_score,
                            "reward": reward,
                            "evaluation_result": {
                                "correctness": eval_score,
                                "efficiency": time_score,
                                "evaluated_at": time.time()
                            },
                            "status": TaskStatus.completed
                        }
                    }
                )
                evaluated += 1
                
            except Exception as e:
                errors.append(f"Task execution {execution_doc['_id']}: {str(e)}")
        
        # Update round status
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "status": RoundStatus.evaluation,
                    "metadata.evaluation_completed_at": time.time()
                }
            }
        )
        
        logger.info(f"Evaluated {evaluated} task executions for round {round_id}")
        return ok_response(
            round_id=round_id,
            evaluations_completed=evaluated,
            errors=errors if errors else None,
            status=RoundStatus.evaluation
        )
        
    except Exception as e:
        logger.error(f"Error evaluating tasks for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to evaluate tasks: {str(e)}")


@router.post("/{round_id}/score", response_model=SuccessResponse)
async def calculate_scores(
    round_id: str,
    payload: ScoringRequest,
    token: str = Depends(api_key_auth)
):
    """Calculate final scores and rankings for miners."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        # Get all agent evaluation runs for this round
        agent_runs = await db.agent_evaluation_runs.find({
            "round_id": round_id,
            "validator_info.validator_uid": payload.validator_info.validator_uid
        }).to_list(None)
        
        # Calculate aggregated scores for each agent run
        for agent_run in agent_runs:
            # Get all task executions for this agent run
            task_executions = await db.task_executions.find({
                "agent_run_id": agent_run["agent_run_id"]
            }).to_list(None)
            
            if task_executions:
                # Calculate averages
                avg_eval_score = sum(te.get("eval_score", 0) for te in task_executions) / len(task_executions)
                avg_execution_time = sum(te.get("execution_time", 0) for te in task_executions) / len(task_executions)
                total_reward = sum(te.get("reward", 0) for te in task_executions)
                
                # Update agent run
                await db.agent_evaluation_runs.update_one(
                    {"_id": agent_run["_id"]},
                    {
                        "$set": {
                            "avg_eval_score": avg_eval_score,
                            "avg_execution_time": avg_execution_time,
                            "total_reward": total_reward,
                            "n_tasks_completed": len(task_executions),
                            "status": EvaluationStatus.completed
                        }
                    }
                )
        
        # Get updated agent runs and sort by total reward
        updated_agent_runs = await db.agent_evaluation_runs.find({
            "round_id": round_id,
            "validator_info.validator_uid": payload.validator_info.validator_uid
        }).sort("total_reward", -1).to_list(None)
        
        # Assign ranks
        for i, agent_run in enumerate(updated_agent_runs):
            await db.agent_evaluation_runs.update_one(
                {"_id": agent_run["_id"]},
                {"$set": {"rank": i + 1}}
            )
        
        # Update round status
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "status": RoundStatus.scoring,
                    "metadata.scoring_completed_at": time.time()
                }
            }
        )
        
        logger.info(f"Calculated scores for {len(updated_agent_runs)} agent runs in round {round_id}")
        return ok_response(
            round_id=round_id,
            agent_runs_scored=len(updated_agent_runs),
            status=RoundStatus.scoring
        )
        
    except Exception as e:
        logger.error(f"Error calculating scores for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to calculate scores: {str(e)}")


@router.post("/{round_id}/assign-weights", response_model=SuccessResponse)
async def assign_weights(
    round_id: str,
    payload: WeightAssignmentRequest,
    token: str = Depends(api_key_auth)
):
    """Assign final weights to miners based on rankings."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        # Get top K agent runs
        top_agent_runs = await db.agent_evaluation_runs.find({
            "round_id": round_id,
            "validator_info.validator_uid": payload.validator_info.validator_uid
        }).sort("rank", 1).limit(payload.winners[0].get("n_winners", 3)).to_list(None)
        
        # Default weight distribution (exponential decay)
        default_weights = {1: 0.8, 2: 0.15, 3: 0.05}
        weight_dist = payload.weight_distribution or default_weights
        
        # Assign weights
        weights = {}
        for agent_run in top_agent_runs:
            rank = agent_run["rank"]
            weight = weight_dist.get(rank, 0.0)
            
            # Update agent run with weight
            await db.agent_evaluation_runs.update_one(
                {"_id": agent_run["_id"]},
                {"$set": {"weight": weight}}
            )
            
            weights[agent_run["miner_info"]["miner_uid"]] = weight
        
        # Update round with final results
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "weights": weights,
                    "winners": payload.winners,
                    "status": RoundStatus.weight_assignment,
                    "metadata.weights_assigned_at": time.time()
                }
            }
        )
        
        logger.info(f"Assigned weights to {len(weights)} miners in round {round_id}")
        return ok_response(
            round_id=round_id,
            weights_assigned=weights,
            status=RoundStatus.weight_assignment
        )
        
    except Exception as e:
        logger.error(f"Error assigning weights for round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to assign weights: {str(e)}")


@router.post("/{round_id}/complete", response_model=SuccessResponse)
async def complete_round(
    round_id: str,
    payload: RoundCompletionRequest,
    token: str = Depends(api_key_auth)
):
    """Complete a round and finalize all data."""
    db = get_db()
    
    if payload.round_id != round_id:
        raise HTTPException(status_code=400, detail="round_id in URL does not match payload")
    
    try:
        end_time = time.time()
        
        # Update round with completion data
        await db.rounds.update_one(
            {
                "round_id": round_id,
                "validator_info.validator_uid": payload.validator_info.validator_uid
            },
            {
                "$set": {
                    "status": RoundStatus.completed,
                    "ended_at": end_time,
                    "elapsed_sec": end_time - time.time(),  # This should be calculated from start
                    "metadata.completed_at": end_time,
                    "metadata.final_stats": payload.final_stats or {}
                }
            }
        )
        
        logger.info(f"Completed round {round_id}")
        return ok_response(
            round_id=round_id,
            status=RoundStatus.completed,
            completed_at=end_time
        )
        
    except Exception as e:
        logger.error(f"Error completing round {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to complete round: {str(e)}")


# ============================================================================
# LEADERBOARD ENDPOINTS
# ============================================================================

@router.get("/leaderboard/rounds", response_model=SuccessResponse)
async def get_rounds_leaderboard(
    validator_uid: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("started_at", regex="^(started_at|ended_at|n_tasks|n_miners)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    token: str = Depends(api_key_auth)
):
    """Get leaderboard of rounds."""
    db = get_db()
    
    try:
        # Build query
        query = {}
        if validator_uid:
            query["validator_info.validator_uid"] = validator_uid
        
        # Get rounds
        sort_direction = -1 if sort_order == "desc" else 1
        rounds = await db.rounds.find(query).sort(sort_by, sort_direction).skip(offset).limit(limit).to_list(limit)
        
        # Convert to round summaries
        round_summaries = []
        for round_doc in rounds:
            summary = RoundSummary(
                round_id=round_doc["round_id"],
                validator_info=round_doc["validator_info"],
                status=round_doc["status"],
                started_at=round_doc["started_at"],
                ended_at=round_doc.get("ended_at"),
                elapsed_sec=round_doc.get("elapsed_sec"),
                n_tasks=round_doc["n_tasks"],
                n_miners=round_doc["n_miners"],
                n_winners=round_doc["n_winners"],
                winners=round_doc.get("winners"),
                stats=round_doc.get("metadata", {})
            )
            round_summaries.append(summary)
        
        # Get total count
        total_count = await db.rounds.count_documents(query)
        
        return ok_response(
            rounds=round_summaries,
            total_count=total_count,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Error getting rounds leaderboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get rounds leaderboard: {str(e)}")


@router.get("/leaderboard/miners", response_model=SuccessResponse)
async def get_miners_leaderboard(
    validator_uid: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("avg_score", regex="^(avg_score|total_reward|wins)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    token: str = Depends(api_key_auth)
):
    """Get leaderboard of miners."""
    db = get_db()
    
    try:
        # Build query for agent evaluation runs
        query = {}
        if validator_uid:
            query["validator_info.validator_uid"] = validator_uid
        
        # Aggregate miner performance
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": "$miner_info.miner_uid",
                "miner_info": {"$first": "$miner_info"},
                "rounds_participated": {"$sum": 1},
                "total_tasks": {"$sum": "$n_tasks_total"},
                "completed_tasks": {"$sum": "$n_tasks_completed"},
                "avg_score": {"$avg": "$avg_eval_score"},
                "avg_execution_time": {"$avg": "$avg_execution_time"},
                "total_reward": {"$sum": "$total_reward"},
                "wins": {"$sum": {"$cond": [{"$lte": ["$rank", 3]}, 1, 0]}},
                "best_rank": {"$min": "$rank"},
                "recent_performance": {"$push": {
                    "round_id": "$round_id",
                    "rank": "$rank",
                    "score": "$avg_eval_score",
                    "reward": "$total_reward"
                }}
            }},
            {"$sort": {sort_by: -1 if sort_order == "desc" else 1}},
            {"$skip": offset},
            {"$limit": limit}
        ]
        
        results = await db.agent_evaluation_runs.aggregate(pipeline).to_list(limit)
        
        # Convert to miner performance objects
        miner_performances = []
        for result in results:
            performance = MinerPerformance(
                miner_info=result["miner_info"],
                rounds_participated=result["rounds_participated"],
                total_tasks=result["total_tasks"],
                completed_tasks=result["completed_tasks"],
                avg_score=result["avg_score"] or 0.0,
                avg_execution_time=result["avg_execution_time"] or 0.0,
                total_reward=result["total_reward"] or 0.0,
                wins=result["wins"],
                best_rank=result["best_rank"],
                recent_performance=result["recent_performance"][-10:]  # Last 10 rounds
            )
            miner_performances.append(performance)
        
        # Get total count
        total_count = await db.agent_evaluation_runs.count_documents(query)
        
        return ok_response(
            miners=miner_performances,
            total_count=total_count,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Error getting miners leaderboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get miners leaderboard: {str(e)}")


@router.get("/{round_id}/details", response_model=SuccessResponse)
async def get_round_details(
    round_id: str,
    validator_uid: int = Query(...),
    token: str = Depends(api_key_auth)
):
    """Get detailed information about a specific round."""
    db = get_db()
    
    try:
        # Get round
        round_doc = await db.rounds.find_one({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        })
        
        if not round_doc:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get agent evaluation runs
        agent_runs = await db.agent_evaluation_runs.find({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        }).sort("rank", 1).to_list(None)
        
        # Get task executions summary
        task_executions = await db.task_executions.find({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        }).to_list(None)
        
        return ok_response(
            round=round_doc,
            agent_runs=agent_runs,
            task_executions_count=len(task_executions),
            summary={
                "total_tasks": round_doc["n_tasks"],
                "total_miners": round_doc["n_miners"],
                "completed_evaluations": len(agent_runs),
                "status": round_doc["status"]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting round details for {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get round details: {str(e)}")


@router.get("/{round_id}/status", response_model=SuccessResponse)
async def get_round_status(
    round_id: str,
    validator_uid: int = Query(...),
    token: str = Depends(api_key_auth)
):
    """Get current status of a round."""
    db = get_db()
    
    try:
        round_doc = await db.rounds.find_one({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        })
        
        if not round_doc:
            raise HTTPException(status_code=404, detail="Round not found")
        
        # Get counts
        task_executions_count = await db.task_executions.count_documents({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        })
        
        agent_runs_count = await db.agent_evaluation_runs.count_documents({
            "round_id": round_id,
            "validator_info.validator_uid": validator_uid
        })
        
        return ok_response(
            round_id=round_id,
            status=round_doc["status"],
            progress={
                "tasks_generated": len(round_doc.get("tasks", [])),
                "task_executions": task_executions_count,
                "agent_runs": agent_runs_count,
                "miners": len(round_doc.get("miners", []))
            },
            timing={
                "started_at": round_doc["started_at"],
                "ended_at": round_doc.get("ended_at"),
                "elapsed_sec": round_doc.get("elapsed_sec")
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting round status for {round_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get round status: {str(e)}")
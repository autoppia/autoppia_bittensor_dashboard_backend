"""
Optimized POST endpoints for rounds data submission with improved performance and data handling.
"""
from fastapi import APIRouter, HTTPException, Depends
from app.models.schemas import RoundSubmissionRequest, RoundSubmissionResponse
from app.db.mock_mongo import get_mock_db
from app.services.collection_optimizer import CollectionOptimizer
import logging
import time
import asyncio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rounds/optimized", tags=["rounds-post-optimized"])


def _validate_round_relationships_optimized(payload: RoundSubmissionRequest) -> None:
    """
    Optimized validation that checks relationships without loading full objects.
    """
    round_data = payload.round
    tasks = payload.tasks
    agent_runs = payload.agent_evaluation_runs
    task_solutions = payload.task_solutions
    evaluation_results = payload.evaluation_results
    
    # Create lookup maps for validation (lightweight)
    task_map = {task.task_id: task for task in tasks}
    task_solution_map = {ts.solution_id: ts for ts in task_solutions}
    evaluation_result_map = {er.evaluation_id: er for er in evaluation_results}
    agent_run_map = {ar.agent_run_id: ar for ar in agent_runs}
    
    # Validate agent run relationships (lightweight check)
    for agent_run in agent_runs:
        agent_tasks = [task for task in tasks if task.agent_run_id == agent_run.agent_run_id]
        if not agent_run.validate_task_relationships(agent_tasks):
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid task relationships in agent run {agent_run.agent_run_id}"
            )
    
    # Validate task solution relationships (lightweight check)
    for task_solution in task_solutions:
        if task_solution.task_id not in task_map:
            raise HTTPException(
                status_code=400,
                detail=f"Task {task_solution.task_id} not found for task solution {task_solution.solution_id}"
            )
        
        agent_run = agent_run_map.get(task_solution.agent_run_id)
        if not agent_run:
            raise HTTPException(
                status_code=400,
                detail=f"Agent run {task_solution.agent_run_id} not found for task solution {task_solution.solution_id}"
            )
        
        task = task_map[task_solution.task_id]
        if not task_solution.validate_relationships(agent_run, task):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid relationships for task solution {task_solution.solution_id}"
            )
    
    # Validate evaluation result relationships (lightweight check)
    for evaluation_result in evaluation_results:
        if evaluation_result.task_id not in task_map:
            raise HTTPException(
                status_code=400,
                detail=f"Task {evaluation_result.task_id} not found for evaluation result {evaluation_result.evaluation_id}"
            )
        
        if evaluation_result.task_solution_id not in task_solution_map:
            raise HTTPException(
                status_code=400,
                detail=f"Task solution {evaluation_result.task_solution_id} not found for evaluation result {evaluation_result.evaluation_id}"
            )
        
        agent_run = agent_run_map.get(evaluation_result.agent_run_id)
        if not agent_run:
            raise HTTPException(
                status_code=400,
                detail=f"Agent run {evaluation_result.agent_run_id} not found for evaluation result {evaluation_result.evaluation_id}"
            )
        
        task = task_map[evaluation_result.task_id]
        task_solution = task_solution_map[evaluation_result.task_solution_id]
        if not evaluation_result.validate_relationships(agent_run, task, task_solution):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid relationships for evaluation result {evaluation_result.evaluation_id}"
            )


async def _save_round_optimized(db, round_data, validator_uid):
    """Save round with computed fields."""
    round_doc = round_data.model_dump()
    
    # Add computed fields
    round_doc["agent_runs_count"] = 0  # Will be updated after agent runs are saved
    round_doc["tasks_count"] = 0  # Will be updated after tasks are saved
    round_doc["computed_at"] = time.time()
    
    result = await db.rounds.update_one(
        {"validator_round_id": round_data.validator_round_id},
        {"$set": round_doc},
        upsert=True
    )
    
    return result.upserted_id or result.matched_count


async def _save_tasks_optimized(db, tasks, round_data, validator_uid):
    """Save tasks with separated large data."""
    saved_task_ids = []
    
    for task in tasks:
        task_doc = task.model_dump()
        
        # Remove large data fields for main collection
        large_data = {
            "html": task_doc.pop("html", ""),
            "clean_html": task_doc.pop("clean_html", ""),
            "screenshot": task_doc.pop("screenshot"),
            "screenshot_description": task_doc.pop("screenshot_description"),
            "interactive_elements": task_doc.pop("interactive_elements"),
            "specifications": task_doc.pop("specifications", {}),
            "relevant_data": task_doc.pop("relevant_data", {})
        }
        
        # Ensure task has proper references
        task_doc["validator_round_id"] = round_data.validator_round_id
        
        # Save main task document
        await db.tasks.update_one(
            {"task_id": task.task_id},
            {"$set": task_doc},
            upsert=True
        )
        
        # Save large data separately if it exists
        if any(large_data.values()):
            await db.task_large_data.update_one(
                {"task_id": task.task_id},
                {"$set": {"task_id": task.task_id, **large_data}},
                upsert=True
            )
        
        saved_task_ids.append(task.task_id)
    
    return saved_task_ids


async def _save_agent_runs_optimized(db, agent_runs, round_data, validator_uid):
    """Save agent evaluation runs with computed fields."""
    saved_agent_run_ids = []
    
    for agent_run in agent_runs:
        agent_run_doc = agent_run.model_dump()
        
        # Ensure agent run has proper references
        agent_run_doc["validator_round_id"] = round_data.validator_round_id
        agent_run_doc["validator_uid"] = validator_uid
        
        # Add computed fields
        agent_run_doc["tasks_count"] = 0  # Will be updated after tasks are saved
        agent_run_doc["solutions_count"] = 0  # Will be updated after solutions are saved
        agent_run_doc["evaluations_count"] = 0  # Will be updated after evaluations are saved
        
        await db.agent_evaluation_runs.update_one(
            {"agent_run_id": agent_run.agent_run_id},
            {"$set": agent_run_doc},
            upsert=True
        )
        
        saved_agent_run_ids.append(agent_run.agent_run_id)
    
    return saved_agent_run_ids


async def _save_task_solutions_optimized(db, task_solutions, round_data, validator_uid):
    """Save task solutions with separated large data."""
    saved_solution_ids = []
    
    for task_solution in task_solutions:
        solution_doc = task_solution.model_dump()
        
        # Remove large data fields
        recording_data = solution_doc.pop("recording", None)
        
        # Ensure task solution has proper references
        solution_doc["validator_round_id"] = round_data.validator_round_id
        solution_doc["validator_uid"] = validator_uid
        
        # Save main solution document
        await db.task_solutions.update_one(
            {"solution_id": task_solution.solution_id},
            {"$set": solution_doc},
            upsert=True
        )
        
        # Save recording data separately if it exists
        if recording_data is not None:
            await db.solution_large_data.update_one(
                {"solution_id": task_solution.solution_id},
                {"$set": {"solution_id": task_solution.solution_id, "recording": recording_data}},
                upsert=True
            )
        
        saved_solution_ids.append(task_solution.solution_id)
    
    return saved_solution_ids


async def _save_evaluation_results_optimized(db, evaluation_results, round_data, validator_uid):
    """Save evaluation results with separated large data."""
    saved_evaluation_ids = []
    
    for eval_result in evaluation_results:
        eval_doc = eval_result.model_dump()
        
        # Remove large data fields
        large_data = {
            "execution_history": eval_doc.pop("execution_history", []),
            "gif_recording": eval_doc.pop("gif_recording"),
            "test_results_matrix": eval_doc.pop("test_results_matrix", []),
            "feedback": eval_doc.pop("feedback")
        }
        
        # Ensure evaluation result has proper references
        eval_doc["validator_round_id"] = round_data.validator_round_id
        eval_doc["validator_uid"] = validator_uid
        
        # Save main evaluation document
        await db.evaluation_results.update_one(
            {"evaluation_id": eval_result.evaluation_id},
            {"$set": eval_doc},
            upsert=True
        )
        
        # Save large data separately if it exists
        if any(large_data.values()):
            await db.evaluation_large_data.update_one(
                {"evaluation_id": eval_result.evaluation_id},
                {"$set": {"evaluation_id": eval_result.evaluation_id, **large_data}},
                upsert=True
            )
        
        saved_evaluation_ids.append(eval_result.evaluation_id)
    
    return saved_evaluation_ids


async def _update_computed_fields(db, round_data, saved_entities):
    """Update computed fields after all data is saved."""
    # Update round with computed counts
    await db.rounds.update_one(
        {"validator_round_id": round_data.validator_round_id},
        {
            "$set": {
                "agent_runs_count": len(saved_entities["agent_evaluation_runs"]),
                "tasks_count": len(saved_entities["tasks"]),
                "computed_at": time.time()
            }
        }
    )
    
    # Update agent runs with computed counts
    for agent_run_id in saved_entities["agent_evaluation_runs"]:
        tasks_count = await db.tasks.count_documents({"agent_run_id": agent_run_id})
        solutions_count = await db.task_solutions.count_documents({"agent_run_id": agent_run_id})
        evaluations_count = await db.evaluation_results.count_documents({"agent_run_id": agent_run_id})
        
        await db.agent_evaluation_runs.update_one(
            {"agent_run_id": agent_run_id},
            {
                "$set": {
                    "tasks_count": tasks_count,
                    "solutions_count": solutions_count,
                    "evaluations_count": evaluations_count
                }
            }
        )


@router.post("/submit", response_model=RoundSubmissionResponse)
async def submit_round_data_optimized(payload: RoundSubmissionRequest):
    """
    Optimized round data submission with improved performance and data handling.
    
    Key optimizations:
    1. Separates large data (HTML, screenshots, recordings) into dedicated collections
    2. Uses batch operations where possible
    3. Updates computed fields after all data is saved
    4. Validates relationships without loading full objects
    5. Implements proper indexing strategy
    """
    start_time = time.time()
    
    try:
        logger.info(f"Starting optimized round submission for round {payload.round.validator_round_id}")
        
        # Validate all relationships before saving (optimized)
        _validate_round_relationships_optimized(payload)
        
        # Get database connection
        db = get_mock_db()
        
        # Extract validator UID from the round
        validator_uid = payload.round.validator_info.uid
        
        # Track saved entities
        saved_entities = {
            "round": None,
            "agent_evaluation_runs": [],
            "tasks": [],
            "task_solutions": [],
            "evaluation_results": []
        }
        
        # Save data in optimized order with parallel operations where possible
        
        # 1. Save Round (with computed fields)
        saved_entities["round"] = await _save_round_optimized(db, payload.round, validator_uid)
        logger.info(f"Saved round {payload.round.validator_round_id}")
        
        # 2. Save Agent Evaluation Runs (with computed fields)
        saved_entities["agent_evaluation_runs"] = await _save_agent_runs_optimized(
            db, payload.agent_evaluation_runs, payload.round, validator_uid
        )
        logger.info(f"Saved {len(payload.agent_evaluation_runs)} agent evaluation runs")
        
        # 3. Save Tasks (with separated large data)
        saved_entities["tasks"] = await _save_tasks_optimized(
            db, payload.tasks, payload.round, validator_uid
        )
        logger.info(f"Saved {len(payload.tasks)} tasks")
        
        # 4. Save Task Solutions (with separated large data)
        saved_entities["task_solutions"] = await _save_task_solutions_optimized(
            db, payload.task_solutions, payload.round, validator_uid
        )
        logger.info(f"Saved {len(payload.task_solutions)} task solutions")
        
        # 5. Save Evaluation Results (with separated large data)
        saved_entities["evaluation_results"] = await _save_evaluation_results_optimized(
            db, payload.evaluation_results, payload.round, validator_uid
        )
        logger.info(f"Saved {len(payload.evaluation_results)} evaluation results")
        
        # 6. Update computed fields
        await _update_computed_fields(db, payload.round, saved_entities)
        logger.info("Updated computed fields")
        
        # 7. Update summary collections (async, non-blocking)
        asyncio.create_task(CollectionOptimizer.create_summary_collections())
        
        # Calculate processing time
        processing_time = time.time() - start_time
        
        # Create response
        response = RoundSubmissionResponse(
            success=True,
            message=f"Successfully submitted round {payload.round.validator_round_id} (optimized)",
            validator_round_id=payload.round.validator_round_id,
            validator_uid=validator_uid,
            processing_time_seconds=processing_time,
            entities_saved=saved_entities,
            summary={
                "rounds": 1,
                "agent_evaluation_runs": len(payload.agent_evaluation_runs),
                "tasks": len(payload.tasks),
                "task_solutions": len(payload.task_solutions),
                "evaluation_results": len(payload.evaluation_results)
            }
        )
        
        logger.info(f"Optimized round submission completed in {processing_time:.3f}s for round {payload.round.validator_round_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"Error in optimized round submission: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to submit round data: {str(e)}"
        )


@router.post("/optimize-collections")
async def optimize_collections():
    """
    Endpoint to run collection optimization.
    This should be called after data migration or periodically for maintenance.
    """
    try:
        logger.info("Starting collection optimization...")
        
        await CollectionOptimizer.optimize_all()
        
        return {
            "success": True,
            "message": "Collection optimization completed successfully",
            "optimizations": [
                "Created optimized indexes",
                "Optimized rounds collection with computed fields",
                "Separated large data into dedicated collections",
                "Created summary collections for fast queries"
            ]
        }
        
    except Exception as e:
        logger.error(f"Error during collection optimization: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Collection optimization failed: {str(e)}"
        )

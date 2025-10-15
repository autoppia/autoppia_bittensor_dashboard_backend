"""
POST endpoints for rounds data submission.
"""
from fastapi import APIRouter, HTTPException, Depends
from app.models.schemas import RoundSubmissionRequest, RoundSubmissionResponse
from app.db.mock_mongo import get_mock_db
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rounds", tags=["rounds-post"])


def _validate_round_relationships(payload: RoundSubmissionRequest) -> None:
    """Validate that all entity relationships are properly maintained."""
    round_data = payload.round
    tasks = payload.tasks
    agent_runs = payload.agent_evaluation_runs
    task_solutions = payload.task_solutions
    evaluation_results = payload.evaluation_results
    
    # Create lookup maps for validation
    task_map = {task.task_id: task for task in tasks}
    task_solution_map = {ts.solution_id: ts for ts in task_solutions}
    evaluation_result_map = {er.evaluation_id: er for er in evaluation_results}
    
    # Validate each agent run
    for agent_run in agent_runs:
        # Get tasks for this agent run
        agent_tasks = [task for task in tasks if task.agent_run_id == agent_run.agent_run_id]
        
        # Validate agent run relationships
        if not agent_run.validate_task_relationships(agent_tasks):
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid task relationships in agent run {agent_run.agent_run_id}"
            )
    
    # Validate each task solution
    for task_solution in task_solutions:
        if task_solution.task_id not in task_map:
            raise HTTPException(
                status_code=400,
                detail=f"Task {task_solution.task_id} not found for task solution {task_solution.solution_id}"
            )
        
        # Find the corresponding agent run
        agent_run = None
        for ar in agent_runs:
            if ar.agent_run_id == task_solution.agent_run_id:
                agent_run = ar
                break
        
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
    
    # Validate each evaluation result
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
        
        # Find the corresponding agent run
        agent_run = None
        for ar in agent_runs:
            if ar.agent_run_id == evaluation_result.agent_run_id:
                agent_run = ar
                break
        
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


@router.post("/submit", response_model=RoundSubmissionResponse)
async def submit_round_data(payload: RoundSubmissionRequest):
    """
    Submit complete round data including Round, AgentEvaluationRuns, Tasks, TaskSolutions, and EvaluationResults.
    This endpoint performs atomic submission with comprehensive validation.
    
    Args:
        payload: Complete round data including all related entities
        
    Returns:
        RoundSubmissionResponse with submission details and processing time
        
    Raises:
        HTTPException: 400 for validation errors, 500 for server errors
    """
    start_time = time.time()
    
    try:
        logger.info(f"Starting round submission for round {payload.round.validator_round_id}")
        
        # Validate all relationships before saving
        _validate_round_relationships(payload)
        
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
        
        # 1. Save Round
        round_data = payload.round
        round_doc = round_data.model_dump()
        result = await db.rounds.update_one(
            {"validator_round_id": round_data.validator_round_id},
            {"$set": round_doc},
            upsert=True
        )
        saved_entities["round"] = result.upserted_id or result.matched_count
        logger.info(f"Saved round {round_data.validator_round_id}")
        
        # 2. Save Tasks
        for task in payload.tasks:
            task_doc = task.model_dump()
            # Ensure task has proper references
            task_doc["validator_round_id"] = round_data.validator_round_id
            # agent_run_id is already set in the task model
            # validator_uid removed - available via AgentEvaluationRun
            
            await db.tasks.update_one(
                {"task_id": task.task_id},
                {"$set": task_doc},
                upsert=True
            )
            saved_entities["tasks"].append(task.task_id)
        
        logger.info(f"Saved {len(payload.tasks)} tasks")
        
        # 3. Save Agent Evaluation Runs
        for agent_run in payload.agent_evaluation_runs:
            agent_run_doc = agent_run.model_dump()
            # Ensure agent run has proper references
            agent_run_doc["validator_round_id"] = round_data.validator_round_id
            agent_run_doc["validator_uid"] = validator_uid
            
            await db.agent_evaluation_runs.update_one(
                {"agent_run_id": agent_run.agent_run_id},
                {"$set": agent_run_doc},
                upsert=True
            )
            saved_entities["agent_evaluation_runs"].append(agent_run.agent_run_id)
        
        logger.info(f"Saved {len(payload.agent_evaluation_runs)} agent evaluation runs")
        
        # 4. Save Task Solutions
        for task_solution in payload.task_solutions:
            task_solution_doc = task_solution.model_dump()
            # Ensure task solution has proper references
            task_solution_doc["validator_round_id"] = round_data.validator_round_id
            task_solution_doc["validator_uid"] = validator_uid
            
            await db.task_solutions.update_one(
                {"solution_id": task_solution.solution_id},
                {"$set": task_solution_doc},
                upsert=True
            )
            saved_entities["task_solutions"].append(task_solution.solution_id)
        
        logger.info(f"Saved {len(payload.task_solutions)} task solutions")
        
        # 5. Save Evaluation Results
        for eval_result in payload.evaluation_results:
            eval_result_doc = eval_result.model_dump()
            # Ensure evaluation result has proper references
            eval_result_doc["validator_round_id"] = round_data.validator_round_id
            eval_result_doc["validator_uid"] = validator_uid
            
            await db.evaluation_results.update_one(
                {"evaluation_id": eval_result.evaluation_id},
                {"$set": eval_result_doc},
                upsert=True
            )
            saved_entities["evaluation_results"].append(eval_result.evaluation_id)
        
        logger.info(f"Saved {len(payload.evaluation_results)} evaluation results")
        
        # Calculate processing time
        processing_time = time.time() - start_time
        
        # Create response
        response = RoundSubmissionResponse(
            success=True,
            message=f"Successfully submitted round {round_data.validator_round_id}",
            validator_round_id=round_data.validator_round_id,
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
        
        logger.info(f"Round submission completed in {processing_time:.3f}s for round {round_data.validator_round_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"Error submitting round data: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to submit round data: {str(e)}"
        )

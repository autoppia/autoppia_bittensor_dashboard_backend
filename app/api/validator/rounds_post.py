"""
POST endpoints for rounds data submission.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.core import RoundSubmissionRequest, RoundSubmissionResponse
from app.services.validator_storage import RoundPersistenceService
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds-post"])


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
        round_data = payload.round

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
async def submit_round_data(
    payload: RoundSubmissionRequest,
    session: AsyncSession = Depends(get_session),
):
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
        
        round_data = payload.round

        service = RoundPersistenceService(session)
        async with session.begin():
            result = await service.upsert_round_submission(payload)
        
        validator_uid = result.validator_uid
        saved_entities = result.saved_entities
        logger.info(
            "Saved round submission %s (runs=%d, tasks=%d, solutions=%d, evaluations=%d)",
            payload.round.validator_round_id,
            len(saved_entities["agent_evaluation_runs"]),
            len(saved_entities["tasks"]),
            len(saved_entities["task_solutions"]),
            len(saved_entities["evaluation_results"]),
        )
        
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
    except ValueError as e:
        logger.error(f"Validation error during round submission: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"Error submitting round data: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to submit round data: {str(e)}"
        )

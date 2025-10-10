"""
GET endpoints for rounds data retrieval.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from app.models.schemas import RoundWithDetails, AgentEvaluationRunWithDetails
from app.services.data_builder import DataBuilder
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rounds", tags=["rounds-get"])


@router.get("/", response_model=List[RoundWithDetails])
async def list_rounds(
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of rounds to return"),
    skip: int = Query(default=0, ge=0, description="Number of rounds to skip for pagination")
):
    """
    Get a list of all rounds with complete data including agent evaluation runs, tasks, task solutions, and evaluation results.
    
    Args:
        limit: Maximum number of rounds to return (1-1000)
        skip: Number of rounds to skip for pagination
        
    Returns:
        List of RoundWithDetails objects with all related data
    """
    try:
        logger.info(f"Fetching rounds list with limit={limit}, skip={skip}")
        rounds = await DataBuilder.build_rounds_list(limit=limit, skip=skip)
        
        logger.info(f"Successfully retrieved {len(rounds)} rounds")
        return rounds
        
    except Exception as e:
        logger.error(f"Error fetching rounds list: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch rounds: {str(e)}")


@router.get("/{round_id}", response_model=RoundWithDetails)
async def get_round(round_id: str):
    """
    Get a specific round by ID with complete data including agent evaluation runs, tasks, task solutions, and evaluation results.
    
    Args:
        round_id: The ID of the round to retrieve
        
    Returns:
        RoundWithDetails object with all related data
        
    Raises:
        HTTPException: 404 if round not found, 500 for server errors
    """
    try:
        logger.info(f"Fetching round {round_id}")
        round_data = await DataBuilder.build_round_with_details(round_id)
        
        if not round_data:
            logger.warning(f"Round {round_id} not found")
            raise HTTPException(status_code=404, detail=f"Round {round_id} not found")
        
        logger.info(f"Successfully retrieved round {round_id} with {len(round_data.agent_evaluation_runs)} agent runs")
        return round_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching round {round_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch round: {str(e)}")


@router.get("/{round_id}/agent-runs", response_model=List[AgentEvaluationRunWithDetails])
async def get_round_agent_runs(
    round_id: str,
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of agent runs to return"),
    skip: int = Query(default=0, ge=0, description="Number of agent runs to skip for pagination")
):
    """
    Get all agent evaluation runs for a specific round with complete data including tasks, task solutions, and evaluation results.
    
    Args:
        round_id: The ID of the round
        limit: Maximum number of agent runs to return (1-1000)
        skip: Number of agent runs to skip for pagination
        
    Returns:
        List of AgentEvaluationRunWithDetails objects with all related data
        
    Raises:
        HTTPException: 404 if round not found, 500 for server errors
    """
    try:
        logger.info(f"Fetching agent runs for round {round_id} with limit={limit}, skip={skip}")
        agent_runs = await DataBuilder.build_agent_runs_list(round_id=round_id, limit=limit, skip=skip)
        
        logger.info(f"Successfully retrieved {len(agent_runs)} agent runs for round {round_id}")
        return agent_runs
        
    except Exception as e:
        logger.error(f"Error fetching agent runs for round {round_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent runs: {str(e)}")


@router.get("/agent-runs/{agent_run_id}", response_model=AgentEvaluationRunWithDetails)
async def get_agent_run(agent_run_id: str):
    """
    Get a specific agent evaluation run by ID with complete data including tasks, task solutions, and evaluation results.
    
    Args:
        agent_run_id: The ID of the agent evaluation run to retrieve
        
    Returns:
        AgentEvaluationRunWithDetails object with all related data
        
    Raises:
        HTTPException: 404 if agent run not found, 500 for server errors
    """
    try:
        logger.info(f"Fetching agent run {agent_run_id}")
        agent_run_data = await DataBuilder.build_agent_run_with_details(agent_run_id)
        
        if not agent_run_data:
            logger.warning(f"Agent run {agent_run_id} not found")
            raise HTTPException(status_code=404, detail=f"Agent run {agent_run_id} not found")
        
        logger.info(f"Successfully retrieved agent run {agent_run_id} with {len(agent_run_data.tasks)} tasks, "
                   f"{len(agent_run_data.task_solutions)} task solutions, {len(agent_run_data.evaluation_results)} evaluation results")
        return agent_run_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agent run {agent_run_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent run: {str(e)}")


@router.get("/agent-runs/", response_model=List[AgentEvaluationRunWithDetails])
async def list_agent_runs(
    round_id: Optional[str] = Query(default=None, description="Filter by round ID"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of agent runs to return"),
    skip: int = Query(default=0, ge=0, description="Number of agent runs to skip for pagination")
):
    """
    Get a list of agent evaluation runs with complete data including tasks, task solutions, and evaluation results.
    Optionally filter by round ID.
    
    Args:
        round_id: Optional round ID to filter agent runs
        limit: Maximum number of agent runs to return (1-1000)
        skip: Number of agent runs to skip for pagination
        
    Returns:
        List of AgentEvaluationRunWithDetails objects with all related data
    """
    try:
        logger.info(f"Fetching agent runs list with round_id={round_id}, limit={limit}, skip={skip}")
        agent_runs = await DataBuilder.build_agent_runs_list(round_id=round_id, limit=limit, skip=skip)
        
        logger.info(f"Successfully retrieved {len(agent_runs)} agent runs")
        return agent_runs
        
    except Exception as e:
        logger.error(f"Error fetching agent runs list: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent runs: {str(e)}")

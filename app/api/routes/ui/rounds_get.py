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
        
        # Get rounds directly from mock DB (much faster)
        from app.db.mock_mongo import get_mock_db
        from app.models.schemas import Round
        db = get_mock_db()
        rounds_docs = await db.rounds.find().sort("round_id", -1).skip(skip).limit(limit).to_list(length=limit)
        
        # Convert to RoundWithDetails objects (minimal construction)
        rounds = []
        for doc in rounds_docs:
            round_data = Round(**doc)
            # Create minimal RoundWithDetails with just the round data
            # This avoids the expensive DataBuilder.build_rounds_list() call
            round_with_details = RoundWithDetails(
                round_id=round_data.round_id,
                start_block=round_data.start_block,
                end_block=round_data.end_block,
                started_at=round_data.started_at,
                ended_at=round_data.ended_at,
                n_tasks=round_data.n_tasks,
                n_winners=round_data.n_winners,
                winners=round_data.winners,
                average_score=round_data.average_score,
                top_score=round_data.top_score,
                validators=round_data.validators,
                agent_evaluation_runs=[]  # Empty for performance - can be populated on demand
            )
            rounds.append(round_with_details)
        
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
        
        # Get round directly from mock DB (much faster)
        from app.db.mock_mongo import get_mock_db
        from app.models.schemas import Round
        db = get_mock_db()
        round_doc = await db.rounds.find_one({"round_id": round_id})
        
        if not round_doc:
            logger.warning(f"Round {round_id} not found")
            raise HTTPException(status_code=404, detail=f"Round {round_id} not found")
        
        round_data = Round(**round_doc)
        
        # Create minimal RoundWithDetails with just the round data
        # This avoids the expensive DataBuilder.build_round_with_details() call
        round_with_details = RoundWithDetails(
            round_id=round_data.round_id,
            start_block=round_data.start_block,
            end_block=round_data.end_block,
            started_at=round_data.started_at,
            ended_at=round_data.ended_at,
            n_tasks=round_data.n_tasks,
            n_winners=round_data.n_winners,
            winners=round_data.winners,
            average_score=round_data.average_score,
            top_score=round_data.top_score,
            validators=round_data.validators,
            agent_evaluation_runs=[]  # Empty for performance - can be populated on demand
        )
        
        logger.info(f"Successfully retrieved round {round_id}")
        return round_with_details
        
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
        
        # Get agent runs directly from mock DB (much faster)
        from app.db.mock_mongo import get_mock_db
        from app.models.schemas import AgentEvaluationRun
        db = get_mock_db()
        agent_runs_docs = await db.agent_evaluation_runs.find({"round_id": round_id}).skip(skip).limit(limit).to_list(length=limit)
        
        # Convert to AgentEvaluationRunWithDetails objects (minimal construction)
        agent_runs = []
        for doc in agent_runs_docs:
            agent_run_data = AgentEvaluationRun(**doc)
            # Create minimal AgentEvaluationRunWithDetails with just the agent run data
            # This avoids the expensive DataBuilder.build_agent_runs_list() call
            agent_run_with_details = AgentEvaluationRunWithDetails(
                agent_run_id=agent_run_data.agent_run_id,
                round_id=agent_run_data.round_id,
                validator_uid=agent_run_data.validator_uid,
                miner_uid=agent_run_data.miner_uid,
                started_at=agent_run_data.started_at,
                ended_at=agent_run_data.ended_at,
                total_reward=agent_run_data.total_reward,
                tasks=[]  # Empty for performance - can be populated on demand
            )
            agent_runs.append(agent_run_with_details)
        
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
        
        # Get agent run directly from mock DB (much faster)
        from app.db.mock_mongo import get_mock_db
        from app.models.schemas import AgentEvaluationRun
        db = get_mock_db()
        agent_run_doc = await db.agent_evaluation_runs.find_one({"agent_run_id": agent_run_id})
        
        if not agent_run_doc:
            logger.warning(f"Agent run {agent_run_id} not found")
            raise HTTPException(status_code=404, detail=f"Agent run {agent_run_id} not found")
        
        agent_run_data = AgentEvaluationRun(**agent_run_doc)
        
        # Create minimal AgentEvaluationRunWithDetails with just the agent run data
        # This avoids the expensive DataBuilder.build_agent_run_with_details() call
        agent_run_with_details = AgentEvaluationRunWithDetails(
            agent_run_id=agent_run_data.agent_run_id,
            round_id=agent_run_data.round_id,
            validator_uid=agent_run_data.validator_uid,
            miner_uid=agent_run_data.miner_uid,
            started_at=agent_run_data.started_at,
            ended_at=agent_run_data.ended_at,
            total_reward=agent_run_data.total_reward,
            tasks=[]  # Empty for performance - can be populated on demand
        )
        
        logger.info(f"Successfully retrieved agent run {agent_run_id}")
        return agent_run_with_details
        
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
        
        # Get agent runs directly from mock DB (much faster)
        from app.db.mock_mongo import get_mock_db
        from app.models.schemas import AgentEvaluationRun
        db = get_mock_db()
        
        # Build query filter
        query_filter = {}
        if round_id:
            query_filter["round_id"] = round_id
        
        agent_runs_docs = await db.agent_evaluation_runs.find(query_filter).skip(skip).limit(limit).to_list(length=limit)
        
        # Convert to AgentEvaluationRunWithDetails objects (minimal construction)
        agent_runs = []
        for doc in agent_runs_docs:
            agent_run_data = AgentEvaluationRun(**doc)
            # Create minimal AgentEvaluationRunWithDetails with just the agent run data
            # This avoids the expensive DataBuilder.build_agent_runs_list() call
            agent_run_with_details = AgentEvaluationRunWithDetails(
                agent_run_id=agent_run_data.agent_run_id,
                round_id=agent_run_data.round_id,
                validator_uid=agent_run_data.validator_uid,
                miner_uid=agent_run_data.miner_uid,
                started_at=agent_run_data.started_at,
                ended_at=agent_run_data.ended_at,
                total_reward=agent_run_data.total_reward,
                tasks=[]  # Empty for performance - can be populated on demand
            )
            agent_runs.append(agent_run_with_details)
        
        logger.info(f"Successfully retrieved {len(agent_runs)} agent runs")
        return agent_runs
        
    except Exception as e:
        logger.error(f"Error fetching agent runs list: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent runs: {str(e)}")

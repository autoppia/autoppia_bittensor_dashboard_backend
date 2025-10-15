"""
Data builder service for constructing full objects with all related data.
"""
from typing import List, Optional, Dict, Any
from app.models.schemas import (
    Round, AgentEvaluationRun, Task, TaskSolution, EvaluationResult,
    RoundWithDetails, AgentEvaluationRunWithDetails
)
from app.db.mock_mongo import get_mock_db
import logging

logger = logging.getLogger(__name__)


class DataBuilder:
    """Service for building complete objects with all related data."""
    
    @staticmethod
    async def build_round_with_details(validator_round_id: str) -> Optional[RoundWithDetails]:
        """
        Build a complete Round object with all its AgentEvaluationRuns and related data.
        
        Args:
            validator_round_id: The ID of the round to build
            
        Returns:
            RoundWithDetails object with all related data, or None if not found
        """
        db = get_mock_db()
        
        # Get the base round
        round_doc = await db.rounds.find_one({"validator_round_id": validator_round_id})
        if not round_doc:
            logger.warning(f"Round {validator_round_id} not found")
            return None
        
        # Convert to Round object
        round_obj = Round(**round_doc)
        
        # Get all agent evaluation runs for this round
        agent_runs_docs = await db.agent_evaluation_runs.find({"validator_round_id": validator_round_id}).to_list()
        
        # Build complete agent runs with all their data
        agent_runs_with_details = []
        for agent_run_doc in agent_runs_docs:
            agent_run_obj = AgentEvaluationRun(**agent_run_doc)
            agent_run_with_details = await DataBuilder.build_agent_run_with_details(
                agent_run_obj.agent_run_id
            )
            if agent_run_with_details:
                agent_runs_with_details.append(agent_run_with_details)
        
        # Create the complete round object
        round_with_details = RoundWithDetails(
            **round_obj.model_dump(),
            agent_evaluation_runs=agent_runs_with_details
        )
        
        logger.info(f"Built complete round {validator_round_id} with {len(agent_runs_with_details)} agent runs")
        return round_with_details
    
    @staticmethod
    async def build_agent_run_with_details(agent_run_id: str) -> Optional[AgentEvaluationRunWithDetails]:
        """
        Build a complete AgentEvaluationRun object with all its Tasks, TaskSolutions, and EvaluationResults.
        
        Args:
            agent_run_id: The ID of the agent evaluation run to build
            
        Returns:
            AgentEvaluationRunWithDetails object with all related data, or None if not found
        """
        db = get_mock_db()
        
        # Get the base agent run
        agent_run_doc = await db.agent_evaluation_runs.find_one({"agent_run_id": agent_run_id})
        if not agent_run_doc:
            logger.warning(f"Agent run {agent_run_id} not found")
            return None
        
        # Convert to AgentEvaluationRun object
        agent_run_obj = AgentEvaluationRun(**agent_run_doc)
        
        # Get all tasks for this agent run
        tasks_docs = await db.tasks.find({"agent_run_id": agent_run_id}).to_list()
        tasks = [Task(**task_doc) for task_doc in tasks_docs]
        
        # Get all task solutions for this agent run
        task_solutions_docs = await db.task_solutions.find({"agent_run_id": agent_run_id}).to_list()
        task_solutions = [TaskSolution(**ts_doc) for ts_doc in task_solutions_docs]
        
        # Get all evaluation results for this agent run
        evaluation_results_docs = await db.evaluation_results.find({"agent_run_id": agent_run_id}).to_list()
        evaluation_results = [EvaluationResult(**er_doc) for er_doc in evaluation_results_docs]
        
        # Create the complete agent run object
        agent_run_with_details = AgentEvaluationRunWithDetails(
            **agent_run_obj.model_dump(),
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results
        )
        
        logger.info(f"Built complete agent run {agent_run_id} with {len(tasks)} tasks, "
                   f"{len(task_solutions)} task solutions, {len(evaluation_results)} evaluation results")
        return agent_run_with_details
    
    @staticmethod
    async def build_rounds_list(limit: int = 100, skip: int = 0) -> List[RoundWithDetails]:
        """
        Build a list of complete Round objects with all their related data.
        
        Args:
            limit: Maximum number of rounds to return
            skip: Number of rounds to skip (for pagination)
            
        Returns:
            List of RoundWithDetails objects
        """
        db = get_mock_db()
        
        # Get rounds with pagination
        rounds_docs = await db.rounds.find().to_list()
        
        # Build complete rounds
        rounds_with_details = []
        for round_doc in rounds_docs:
            validator_round_id = round_doc["validator_round_id"]
            round_with_details = await DataBuilder.build_round_with_details(validator_round_id)
            if round_with_details:
                rounds_with_details.append(round_with_details)
        
        logger.info(f"Built {len(rounds_with_details)} complete rounds")
        return rounds_with_details
    
    @staticmethod
    async def build_rounds_list_lightweight(limit: int = 100, skip: int = 0) -> List[Round]:
        """
        Build a list of basic Round objects without agent evaluation runs.
        This is much faster for overview and basic round listing.
        
        Args:
            limit: Maximum number of rounds to return
            skip: Number of rounds to skip (for pagination)
            
        Returns:
            List of Round objects (without agent evaluation runs)
        """
        db = get_mock_db()
        
        # Get rounds with pagination - sort by validator_round_id descending to get latest first
        rounds_docs = await db.rounds.find().sort("validator_round_id", -1).skip(skip).limit(limit).to_list(length=limit)
        
        # Convert to Round objects (no agent evaluation runs)
        rounds = []
        for round_doc in rounds_docs:
            try:
                round_obj = Round(**round_doc)
                rounds.append(round_obj)
            except Exception as e:
                logger.warning(f"Failed to parse round {round_doc.get('validator_round_id', 'unknown')}: {e}")
                continue
        
        logger.info(f"Built {len(rounds)} lightweight rounds (no agent evaluation runs)")
        return rounds
    
    @staticmethod
    async def get_round_lightweight(validator_round_id: str) -> Optional[Round]:
        """
        Get a single round by ID without building agent evaluation runs.
        This is much faster for basic round information.
        
        Args:
            validator_round_id: The ID of the round to get
            
        Returns:
            Round object (without agent evaluation runs) or None if not found
        """
        db = get_mock_db()
        
        # Get the round document
        round_doc = await db.rounds.find_one({"validator_round_id": validator_round_id})
        if not round_doc:
            logger.warning(f"Round {validator_round_id} not found")
            return None
        
        try:
            round_obj = Round(**round_doc)
            logger.info(f"Retrieved lightweight round {validator_round_id}")
            return round_obj
        except Exception as e:
            logger.error(f"Failed to parse round {validator_round_id}: {e}")
            return None
    
    @staticmethod
    async def build_agent_runs_list(validator_round_id: Optional[str] = None, limit: int = 100, skip: int = 0) -> List[AgentEvaluationRunWithDetails]:
        """
        Build a list of complete AgentEvaluationRun objects with all their related data.
        
        Args:
            validator_round_id: Optional round ID to filter agent runs
            limit: Maximum number of agent runs to return
            skip: Number of agent runs to skip (for pagination)
            
        Returns:
            List of AgentEvaluationRunWithDetails objects
        """
        db = get_mock_db()
        
        # Build query
        query = {}
        if validator_round_id:
            query["validator_round_id"] = validator_round_id
        
        # Get agent runs with pagination
        agent_runs_docs = await db.agent_evaluation_runs.find(query).to_list()
        
        # Build complete agent runs
        agent_runs_with_details = []
        for agent_run_doc in agent_runs_docs:
            agent_run_id = agent_run_doc["agent_run_id"]
            agent_run_with_details = await DataBuilder.build_agent_run_with_details(agent_run_id)
            if agent_run_with_details:
                agent_runs_with_details.append(agent_run_with_details)
        
        logger.info(f"Built {len(agent_runs_with_details)} complete agent runs")
        return agent_runs_with_details

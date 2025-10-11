"""
Agent Runs service layer for the AutoPPIA Bittensor Dashboard.
This service handles business logic for agent evaluation runs.
"""
import logging
import hashlib
import random
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from app.models.agent_runs import (
    AgentRun, Personas, Statistics, Summary, Task, Action, Website,
    RoundInfo, ValidatorInfo, AgentInfo, ScoreDistribution,
    PerformanceByWebsite, PerformanceByUseCase, TopPerformingWebsite,
    TopPerformingUseCase, RecentActivity, Event, Log, Metric, Metrics,
    RunStatus, TaskStatus, LogLevel, EventType
)
from app.db.mock_mongo import get_mock_db
from app.models.schemas import Round, AgentEvaluationRun, Task as SchemaTask, TaskSolution, EvaluationResult

logger = logging.getLogger(__name__)


class AgentRunsService:
    """Service for managing agent evaluation runs."""
    
    def __init__(self):
        self.db = get_mock_db()
    
    async def get_agent_run_details(
        self, 
        run_id: str, 
        include_tasks: bool = False,
        include_stats: bool = False,
        include_summary: bool = False,
        include_personas: bool = False
    ) -> Optional[AgentRun]:
        """Get comprehensive details for a specific agent run."""
        try:
            logger.info(f"Fetching agent run details for {run_id}")
            
            # Get agent evaluation run from mock data
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return None
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Get related data
            tasks = await self._get_tasks_for_run(run_id)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            
            # Get round data for context
            round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
            if not round_doc:
                return None
            
            round_data = Round(**round_doc)
            
            # Find validator and miner info
            validator = next((v for v in round_data.validators if v.uid == agent_run.validator_uid), None)
            miner = next((m for m in round_data.miners if m.uid == agent_run.miner_uid), None)
            
            if not validator or not miner:
                return None
            
            # Calculate metrics
            total_tasks = len(tasks)
            completed_tasks = len([t for t in tasks if t.scope == "local"])
            successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            failed_tasks = total_tasks - successful_tasks
            avg_score = sum(er.final_score for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0
            overall_score = int(avg_score * 100)
            
            # Calculate duration
            duration = int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
            
            # Get ranking from round winners
            ranking = 1
            if round_data.winners:
                for i, winner in enumerate(round_data.winners):
                    if winner.get('miner_uid') == agent_run.miner_uid:
                        ranking = winner.get('rank', i + 1)
                        break
            
            # Build websites data
            websites = await self._build_websites_data(tasks, evaluation_results)
            
            # Build tasks data if requested
            tasks_data = []
            if include_tasks:
                tasks_data = await self._build_tasks_data(tasks, task_solutions, evaluation_results)
            
            # Build metadata
            metadata = {
                "environment": "production",
                "version": "1.2.3",
                "resources": {
                    "cpu": 2.5,
                    "memory": 1024,
                    "storage": 512
                }
            }
            
            return AgentRun(
                runId=run_id,
                agentId=f"agent-{agent_run.miner_uid}",
                roundId=int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                validatorId=f"validator-{validator.uid}",
                validatorName=validator.name or f"Validator {validator.uid}",
                validatorImage=f"/images/icons/validators/{validator.name or f'validator_{validator.uid}'}.png",
                startTime=datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                endTime=datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                status=RunStatus.COMPLETED if agent_run.ended_at else RunStatus.RUNNING,
                totalTasks=total_tasks,
                completedTasks=completed_tasks,
                successfulTasks=successful_tasks,
                failedTasks=failed_tasks,
                score=avg_score,
                ranking=ranking,
                duration=duration,
                overallScore=overall_score,
                websites=websites,
                tasks=tasks_data,
                metadata=metadata
            )
            
        except Exception as e:
            logger.error(f"Error fetching agent run details: {e}")
            return None
    
    async def get_agent_run_personas(self, run_id: str) -> Optional[Personas]:
        """Get personas data for an agent run."""
        try:
            logger.info(f"Fetching personas for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return None
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Get round data
            round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
            if not round_doc:
                return None
            
            round_data = Round(**round_doc)
            
            # Find validator and miner info
            validator = next((v for v in round_data.validators if v.uid == agent_run.validator_uid), None)
            miner = next((m for m in round_data.miners if m.uid == agent_run.miner_uid), None)
            
            if not validator or not miner:
                return None
            
            # Build round info
            round_info = RoundInfo(
                id=int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                name=f"Round {int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20}",
                status="active" if not agent_run.ended_at else "completed",
                startTime=datetime.fromtimestamp(round_data.started_at, tz=timezone.utc).isoformat(),
                endTime=datetime.fromtimestamp(round_data.ended_at, tz=timezone.utc).isoformat() if round_data.ended_at else None
            )
            
            # Build validator info
            validator_info = ValidatorInfo(
                id=f"validator-{validator.uid}",
                name=validator.name or f"Validator {validator.uid}",
                image=f"/images/icons/validators/{validator.name or f'validator_{validator.uid}'}.png",
                description=f"{validator.name or f'Validator {validator.uid}'} Validator",
                website="https://autoppia.com",
                github="https://github.com/autoppia"
            )
            
            # Build agent info
            agent_info = AgentInfo(
                id=f"agent-{miner.uid}",
                name=miner.agent_name or f"Agent {miner.uid}",
                type="autoppia",
                image=miner.agent_image or f"/images/icons/agents/agent_{miner.uid}.png",
                description=f"{miner.agent_name or f'Agent {miner.uid}'}'s main agent"
            )
            
            return Personas(
                round=round_info,
                validator=validator_info,
                agent=agent_info
            )
            
        except Exception as e:
            logger.error(f"Error fetching personas: {e}")
            return None
    
    async def get_agent_run_statistics(self, run_id: str) -> Optional[Statistics]:
        """Get detailed statistics for an agent run."""
        try:
            logger.info(f"Fetching statistics for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return None
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Get related data
            tasks = await self._get_tasks_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            
            # Calculate basic metrics
            total_tasks = len(tasks)
            successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            failed_tasks = total_tasks - successful_tasks
            overall_score = int(sum(er.final_score for er in evaluation_results) / len(evaluation_results) * 100) if evaluation_results else 0
            success_rate = (successful_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
            
            # Calculate average task duration
            avg_duration = sum(er.evaluation_time for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0
            
            # Build score distribution
            excellent = len([er for er in evaluation_results if er.final_score >= 0.9])
            good = len([er for er in evaluation_results if 0.7 <= er.final_score < 0.9])
            average = len([er for er in evaluation_results if 0.5 <= er.final_score < 0.7])
            poor = len([er for er in evaluation_results if er.final_score < 0.5])
            
            score_distribution = ScoreDistribution(
                excellent=excellent,
                good=good,
                average=average,
                poor=poor
            )
            
            # Build performance by website
            performance_by_website = await self._build_performance_by_website(tasks, evaluation_results)
            
            # Build performance by use case
            performance_by_use_case = await self._build_performance_by_use_case(tasks, evaluation_results)
            
            return Statistics(
                runId=run_id,
                overallScore=overall_score,
                totalTasks=total_tasks,
                successfulTasks=successful_tasks,
                failedTasks=failed_tasks,
                websites=len(set(task.url for task in tasks)),
                averageTaskDuration=avg_duration,
                successRate=success_rate,
                scoreDistribution=score_distribution,
                performanceByWebsite=performance_by_website,
                performanceByUseCase=performance_by_use_case
            )
            
        except Exception as e:
            logger.error(f"Error fetching statistics: {e}")
            return None
    
    async def get_agent_run_summary(self, run_id: str) -> Optional[Summary]:
        """Get summary information for an agent run."""
        try:
            logger.info(f"Fetching summary for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return None
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Get related data
            tasks = await self._get_tasks_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            
            # Calculate metrics
            total_tasks = len(tasks)
            successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            failed_tasks = total_tasks - successful_tasks
            overall_score = int(sum(er.final_score for er in evaluation_results) / len(evaluation_results) * 100) if evaluation_results else 0
            duration = int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
            
            # Get ranking
            round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
            ranking = 1
            if round_doc:
                round_data = Round(**round_doc)
                if round_data.winners:
                    for i, winner in enumerate(round_data.winners):
                        if winner.get('miner_uid') == agent_run.miner_uid:
                            ranking = winner.get('rank', i + 1)
                            break
            
            # Find top performing website
            website_scores = {}
            for task, er in zip(tasks, evaluation_results):
                website = self._extract_website_from_url(task.url)
                if website not in website_scores:
                    website_scores[website] = []
                website_scores[website].append(er.final_score)
            
            top_website = None
            if website_scores:
                best_website = max(website_scores.keys(), key=lambda w: sum(website_scores[w]) / len(website_scores[w]))
                top_website = TopPerformingWebsite(
                    website=best_website,
                    score=sum(website_scores[best_website]) / len(website_scores[best_website]),
                    tasks=len(website_scores[best_website])
                )
            
            # Find top performing use case
            use_case_scores = {}
            for task, er in zip(tasks, evaluation_results):
                use_case = getattr(task, 'use_case', 'unknown')
                if use_case not in use_case_scores:
                    use_case_scores[use_case] = []
                use_case_scores[use_case].append(er.final_score)
            
            top_use_case = None
            if use_case_scores:
                best_use_case = max(use_case_scores.keys(), key=lambda u: sum(use_case_scores[u]) / len(use_case_scores[u]))
                top_use_case = TopPerformingUseCase(
                    useCase=best_use_case,
                    score=sum(use_case_scores[best_use_case]) / len(use_case_scores[best_use_case]),
                    tasks=len(use_case_scores[best_use_case])
                )
            
            # Build recent activity
            recent_activity = [
                RecentActivity(
                    timestamp=datetime.fromtimestamp(agent_run.ended_at or agent_run.started_at, tz=timezone.utc).isoformat(),
                    action="run_completed" if agent_run.ended_at else "run_started",
                    details="Agent run completed successfully" if agent_run.ended_at else "Agent run started"
                )
            ]
            
            return Summary(
                runId=run_id,
                agentId=f"agent-{agent_run.miner_uid}",
                roundId=int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                validatorId=f"validator-{agent_run.validator_uid}",
                startTime=datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                endTime=datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                status=RunStatus.COMPLETED if agent_run.ended_at else RunStatus.RUNNING,
                overallScore=overall_score,
                totalTasks=total_tasks,
                successfulTasks=successful_tasks,
                failedTasks=failed_tasks,
                duration=duration,
                ranking=ranking,
                topPerformingWebsite=top_website or TopPerformingWebsite(website="Unknown", score=0.0, tasks=0),
                topPerformingUseCase=top_use_case or TopPerformingUseCase(useCase="unknown", score=0.0, tasks=0),
                recentActivity=recent_activity
            )
            
        except Exception as e:
            logger.error(f"Error fetching summary: {e}")
            return None
    
    async def get_agent_run_tasks(
        self, 
        run_id: str, 
        page: int = 1, 
        limit: int = 20,
        website: Optional[str] = None,
        use_case: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get tasks for an agent run with pagination and filtering."""
        try:
            logger.info(f"Fetching tasks for agent run {run_id}")
            
            # Get tasks
            tasks = await self._get_tasks_for_run(run_id)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            
            # Build tasks data
            tasks_data = await self._build_tasks_data(tasks, task_solutions, evaluation_results)
            
            # Apply filters
            if website:
                tasks_data = [t for t in tasks_data if website.lower() in t.website.lower()]
            
            if use_case:
                tasks_data = [t for t in tasks_data if use_case.lower() in t.useCase.lower()]
            
            if status:
                tasks_data = [t for t in tasks_data if t.status.value == status]
            
            # Apply sorting
            if sort_by == "startTime":
                tasks_data.sort(key=lambda x: x.startTime, reverse=(sort_order == "desc"))
            elif sort_by == "score":
                tasks_data.sort(key=lambda x: x.score, reverse=(sort_order == "desc"))
            elif sort_by == "duration":
                tasks_data.sort(key=lambda x: x.duration, reverse=(sort_order == "desc"))
            
            # Apply pagination
            total = len(tasks_data)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_tasks = tasks_data[start_idx:end_idx]
            
            return {
                "tasks": paginated_tasks,
                "total": total,
                "page": page,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error fetching tasks: {e}")
            return {"tasks": [], "total": 0, "page": page, "limit": limit}
    
    async def get_agent_runs_by_agent(
        self, 
        agent_id: str, 
        page: int = 1, 
        limit: int = 20,
        round_id: Optional[int] = None,
        validator_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get all agent runs for a specific agent."""
        try:
            logger.info(f"Fetching agent runs for agent {agent_id}")
            
            # Extract miner UID from agent ID
            miner_uid = int(agent_id.split('-')[1]) if '-' in agent_id else int(agent_id)
            
            # Build query
            query = {"miner_uid": miner_uid}
            if round_id:
                query["round_id"] = f"round_{round_id:03d}"
            if validator_id:
                validator_uid = int(validator_id.split('-')[1]) if '-' in validator_id else int(validator_id)
                query["validator_uid"] = validator_uid
            
            # Get agent runs
            agent_runs_docs = await self.db.agent_evaluation_runs.find(query).to_list(length=100)
            
            # Convert to agent runs
            agent_runs = []
            for doc in agent_runs_docs:
                agent_run = AgentEvaluationRun(**doc)
                
                # Get round data
                round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
                if not round_doc:
                    continue
                
                round_data = Round(**round_doc)
                
                # Find validator
                validator = next((v for v in round_data.validators if v.uid == agent_run.validator_uid), None)
                if not validator:
                    continue
                
                # Get evaluation results for metrics
                evaluation_results = await self._get_evaluation_results_for_run(agent_run.agent_run_id)
                avg_score = sum(er.final_score for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0
                overall_score = int(avg_score * 100)
                
                # Get ranking
                ranking = 1
                if round_data.winners:
                    for i, winner in enumerate(round_data.winners):
                        if winner.get('miner_uid') == agent_run.miner_uid:
                            ranking = winner.get('rank', i + 1)
                            break
                
                agent_runs.append({
                    "runId": agent_run.agent_run_id,
                    "agentId": agent_id,
                    "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                    "validatorId": f"validator-{validator.uid}",
                    "validatorName": validator.name or f"Validator {validator.uid}",
                    "startTime": datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                    "status": "completed" if agent_run.ended_at else "running",
                    "overallScore": overall_score,
                    "ranking": ranking,
                    "duration": int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
                })
            
            # Apply status filter
            if status:
                agent_runs = [r for r in agent_runs if r["status"] == status]
            
            # Apply sorting
            if sort_by == "startTime":
                agent_runs.sort(key=lambda x: x["startTime"], reverse=(sort_order == "desc"))
            elif sort_by == "score":
                agent_runs.sort(key=lambda x: x["overallScore"], reverse=(sort_order == "desc"))
            elif sort_by == "duration":
                agent_runs.sort(key=lambda x: x["duration"], reverse=(sort_order == "desc"))
            elif sort_by == "ranking":
                agent_runs.sort(key=lambda x: x["ranking"], reverse=(sort_order == "desc"))
            
            # Apply pagination
            total = len(agent_runs)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_runs = agent_runs[start_idx:end_idx]
            
            return {
                "runs": paginated_runs,
                "total": total,
                "page": page,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error fetching agent runs by agent: {e}")
            return {"runs": [], "total": 0, "page": page, "limit": limit}
    
    async def get_agent_runs_by_round(
        self, 
        round_id: int, 
        page: int = 1, 
        limit: int = 20,
        validator_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get all agent runs for a specific round."""
        try:
            logger.info(f"Fetching agent runs for round {round_id}")
            
            # Build query
            query = {"round_id": f"round_{round_id:03d}"}
            if validator_id:
                validator_uid = int(validator_id.split('-')[1]) if '-' in validator_id else int(validator_id)
                query["validator_uid"] = validator_uid
            
            # Get agent runs
            agent_runs_docs = await self.db.agent_evaluation_runs.find(query).to_list(length=100)
            
            # Convert to agent runs
            agent_runs = []
            for doc in agent_runs_docs:
                agent_run = AgentEvaluationRun(**doc)
                
                # Get round data
                round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
                if not round_doc:
                    continue
                
                round_data = Round(**round_doc)
                
                # Find validator and miner
                validator = next((v for v in round_data.validators if v.uid == agent_run.validator_uid), None)
                miner = next((m for m in round_data.miners if m.uid == agent_run.miner_uid), None)
                
                if not validator or not miner:
                    continue
                
                # Get evaluation results for metrics
                evaluation_results = await self._get_evaluation_results_for_run(agent_run.agent_run_id)
                avg_score = sum(er.final_score for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0
                overall_score = int(avg_score * 100)
                
                # Get ranking
                ranking = 1
                if round_data.winners:
                    for i, winner in enumerate(round_data.winners):
                        if winner.get('miner_uid') == agent_run.miner_uid:
                            ranking = winner.get('rank', i + 1)
                            break
                
                agent_runs.append({
                    "runId": agent_run.agent_run_id,
                    "agentId": f"agent-{miner.uid}",
                    "agentName": miner.agent_name or f"Agent {miner.uid}",
                    "validatorId": f"validator-{validator.uid}",
                    "validatorName": validator.name or f"Validator {validator.uid}",
                    "startTime": datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                    "status": "completed" if agent_run.ended_at else "running",
                    "overallScore": overall_score,
                    "ranking": ranking,
                    "duration": int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
                })
            
            # Apply status filter
            if status:
                agent_runs = [r for r in agent_runs if r["status"] == status]
            
            # Apply sorting
            if sort_by == "startTime":
                agent_runs.sort(key=lambda x: x["startTime"], reverse=(sort_order == "desc"))
            elif sort_by == "score":
                agent_runs.sort(key=lambda x: x["overallScore"], reverse=(sort_order == "desc"))
            elif sort_by == "duration":
                agent_runs.sort(key=lambda x: x["duration"], reverse=(sort_order == "desc"))
            elif sort_by == "ranking":
                agent_runs.sort(key=lambda x: x["ranking"], reverse=(sort_order == "desc"))
            
            # Apply pagination
            total = len(agent_runs)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_runs = agent_runs[start_idx:end_idx]
            
            return {
                "runs": paginated_runs,
                "total": total,
                "page": page,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error fetching agent runs by round: {e}")
            return {"runs": [], "total": 0, "page": page, "limit": limit}
    
    async def get_agent_runs_by_validator(
        self, 
        validator_id: str, 
        page: int = 1, 
        limit: int = 20,
        round_id: Optional[int] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get all agent runs for a specific validator."""
        try:
            logger.info(f"Fetching agent runs for validator {validator_id}")
            
            # Extract validator UID
            validator_uid = int(validator_id.split('-')[1]) if '-' in validator_id else int(validator_id)
            
            # Build query
            query = {"validator_uid": validator_uid}
            if round_id:
                query["round_id"] = f"round_{round_id:03d}"
            
            # Get agent runs
            agent_runs_docs = await self.db.agent_evaluation_runs.find(query).to_list(length=100)
            
            # Convert to agent runs
            agent_runs = []
            for doc in agent_runs_docs:
                agent_run = AgentEvaluationRun(**doc)
                
                # Get round data
                round_doc = await self.db.rounds.find_one({"round_id": agent_run.round_id})
                if not round_doc:
                    continue
                
                round_data = Round(**round_doc)
                
                # Find miner
                miner = next((m for m in round_data.miners if m.uid == agent_run.miner_uid), None)
                if not miner:
                    continue
                
                # Get evaluation results for metrics
                evaluation_results = await self._get_evaluation_results_for_run(agent_run.agent_run_id)
                avg_score = sum(er.final_score for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0
                overall_score = int(avg_score * 100)
                
                # Get ranking
                ranking = 1
                if round_data.winners:
                    for i, winner in enumerate(round_data.winners):
                        if winner.get('miner_uid') == agent_run.miner_uid:
                            ranking = winner.get('rank', i + 1)
                            break
                
                agent_runs.append({
                    "runId": agent_run.agent_run_id,
                    "agentId": f"agent-{miner.uid}",
                    "agentName": miner.agent_name or f"Agent {miner.uid}",
                    "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                    "startTime": datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                    "status": "completed" if agent_run.ended_at else "running",
                    "overallScore": overall_score,
                    "ranking": ranking,
                    "duration": int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
                })
            
            # Apply status filter
            if status:
                agent_runs = [r for r in agent_runs if r["status"] == status]
            
            # Apply sorting
            if sort_by == "startTime":
                agent_runs.sort(key=lambda x: x["startTime"], reverse=(sort_order == "desc"))
            elif sort_by == "score":
                agent_runs.sort(key=lambda x: x["overallScore"], reverse=(sort_order == "desc"))
            elif sort_by == "duration":
                agent_runs.sort(key=lambda x: x["duration"], reverse=(sort_order == "desc"))
            elif sort_by == "ranking":
                agent_runs.sort(key=lambda x: x["ranking"], reverse=(sort_order == "desc"))
            
            # Apply pagination
            total = len(agent_runs)
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            paginated_runs = agent_runs[start_idx:end_idx]
            
            return {
                "runs": paginated_runs,
                "total": total,
                "page": page,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error fetching agent runs by validator: {e}")
            return {"runs": [], "total": 0, "page": page, "limit": limit}
    
    async def compare_agent_runs(self, run_ids: List[str]) -> Dict[str, Any]:
        """Compare multiple agent runs."""
        try:
            logger.info(f"Comparing agent runs: {run_ids}")
            
            runs = []
            for run_id in run_ids:
                run_details = await self.get_agent_run_details(run_id)
                if run_details:
                    runs.append(run_details)
            
            if not runs:
                return {"runs": [], "comparison": {}}
            
            # Find best performers
            best_score = max(runs, key=lambda r: r.overallScore)
            fastest = min(runs, key=lambda r: r.duration)
            most_tasks = max(runs, key=lambda r: r.totalTasks)
            best_success_rate = max(runs, key=lambda r: (r.successfulTasks / r.totalTasks) if r.totalTasks > 0 else 0)
            
            comparison = {
                "bestScore": best_score.runId,
                "fastest": fastest.runId,
                "mostTasks": most_tasks.runId,
                "bestSuccessRate": best_success_rate.runId
            }
            
            return {
                "runs": runs,
                "comparison": comparison
            }
            
        except Exception as e:
            logger.error(f"Error comparing agent runs: {e}")
            return {"runs": [], "comparison": {}}
    
    async def get_agent_run_timeline(self, run_id: str) -> List[Event]:
        """Get timeline of events for an agent run."""
        try:
            logger.info(f"Fetching timeline for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return []
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Get tasks for timeline
            tasks = await self._get_tasks_for_run(run_id)
            
            events = []
            
            # Add run started event
            events.append(Event(
                timestamp=datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                type=EventType.RUN_STARTED,
                message="Agent run started",
                metadata={
                    "agentId": f"agent-{agent_run.miner_uid}",
                    "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20
                }
            ))
            
            # Add task events
            for task in tasks[:10]:  # Limit to first 10 tasks for performance
                events.append(Event(
                    timestamp=datetime.fromtimestamp(agent_run.started_at + random.randint(1, 3600), tz=timezone.utc).isoformat(),
                    type=EventType.TASK_STARTED,
                    taskId=task.task_id,
                    message=f"Task started: {task.prompt[:50]}...",
                    metadata={
                        "website": self._extract_website_from_url(task.url),
                        "useCase": getattr(task, 'use_case', 'unknown')
                    }
                ))
            
            # Add run completed event if applicable
            if agent_run.ended_at:
                events.append(Event(
                    timestamp=datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat(),
                    type=EventType.RUN_COMPLETED,
                    message="Agent run completed",
                    metadata={
                        "agentId": f"agent-{agent_run.miner_uid}",
                        "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20
                    }
                ))
            
            # Sort events by timestamp
            events.sort(key=lambda x: x.timestamp)
            
            return events
            
        except Exception as e:
            logger.error(f"Error fetching timeline: {e}")
            return []
    
    async def get_agent_run_logs(
        self, 
        run_id: str, 
        level: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get logs for an agent run."""
        try:
            logger.info(f"Fetching logs for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return {"logs": [], "total": 0}
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Generate mock logs
            logs = []
            log_levels = [LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR, LogLevel.DEBUG]
            
            for i in range(150):  # Generate 150 mock logs
                log_level = random.choice(log_levels)
                
                # Apply level filter
                if level and log_level.value != level:
                    continue
                
                messages = [
                    "Agent run started",
                    "Task execution initiated",
                    "Browser automation completed",
                    "Validation test passed",
                    "Validation test failed",
                    "Network request completed",
                    "Screenshot captured",
                    "Action executed successfully",
                    "Error occurred during execution",
                    "Agent run completed"
                ]
                
                logs.append(Log(
                    timestamp=datetime.fromtimestamp(agent_run.started_at + random.randint(1, 3600), tz=timezone.utc).isoformat(),
                    level=log_level,
                    message=random.choice(messages),
                    metadata={
                        "agentId": f"agent-{agent_run.miner_uid}",
                        "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20
                    }
                ))
            
            # Sort by timestamp
            logs.sort(key=lambda x: x.timestamp)
            
            # Apply pagination
            total = len(logs)
            paginated_logs = logs[offset:offset + limit]
            
            return {
                "logs": paginated_logs,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Error fetching logs: {e}")
            return {"logs": [], "total": 0}
    
    async def get_agent_run_metrics(self, run_id: str) -> Optional[Metrics]:
        """Get performance metrics for an agent run."""
        try:
            logger.info(f"Fetching metrics for agent run {run_id}")
            
            # Get agent evaluation run
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return None
            
            agent_run = AgentEvaluationRun(**agent_run_doc)
            
            # Generate mock metrics
            duration = int((agent_run.ended_at or agent_run.started_at) - agent_run.started_at)
            
            # Generate CPU metrics
            cpu_metrics = []
            for i in range(0, duration, 60):  # Every minute
                cpu_metrics.append(Metric(
                    timestamp=datetime.fromtimestamp(agent_run.started_at + i, tz=timezone.utc).isoformat(),
                    value=random.uniform(20.0, 80.0)
                ))
            
            # Generate memory metrics
            memory_metrics = []
            for i in range(0, duration, 60):  # Every minute
                memory_metrics.append(Metric(
                    timestamp=datetime.fromtimestamp(agent_run.started_at + i, tz=timezone.utc).isoformat(),
                    value=random.uniform(512.0, 2048.0)
                ))
            
            # Generate network metrics
            network_metrics = []
            for i in range(0, duration, 60):  # Every minute
                network_metrics.append(Metric(
                    timestamp=datetime.fromtimestamp(agent_run.started_at + i, tz=timezone.utc).isoformat(),
                    value=random.uniform(1024.0, 8192.0)
                ))
            
            return Metrics(
                cpu=cpu_metrics,
                memory=memory_metrics,
                network=network_metrics,
                duration=duration,
                peakCpu=max(m.value for m in cpu_metrics) if cpu_metrics else 0.0,
                peakMemory=max(m.value for m in memory_metrics) if memory_metrics else 0.0,
                totalNetworkTraffic=int(sum(m.value for m in network_metrics) * 60) if network_metrics else 0
            )
            
        except Exception as e:
            logger.error(f"Error fetching metrics: {e}")
            return None
    
    # Helper methods
    async def _get_tasks_for_run(self, run_id: str) -> List[SchemaTask]:
        """Get all tasks for an agent run."""
        tasks_docs = await self.db.tasks.find({"agent_run_id": run_id}).to_list(length=1000)
        return [SchemaTask(**doc) for doc in tasks_docs]
    
    async def _get_task_solutions_for_run(self, run_id: str) -> List[TaskSolution]:
        """Get all task solutions for an agent run."""
        solutions_docs = await self.db.task_solutions.find({"agent_run_id": run_id}).to_list(length=1000)
        return [TaskSolution(**doc) for doc in solutions_docs]
    
    async def _get_evaluation_results_for_run(self, run_id: str) -> List[EvaluationResult]:
        """Get all evaluation results for an agent run."""
        results_docs = await self.db.evaluation_results.find({"agent_run_id": run_id}).to_list(length=1000)
        return [EvaluationResult(**doc) for doc in results_docs]
    
    async def _build_websites_data(self, tasks: List[SchemaTask], evaluation_results: List[EvaluationResult]) -> List[Website]:
        """Build websites performance data."""
        website_data = {}
        
        for task, result in zip(tasks, evaluation_results):
            website = self._extract_website_from_url(task.url)
            if website not in website_data:
                website_data[website] = {
                    "tasks": 0,
                    "successful": 0,
                    "failed": 0,
                    "scores": []
                }
            
            website_data[website]["tasks"] += 1
            website_data[website]["scores"].append(result.final_score)
            
            if result.final_score > 0.5:
                website_data[website]["successful"] += 1
            else:
                website_data[website]["failed"] += 1
        
        websites = []
        for website, data in website_data.items():
            avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
            websites.append(Website(
                website=website,
                tasks=data["tasks"],
                successful=data["successful"],
                failed=data["failed"],
                score=avg_score
            ))
        
        return websites
    
    async def _build_tasks_data(
        self, 
        tasks: List[SchemaTask], 
        task_solutions: List[TaskSolution], 
        evaluation_results: List[EvaluationResult]
    ) -> List[Task]:
        """Build tasks data with actions and details."""
        tasks_data = []
        
        for task, solution, result in zip(tasks, task_solutions, evaluation_results):
            # Build actions from task solution
            actions = []
            for action in solution.actions:
                actions.append(Action(
                    id=f"action-{len(actions) + 1}",
                    type=action.type,
                    selector=action.attributes.get("selector"),
                    value=action.attributes.get("value"),
                    timestamp=datetime.fromtimestamp(task.started_at or 0, tz=timezone.utc).isoformat(),
                    duration=random.uniform(1.0, 5.0),
                    success=result.final_score > 0.5
                ))
            
            # Build screenshots and logs
            screenshots = [f"screenshot-{i+1}.png" for i in range(random.randint(1, 3))]
            logs = [
                "Task started",
                "Navigation successful" if result.final_score > 0.5 else "Navigation failed",
                "Task completed" if result.final_score > 0.5 else "Task failed"
            ]
            
            tasks_data.append(Task(
                taskId=task.task_id,
                website=self._extract_website_from_url(task.url),
                useCase=getattr(task, 'use_case', 'unknown'),
                prompt=task.prompt,
                status=TaskStatus.COMPLETED if result.final_score > 0.5 else TaskStatus.FAILED,
                score=result.final_score,
                duration=int(result.evaluation_time),
                startTime=datetime.fromtimestamp(task.started_at or 0, tz=timezone.utc).isoformat(),
                endTime=datetime.fromtimestamp((task.started_at or 0) + result.evaluation_time, tz=timezone.utc).isoformat(),
                actions=actions,
                screenshots=screenshots,
                logs=logs
            ))
        
        return tasks_data
    
    async def _build_performance_by_website(self, tasks: List[SchemaTask], evaluation_results: List[EvaluationResult]) -> List[PerformanceByWebsite]:
        """Build performance data by website."""
        website_data = {}
        
        for task, result in zip(tasks, evaluation_results):
            website = self._extract_website_from_url(task.url)
            if website not in website_data:
                website_data[website] = {
                    "tasks": 0,
                    "successful": 0,
                    "failed": 0,
                    "scores": [],
                    "durations": []
                }
            
            website_data[website]["tasks"] += 1
            website_data[website]["scores"].append(result.final_score)
            website_data[website]["durations"].append(result.evaluation_time)
            
            if result.final_score > 0.5:
                website_data[website]["successful"] += 1
            else:
                website_data[website]["failed"] += 1
        
        performance = []
        for website, data in website_data.items():
            avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
            avg_duration = sum(data["durations"]) / len(data["durations"]) if data["durations"] else 0.0
            
            performance.append(PerformanceByWebsite(
                website=website,
                tasks=data["tasks"],
                successful=data["successful"],
                failed=data["failed"],
                averageScore=avg_score,
                averageDuration=avg_duration
            ))
        
        return performance
    
    async def _build_performance_by_use_case(self, tasks: List[SchemaTask], evaluation_results: List[EvaluationResult]) -> List[PerformanceByUseCase]:
        """Build performance data by use case."""
        use_case_data = {}
        
        for task, result in zip(tasks, evaluation_results):
            use_case = getattr(task, 'use_case', 'unknown')
            if use_case not in use_case_data:
                use_case_data[use_case] = {
                    "tasks": 0,
                    "successful": 0,
                    "failed": 0,
                    "scores": [],
                    "durations": []
                }
            
            use_case_data[use_case]["tasks"] += 1
            use_case_data[use_case]["scores"].append(result.final_score)
            use_case_data[use_case]["durations"].append(result.evaluation_time)
            
            if result.final_score > 0.5:
                use_case_data[use_case]["successful"] += 1
            else:
                use_case_data[use_case]["failed"] += 1
        
        performance = []
        for use_case, data in use_case_data.items():
            avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
            avg_duration = sum(data["durations"]) / len(data["durations"]) if data["durations"] else 0.0
            
            performance.append(PerformanceByUseCase(
                useCase=use_case,
                tasks=data["tasks"],
                successful=data["successful"],
                failed=data["failed"],
                averageScore=avg_score,
                averageDuration=avg_duration
            ))
        
        return performance
    
    def _extract_website_from_url(self, url: str) -> str:
        """Extract website name from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path
            # Remove common prefixes and suffixes
            domain = domain.replace('www.', '').replace('.com', '').replace('.org', '').replace('.net', '')
            return domain.title() if domain else "Unknown"
        except:
            return "Unknown"

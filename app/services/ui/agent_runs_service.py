"""
Agent Runs service layer for the AutoPPIA Bittensor Dashboard.
This service handles business logic for agent evaluation runs.
"""
import logging
import random
from collections import defaultdict
from typing import List, Optional, Dict, Any, Iterable
from datetime import datetime, timezone, timedelta
from app.utils.score_formatter import format_score_as_percentage_float
from app.models.ui.agent_runs import (
    AgentRun, Personas, Statistics, Summary, Task, Action, Website,
    RoundInfo, ValidatorInfo, AgentInfo, ScoreDistribution,
    PerformanceByWebsite, PerformanceByUseCase, TopPerformingWebsite,
    TopPerformingUseCase, RecentActivity, Event, Log, Metric, Metrics,
    RunStatus, TaskStatus, LogLevel, EventType
)
from app.db.mock_mongo import get_mock_db
from app.models.schemas import Round, AgentEvaluationRun, Task as SchemaTask, TaskSolution, EvaluationResult
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class AgentRunsService:
    """Service for managing agent evaluation runs."""
    
    def __init__(self):
        self.db = get_mock_db()
    
    async def get_agent_runs_list(
        self, 
        page: int = 1, 
        limit: int = 20,
        round_id: Optional[int] = None,
        validator_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """Get list of agent runs with filtering and pagination."""
        try:
            logger.info(f"Fetching agent runs list with page={page}, limit={limit}")
            
            # Build query
            query = {}
            if round_id:
                query["round_id"] = f"round_{round_id:03d}"
            if validator_id:
                validator_uid = int(validator_id.split('-')[1]) if '-' in validator_id else int(validator_id)
                query["validator_uid"] = validator_uid
            if agent_id:
                miner_uid = int(agent_id.split('-')[1]) if '-' in agent_id else int(agent_id)
                query["miner_uid"] = miner_uid
            
            # Get agent runs
            agent_runs_docs = await self.db.agent_evaluation_runs.find(query).to_list(length=1000)
            
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
                
                # Calculate success rate
                successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
                total_tasks = len(evaluation_results)
                success_rate = (successful_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
                
                agent_runs.append({
                    "runId": agent_run.agent_run_id,
                    "agentId": f"agent-{miner.uid}",
                    "roundId": int(agent_run.round_id.split('_')[1]) if '_' in agent_run.round_id else 20,
                    "validatorId": f"validator-{validator.uid}",
                    "status": "completed" if agent_run.ended_at else "running",
                    "startTime": datetime.fromtimestamp(agent_run.started_at, tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(agent_run.ended_at, tz=timezone.utc).isoformat() if agent_run.ended_at else None,
                    "totalTasks": total_tasks,
                    "completedTasks": successful_tasks,
                    "averageScore": avg_score,
                    "successRate": success_rate,
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
                agent_runs.sort(key=lambda x: x["averageScore"], reverse=(sort_order == "desc"))
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
            logger.error(f"Error fetching agent runs list: {e}")
            return {"runs": [], "total": 0, "page": page, "limit": limit}
    
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
            tasks = await self._get_tasks_for_run(run_id, agent_run)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)

            tasks_data_full = await self._build_tasks_data(agent_run, tasks, task_solutions, evaluation_results)

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
            total_tasks = len(tasks_data_full) if tasks_data_full else max(len(tasks), len(evaluation_results))
            successful_tasks = len([t for t in tasks_data_full if t.status == TaskStatus.COMPLETED])
            if not successful_tasks and evaluation_results:
                successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            completed_tasks = successful_tasks
            failed_tasks = max(total_tasks - successful_tasks, 0)
            avg_score = (
                sum(t.score for t in tasks_data_full) / len(tasks_data_full)
                if tasks_data_full else
                (sum(er.final_score for er in evaluation_results) / len(evaluation_results) if evaluation_results else 0.0)
            )
            overall_score = int(round(format_score_as_percentage_float(avg_score)))
            
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
            websites = self._build_websites_overview(tasks_data_full)
            
            # Build tasks data if requested
            tasks_data = tasks_data_full if include_tasks else []
            
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
                validatorImage=f"https://autoppia.com/images/icons/validators/{validator.name or f'validator_{validator.uid}'}.png",
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
                image=f"https://autoppia.com/images/icons/validators/{validator.name or f'validator_{validator.uid}'}.png",
                description=f"{validator.name or f'Validator {validator.uid}'} Validator",
                website="https://autoppia.com",
                github="https://github.com/autoppia"
            )
            
            # Build agent info
            agent_info = AgentInfo(
                id=f"agent-{miner.uid}",
                name=miner.agent_name or f"Agent {miner.uid}",
                type="autoppia",
                image=miner.agent_image or f"https://autoppia.com/images/icons/agents/agent_{miner.uid}.png",
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
            tasks = await self._get_tasks_for_run(run_id, agent_run)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)

            tasks_data_full = await self._build_tasks_data(agent_run, tasks, task_solutions, evaluation_results)
            
            # Calculate basic metrics
            total_tasks = len(tasks_data_full) if tasks_data_full else max(len(tasks), len(evaluation_results))
            successful_tasks = len([t for t in tasks_data_full if t.status == TaskStatus.COMPLETED])
            if not successful_tasks and evaluation_results:
                successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            failed_tasks = max(total_tasks - successful_tasks, 0)
            overall_score = int(
                (
                    sum(er.final_score for er in evaluation_results) / len(evaluation_results)
                    if evaluation_results else
                    (sum(t.score for t in tasks_data_full) / len(tasks_data_full) if tasks_data_full else 0.0)
                ) * 100
            ) if (evaluation_results or tasks_data_full) else 0
            success_rate = (successful_tasks / total_tasks * 100) if total_tasks > 0 else 0.0
            
            # Calculate average task duration
            if evaluation_results:
                avg_duration = sum(er.evaluation_time for er in evaluation_results) / len(evaluation_results)
            elif tasks_data_full:
                avg_duration = sum(t.duration for t in tasks_data_full) / len(tasks_data_full)
            else:
                avg_duration = 0.0
            
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
            performance_by_website = self._build_performance_by_website(tasks_data_full, evaluation_results)
            
            # Build performance by use case
            performance_by_use_case = self._build_performance_by_use_case(tasks_data_full, evaluation_results)
            
            return Statistics(
                runId=run_id,
                overallScore=overall_score,
                totalTasks=total_tasks,
                successfulTasks=successful_tasks,
                failedTasks=failed_tasks,
                websites=len({t.website for t in tasks_data_full if t.website}),
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
            tasks = await self._get_tasks_for_run(run_id, agent_run)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            tasks_data_full = await self._build_tasks_data(agent_run, tasks, task_solutions, evaluation_results)
            
            # Calculate metrics
            total_tasks = len(tasks_data_full) if tasks_data_full else max(len(tasks), len(evaluation_results))
            successful_tasks = len([t for t in tasks_data_full if t.status == TaskStatus.COMPLETED])
            if not successful_tasks and evaluation_results:
                successful_tasks = len([er for er in evaluation_results if er.final_score > 0.5])
            failed_tasks = max(total_tasks - successful_tasks, 0)
            overall_score = int(
                (
                    sum(er.final_score for er in evaluation_results) / len(evaluation_results)
                    if evaluation_results else
                    (sum(t.score for t in tasks_data_full) / len(tasks_data_full) if tasks_data_full else 0.0)
                ) * 100
            ) if (evaluation_results or tasks_data_full) else 0
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
            for task in tasks_data_full:
                website = task.website or "unknown"
                matching_result = next((er for er in evaluation_results if er.task_id == task.taskId), None)
                score = matching_result.final_score if matching_result else task.score
                if website not in website_scores:
                    website_scores[website] = []
                website_scores[website].append(score)
            
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
            for task in tasks_data_full:
                use_case = task.useCase or "unknown"
                matching_result = next((er for er in evaluation_results if er.task_id == task.taskId), None)
                score = matching_result.final_score if matching_result else task.score
                if use_case not in use_case_scores:
                    use_case_scores[use_case] = []
                use_case_scores[use_case].append(score)
            
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
            
            agent_run_doc = await self.db.agent_evaluation_runs.find_one({"agent_run_id": run_id})
            if not agent_run_doc:
                return {"tasks": [], "total": 0, "page": page, "limit": limit}
            agent_run = AgentEvaluationRun(**agent_run_doc)

            # Get tasks
            tasks = await self._get_tasks_for_run(run_id, agent_run)
            task_solutions = await self._get_task_solutions_for_run(run_id)
            evaluation_results = await self._get_evaluation_results_for_run(run_id)
            
            # Build tasks data
            tasks_data = await self._build_tasks_data(agent_run, tasks, task_solutions, evaluation_results)
            
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
            tasks = await self._get_tasks_for_run(run_id, agent_run)
            
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
    async def _get_tasks_for_run(self, run_id: str, agent_run: Optional[AgentEvaluationRun] = None) -> List[SchemaTask]:
        """Get all tasks for an agent run, normalizing legacy mock documents when needed."""
        tasks_docs = await self.db.tasks.find({"agent_run_id": run_id}).to_list(length=1000)
        if not tasks_docs:
            # Fallback for datasets that still use camelCase identifiers.
            legacy_docs = await self.db.tasks.find().to_list(length=1000)
            tasks_docs = [doc for doc in legacy_docs if doc.get("agentRunId") == run_id]
        
        round_id = agent_run.round_id if agent_run else None
        normalized: List[SchemaTask] = []
        for raw_doc in tasks_docs:
            try:
                normalized_doc = self._normalize_task_document(raw_doc, run_id, round_id)
                normalized.append(SchemaTask(**normalized_doc))
            except ValidationError as exc:
                logger.warning(
                    "Skipping task %s due to validation error: %s",
                    raw_doc.get("task_id") or raw_doc.get("taskId"),
                    exc
                )
        return normalized
    
    async def _get_task_solutions_for_run(self, run_id: str) -> List[TaskSolution]:
        """Get all task solutions for an agent run."""
        solutions_docs = await self.db.task_solutions.find({"agent_run_id": run_id}).to_list(length=1000)
        normalized: List[TaskSolution] = []
        for raw_doc in solutions_docs:
            doc = self._normalize_task_solution_document(raw_doc)
            try:
                normalized.append(TaskSolution(**doc))
            except ValidationError as exc:
                logger.warning(
                    "Skipping task solution %s due to validation error: %s",
                    raw_doc.get("task_solution_id") or raw_doc.get("solution_id"),
                    exc
                )
        return normalized
    
    async def _get_evaluation_results_for_run(self, run_id: str) -> List[EvaluationResult]:
        """Get all evaluation results for an agent run."""
        results_docs = await self.db.evaluation_results.find({"agent_run_id": run_id}).to_list(length=1000)
        return [EvaluationResult(**doc) for doc in results_docs]
    
    def _normalize_task_document(
        self,
        raw_doc: Dict[str, Any],
        run_id: str,
        round_id: Optional[str]
    ) -> Dict[str, Any]:
        """Normalize legacy task documents into the schema used by the service."""
        doc = dict(raw_doc)
        task_id = doc.get("task_id") or doc.get("taskId")
        agent_run_id = doc.get("agent_run_id") or doc.get("agentRunId") or run_id
        derived_round_id = doc.get("round_id") or doc.get("roundId") or round_id or "round_000"
        
        url = doc.get("url") or doc.get("URL") or doc.get("websiteUrl")
        if not url:
            metadata = doc.get("metadata") or {}
            url = metadata.get("url")
        if not url:
            website = doc.get("website")
            if website:
                slug = str(website).lower().replace(" ", "-")
                url = f"https://{slug}.com"
        if not url:
            url = f"https://autoppia.local/{task_id or 'task'}"
        
        # Normalize timestamps if available
        start_time = doc.pop("start_time", None) or doc.pop("startTime", None)
        end_time = doc.pop("end_time", None) or doc.pop("endTime", None)
        
        normalized = {
            "task_id": task_id,
            "round_id": derived_round_id,
            "agent_run_id": agent_run_id,
            "scope": doc.get("scope", "local"),
            "is_web_real": doc.get("is_web_real") or doc.get("isWebReal", False),
            "web_project_id": doc.get("web_project_id") or doc.get("webProjectId"),
            "url": url,
            "prompt": doc.get("prompt") or doc.get("description") or "",
            "html": doc.get("html", ""),
            "clean_html": doc.get("clean_html", ""),
            "interactive_elements": doc.get("interactive_elements"),
            "screenshot": doc.get("screenshot"),
            "screenshot_description": doc.get("screenshot_description"),
            "specifications": doc.get("specifications", {}),
            "tests": doc.get("tests", []),
            "milestones": doc.get("milestones"),
            "relevant_data": doc.get("relevant_data", {}),
            "success_criteria": doc.get("success_criteria"),
            "use_case": doc.get("use_case") or doc.get("useCase"),
            "should_record": doc.get("should_record", False),
            "extras": doc.get("extras", {}),
        }
        if start_time is not None:
            normalized["start_time"] = self._parse_datetime(start_time, as_timestamp=True)
        if end_time is not None:
            normalized["end_time"] = self._parse_datetime(end_time, as_timestamp=True)
        if "metadata" in doc:
            normalized["metadata"] = doc["metadata"]
        if "website" in doc:
            normalized["website"] = doc["website"]
        if "status" in doc:
            normalized["status"] = doc["status"]
        if "logs" in doc:
            normalized["logs"] = doc["logs"]
        
        return normalized
    
    def _normalize_task_solution_document(self, raw_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize task solution documents to ensure key fields are present."""
        doc = dict(raw_doc)
        doc["solution_id"] = doc.get("solution_id") or doc.get("task_solution_id")
        doc["task_id"] = doc.get("task_id") or doc.get("taskId")
        doc["agent_run_id"] = doc.get("agent_run_id") or doc.get("agentRunId")
        doc["round_id"] = doc.get("round_id") or doc.get("roundId")
        if "actions" not in doc or not doc["actions"]:
            # Legacy datasets may only have logs; convert them into placeholder actions.
            logs = doc.get("logs") or []
            doc["actions"] = [
                {"type": "log", "attributes": {"message": log}}
                for log in logs
            ]
        return doc
    
    async def _build_tasks_data(
        self,
        agent_run: AgentEvaluationRun,
        tasks: List[SchemaTask],
        task_solutions: List[TaskSolution],
        evaluation_results: List[EvaluationResult]
    ) -> List[Task]:
        """Build tasks data with domain-aware fallbacks."""
        task_index = {task.task_id: task for task in tasks if getattr(task, "task_id", None)}
        solutions_index = self._index_task_solutions(task_solutions)
        evaluations_index = self._index_evaluations(evaluation_results)
        
        ordered_ids: List[str] = list(task_index.keys())
        for task_id in evaluations_index:
            if task_id not in ordered_ids:
                ordered_ids.append(task_id)
        
        tasks_data: List[Task] = []
        for position, task_id in enumerate(ordered_ids):
            task = task_index.get(task_id)
            evaluation = self._select_primary_evaluation(evaluations_index.get(task_id))
            solution = self._select_primary_solution(solutions_index.get(task_id))
            composed = self._compose_task_entry(agent_run, task_id, task, solution, evaluation, position)
            if composed:
                tasks_data.append(composed)
        
        return tasks_data
    
    def _index_task_solutions(self, task_solutions: Iterable[TaskSolution]) -> Dict[str, List[TaskSolution]]:
        index: Dict[str, List[TaskSolution]] = defaultdict(list)
        for solution in task_solutions:
            if getattr(solution, "task_id", None):
                index[solution.task_id].append(solution)
        return index
    
    def _index_evaluations(self, evaluation_results: Iterable[EvaluationResult]) -> Dict[str, List[EvaluationResult]]:
        index: Dict[str, List[EvaluationResult]] = defaultdict(list)
        for result in evaluation_results:
            if getattr(result, "task_id", None):
                index[result.task_id].append(result)
        return index
    
    def _select_primary_solution(self, solutions: Optional[Iterable[TaskSolution]]) -> Optional[TaskSolution]:
        if not solutions:
            return None
        return max(
            solutions,
            key=lambda sol: self._parse_datetime(getattr(sol, "created_at", None), as_timestamp=True) or 0
        )
    
    def _select_primary_evaluation(self, evaluations: Optional[Iterable[EvaluationResult]]) -> Optional[EvaluationResult]:
        if not evaluations:
            return None
        return max(
            evaluations,
            key=lambda res: getattr(res, "evaluation_time", None) or 0
        )
    
    def _compose_task_entry(
        self,
        agent_run: AgentEvaluationRun,
        task_id: str,
        task: Optional[SchemaTask],
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult],
        position: int
    ) -> Optional[Task]:
        if not task_id:
            return None
        
        prompt = self._resolve_prompt(task, evaluation)
        website = self._resolve_website(task, solution)
        use_case = self._resolve_use_case(task)
        score = evaluation.final_score if evaluation else 0.0
        status = self._resolve_status(task, solution, evaluation, score)
        duration = self._resolve_duration(solution, evaluation)
        start_dt = self._resolve_start_datetime(task, solution, agent_run, position)
        end_dt = self._resolve_end_datetime(start_dt, duration)
        logs = self._resolve_logs(task, solution, evaluation)
        screenshots = self._resolve_screenshots(task, solution)
        actions = self._resolve_actions(solution, evaluation, start_dt, status)
        
        return Task(
            taskId=task_id,
            website=website,
            useCase=use_case,
            prompt=prompt,
            status=status,
            score=score,
            duration=int(duration),
            startTime=self._datetime_to_iso(start_dt),
            endTime=self._datetime_to_iso(end_dt),
            actions=actions,
            screenshots=screenshots,
            logs=logs
        )
    
    def _resolve_prompt(self, task: Optional[SchemaTask], evaluation: Optional[EvaluationResult]) -> str:
        if task and getattr(task, "prompt", None):
            return str(task.prompt)
        if evaluation and evaluation.feedback and getattr(evaluation.feedback, "task_prompt", None):
            return str(evaluation.feedback.task_prompt)
        return "Prompt not available"
    
    def _resolve_website(self, task: Optional[SchemaTask], solution: Optional[TaskSolution]) -> str:
        if task:
            if getattr(task, "website", None):
                return str(task.website)
            url = getattr(task, "url", "")
            extracted = self._extract_website_from_url(url) if url else ""
            if extracted:
                return extracted
            metadata = getattr(task, "metadata", {})
            if isinstance(metadata, dict) and metadata.get("website"):
                return str(metadata["website"])
        if solution and getattr(solution, "metadata", None):
            metadata = solution.metadata  # type: ignore[attr-defined]
            if isinstance(metadata, dict) and metadata.get("website"):
                return str(metadata["website"])
        return "unknown"
    
    def _resolve_use_case(self, task: Optional[SchemaTask]) -> str:
        if not task:
            return "unknown"
        use_case = getattr(task, "use_case", None)
        if isinstance(use_case, dict):
            return str(use_case.get("name") or use_case.get("id") or "unknown")
        if isinstance(use_case, str):
            return use_case
        return "unknown"
    
    def _resolve_status(
        self,
        task: Optional[SchemaTask],
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult],
        score: float
    ) -> TaskStatus:
        candidates = []
        if task and getattr(task, "status", None):
            candidates.append(str(task.status))
        if solution and getattr(solution, "status", None):
            candidates.append(str(solution.status))
        
        for candidate in candidates:
            normalized = candidate.lower()
            if normalized == "completed":
                return TaskStatus.COMPLETED
            if normalized == "failed":
                return TaskStatus.FAILED
            if normalized == "running":
                return TaskStatus.RUNNING
            if normalized == "pending":
                return TaskStatus.PENDING
            if normalized == "skipped":
                return TaskStatus.SKIPPED
        
        return TaskStatus.COMPLETED if score >= 0.5 else TaskStatus.FAILED
    
    def _resolve_duration(self, solution: Optional[TaskSolution], evaluation: Optional[EvaluationResult]) -> float:
        if evaluation and getattr(evaluation, "evaluation_time", None):
            return float(evaluation.evaluation_time)
        if solution and getattr(solution, "execution_time", None):
            return float(solution.execution_time)
        return 0.0
    
    def _resolve_start_datetime(
        self,
        task: Optional[SchemaTask],
        solution: Optional[TaskSolution],
        agent_run: AgentEvaluationRun,
        position: int
    ) -> Optional[datetime]:
        candidates = []
        if task and getattr(task, "start_time", None):
            start_float = getattr(task, "start_time")
            if isinstance(start_float, (int, float)):
                candidates.append(datetime.fromtimestamp(start_float, tz=timezone.utc))
        if solution and getattr(solution, "created_at", None):
            created_at = getattr(solution, "created_at")
            parsed = self._parse_datetime(created_at)
            if parsed:
                candidates.append(parsed)
        if candidates:
            return min(candidates)
        
        base = self._parse_datetime(agent_run.started_at)
        if base:
            return base + timedelta(seconds=position * 5)
        return None
    
    def _resolve_end_datetime(self, start_dt: Optional[datetime], duration: float) -> Optional[datetime]:
        if not start_dt:
            return None
        return start_dt + timedelta(seconds=duration)
    
    def _resolve_logs(
        self,
        task: Optional[SchemaTask],
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult]
    ) -> List[str]:
        if solution and getattr(solution, "logs", None):
            return [str(entry) for entry in solution.logs]  # type: ignore[attr-defined]
        if task and getattr(task, "logs", None):
            return [str(entry) for entry in task.logs]  # type: ignore[attr-defined]
        if evaluation and getattr(evaluation, "execution_history", None):
            return [str(entry) for entry in evaluation.execution_history]
        return []
    
    def _resolve_screenshots(
        self,
        task: Optional[SchemaTask],
        solution: Optional[TaskSolution]
    ) -> List[str]:
        screenshots: List[str] = []
        if solution and getattr(solution, "screenshots", None):
            screenshots = [str(item) for item in solution.screenshots]  # type: ignore[attr-defined]
        elif task and getattr(task, "screenshots", None):
            screenshots = [str(item) for item in task.screenshots]  # type: ignore[attr-defined]
        return screenshots
    
    def _resolve_actions(
        self,
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult],
        start_dt: Optional[datetime],
        status: TaskStatus
    ) -> List[Action]:
        raw_actions: List[Dict[str, Any]] = []
        if solution and getattr(solution, "actions", None):
            for action in solution.actions:
                if isinstance(action, dict):
                    raw_actions.append(action)
                else:
                    raw_actions.append({
                        "type": getattr(action, "type", "log"),
                        "selector": getattr(action, "attributes", {}).get("selector"),
                        "value": getattr(action, "attributes", {}).get("value"),
                        "duration": getattr(action, "attributes", {}).get("duration", 0.0),
                        "success": getattr(action, "attributes", {}).get("success", status == TaskStatus.COMPLETED)
                    })
        elif evaluation and getattr(evaluation, "execution_history", None):
            for entry in evaluation.execution_history:
                raw_actions.append({
                    "type": "log",
                    "value": str(entry),
                    "success": status == TaskStatus.COMPLETED
                })
        
        actions: List[Action] = []
        for index, raw_action in enumerate(raw_actions, start=1):
            action_time = start_dt + timedelta(seconds=index) if start_dt else datetime.fromtimestamp(0, tz=timezone.utc)
            actions.append(Action(
                id=f"{index}",
                type=str(raw_action.get("type", "log")),
                selector=raw_action.get("selector"),
                value=raw_action.get("value"),
                timestamp=self._datetime_to_iso(action_time),
                duration=float(raw_action.get("duration", 0.0) or 0.0),
                success=bool(raw_action.get("success", status == TaskStatus.COMPLETED))
            ))
        return actions
    
    def _build_websites_overview(self, tasks: List[Task]) -> List[Website]:
        website_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"tasks": 0, "successful": 0, "failed": 0, "scores": []})
        for task in tasks:
            website = task.website or "unknown"
            bucket = website_data[website]
            bucket["tasks"] += 1
            bucket["scores"].append(task.score)
            if task.status == TaskStatus.COMPLETED:
                bucket["successful"] += 1
            elif task.status == TaskStatus.FAILED:
                bucket["failed"] += 1
        
        websites: List[Website] = []
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
    
    def _build_performance_by_website(
        self,
        tasks: List[Task],
        evaluation_results: List[EvaluationResult]
    ) -> List[PerformanceByWebsite]:
        scores_by_task = {er.task_id: er.final_score for er in evaluation_results if getattr(er, "task_id", None)}
        durations_by_task = {er.task_id: er.evaluation_time for er in evaluation_results if getattr(er, "task_id", None)}
        
        website_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "tasks": 0,
            "successful": 0,
            "failed": 0,
            "scores": [],
            "durations": []
        })
        
        for task in tasks:
            website = task.website or "unknown"
            bucket = website_data[website]
            bucket["tasks"] += 1
            score = scores_by_task.get(task.taskId, task.score)
            bucket["scores"].append(score)
            if task.status == TaskStatus.COMPLETED:
                bucket["successful"] += 1
            elif task.status == TaskStatus.FAILED:
                bucket["failed"] += 1
            bucket["durations"].append(durations_by_task.get(task.taskId, task.duration))
        
        performance: List[PerformanceByWebsite] = []
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
    
    def _build_performance_by_use_case(
        self,
        tasks: List[Task],
        evaluation_results: List[EvaluationResult]
    ) -> List[PerformanceByUseCase]:
        scores_by_task = {er.task_id: er.final_score for er in evaluation_results if getattr(er, "task_id", None)}
        durations_by_task = {er.task_id: er.evaluation_time for er in evaluation_results if getattr(er, "task_id", None)}
        
        use_case_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "tasks": 0,
            "successful": 0,
            "failed": 0,
            "scores": [],
            "durations": []
        })
        
        for task in tasks:
            use_case = task.useCase or "unknown"
            bucket = use_case_data[use_case]
            bucket["tasks"] += 1
            score = scores_by_task.get(task.taskId, task.score)
            bucket["scores"].append(score)
            if task.status == TaskStatus.COMPLETED:
                bucket["successful"] += 1
            elif task.status == TaskStatus.FAILED:
                bucket["failed"] += 1
            bucket["durations"].append(durations_by_task.get(task.taskId, task.duration))
        
        performance: List[PerformanceByUseCase] = []
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
    
    def _parse_datetime(self, value: Any, as_timestamp: bool = False) -> Optional[Any]:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt if not as_timestamp else dt.timestamp()
        if isinstance(value, (int, float)):
            return value if as_timestamp else datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            value = value.strip()
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt if not as_timestamp else dt.timestamp()
            except ValueError:
                return None
        return None
    
    def _datetime_to_iso(self, value: Optional[datetime]) -> str:
        if not value:
            return ""
        return value.astimezone(timezone.utc).isoformat()
    
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

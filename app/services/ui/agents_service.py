from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import math
import random
from app.utils.score_formatter import format_score_as_percentage_float, format_score_round_data
from app.models.ui.agents import (
    Agent, AgentRun, AgentActivity, 
    AgentStatistics, AgentComparison, AgentComparisonResponse,
    AgentListQuery, AgentRunsQuery, 
    AgentActivityQuery, AllAgentActivityQuery, AgentCompareRequest,
    AgentType, AgentStatus, RunStatus, TaskStatus, ActivityType,
    TimeRange, Granularity, ScoreDistribution, PerformanceTrend,
    TopAgent, MostActiveAgent, PerformanceDistribution,
    ComparisonMetrics, AgentComparisonMetrics, ScoreRoundDataPoint
)
from app.models.ui.miners import Miner, MinerRun, MinerStatus, MinerPerformanceQuery, MinerRunsQuery
from app.services.ui.miners_service import MinersService


class AgentsService:
    """Service class for managing agent-related operations."""
    
    def __init__(self):
        """Initialize the agents service."""
        self._miners_service = MinersService()
        self._mock_agents = self._generate_mock_agents()
        self._mock_runs = self._generate_mock_runs()
        self._mock_activities = self._generate_mock_activities()
    
    def get_agents(self, query: AgentListQuery) -> Tuple[List[Agent], int]:
        """Get paginated list of agents with filtering and sorting."""
        # Get miners data and convert to agents
        miners = self._miners_service._mock_miners.copy()
        
        # Convert miners to agents
        agents = []
        for miner in miners:
            is_sota = getattr(miner, "isSota", False)
            agent_uid = None if is_sota else miner.uid
            agent_hotkey = None if is_sota else miner.hotkey
            taostats_url = None if is_sota else miner.taostatsUrl
            agent_identifier = miner.id
            if is_sota:
                agent_identifier = miner.name.lower().replace(" ", "-")

            current_rank = 0 if is_sota else self._calculate_current_rank(miner)
            best_rank = 0 if is_sota else self._calculate_best_rank_ever(miner)
            alpha_prizes = 0.0 if is_sota else self._calculate_alpha_prizes(miner)

            agent = Agent(
                id=agent_identifier,
                uid=agent_uid,
                name=miner.name,
                hotkey=agent_hotkey,
                type=self._get_agent_type_from_miner(miner),
                imageUrl=miner.imageUrl,
                githubUrl=miner.githubUrl,
                taostatsUrl=taostats_url,
                isSota=is_sota,
                description=miner.description,
                version="1.0.0",
                status=self._convert_miner_status_to_agent_status(miner.status),
                totalRuns=miner.totalRuns,
                successfulRuns=miner.successfulRuns,
                currentScore=miner.averageScore,  # Already in percentage format
                currentTopScore=miner.bestScore,  # Already in percentage format
                currentRank=current_rank,
                bestRankEver=best_rank,
                roundsParticipated=miner.totalRuns,  # Using totalRuns as rounds participated
                alphaWonInPrizes=alpha_prizes,
                averageDuration=miner.averageDuration,
                totalTasks=miner.totalTasks,
                completedTasks=miner.completedTasks,
                lastSeen=datetime.fromisoformat(miner.lastSeen.replace('Z', '+00:00')),
                createdAt=datetime.fromisoformat(miner.createdAt.replace('Z', '+00:00')),
                updatedAt=datetime.fromisoformat(miner.updatedAt.replace('Z', '+00:00'))
            )
            agents.append(agent)
        
        # Apply filters
        if query.type:
            agents = [a for a in agents if a.type == query.type]
        
        if query.status:
            agents = [a for a in agents if a.status == query.status]
        
        if query.search:
            search_lower = query.search.lower()
            agents = [a for a in agents if 
                     search_lower in a.name.lower() or 
                     (a.description and search_lower in a.description.lower())]
        
        # Apply sorting
        reverse = query.sortOrder == "desc"
        if query.sortBy == "name":
            agents.sort(key=lambda x: x.name, reverse=reverse)
        elif query.sortBy == "currentScore":
            agents.sort(key=lambda x: x.currentScore, reverse=reverse)
        elif query.sortBy == "totalRuns":
            agents.sort(key=lambda x: x.totalRuns, reverse=reverse)
        elif query.sortBy == "lastSeen":
            agents.sort(key=lambda x: x.lastSeen, reverse=reverse)
        
        # Apply pagination
        total = len(agents)
        start = (query.page - 1) * query.limit
        end = start + query.limit
        agents = agents[start:end]
        
        return agents, total
    
    def get_agent_by_id(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        # Get miners data and find the specific agent
        miners = self._miners_service._mock_miners
        # Try multiple matching strategies
        miner = None
        
        # First try exact ID match
        miner = next((m for m in miners if m.id == agent_id), None)
        
        # Then try name-based matching
        if not miner:
            agent_id_lower = agent_id.lower()
            for m in miners:
                # Try various name formats
                name_variants = [
                    m.name.lower(),
                    m.name.lower().replace(' ', '-'),
                    m.name.lower().replace(' ', '_'),
                    m.name.lower().replace(' ', ''),
                ]
                if agent_id_lower in name_variants:
                    miner = m
                    break
        
        # Special case for known agents
        if not miner:
            if agent_id.lower() == 'openai-cua':
                miner = next((m for m in miners if 'openai' in m.name.lower() and m.isSota), None)
            elif agent_id.lower() == 'anthropic-cua':
                miner = next((m for m in miners if 'anthropic' in m.name.lower() and m.isSota), None)
            elif agent_id.lower() == 'browser-use-agent':
                miner = next((m for m in miners if 'browser' in m.name.lower() and m.isSota), None)
            elif agent_id.lower() == 'autoppia-bittensor':
                miner = next((m for m in miners if 'autoppia' in m.name.lower()), None)
        
        if not miner:
            return None
        
        # Convert miner to agent
        is_sota = getattr(miner, "isSota", False)
        agent_uid = None if is_sota else miner.uid
        agent_hotkey = None if is_sota else miner.hotkey
        taostats_url = None if is_sota else miner.taostatsUrl
        agent_identifier = miner.id
        if is_sota:
            agent_identifier = miner.name.lower().replace(" ", "-")

        agent = Agent(
            id=agent_identifier,
            uid=agent_uid,
            name=miner.name,
            hotkey=agent_hotkey,
            type=self._get_agent_type_from_miner(miner),
            imageUrl=miner.imageUrl,
            githubUrl=miner.githubUrl,
            taostatsUrl=taostats_url,
            isSota=is_sota,
            description=miner.description,
            version="1.0.0",
            status=self._convert_miner_status_to_agent_status(miner.status),
            totalRuns=miner.totalRuns,
            successfulRuns=miner.successfulRuns,
            currentScore=miner.averageScore,
            currentTopScore=miner.bestScore,
            currentRank=0 if is_sota else self._calculate_current_rank(miner),
            bestRankEver=0 if is_sota else self._calculate_best_rank_ever(miner),
            roundsParticipated=miner.totalRuns,
            alphaWonInPrizes=0.0 if is_sota else self._calculate_alpha_prizes(miner),
            averageDuration=miner.averageDuration,
            totalTasks=miner.totalTasks,
            completedTasks=miner.completedTasks,
            lastSeen=datetime.fromisoformat(miner.lastSeen.replace('Z', '+00:00')),
            createdAt=datetime.fromisoformat(miner.createdAt.replace('Z', '+00:00')),
            updatedAt=datetime.fromisoformat(miner.updatedAt.replace('Z', '+00:00'))
        )
        return agent
    
    def get_agent_score_round_data(self, agent_id: str, limit: int = 50) -> List[ScoreRoundDataPoint]:
        """Get score vs round data points for an agent."""
        agent = self.get_agent_by_id(agent_id)
        if not agent:
            return []
        
        # Use miners service to get runs data
        try:
            uid = agent.uid
            if uid is None:
                return []
            
            # Get miner runs to extract score vs round data
            miner_runs_query = MinerRunsQuery(
                page=1,
                limit=limit,
                sortBy="startTime",
                sortOrder="desc"
            )
            
            miner_runs, _ = self._miners_service.get_miner_runs(uid, miner_runs_query)
            
            if not miner_runs:
                return []

            validator_round_ids = [run.roundId for run in miner_runs]
            top_scores_by_round = self._miners_service.get_round_top_scores(validator_round_ids)

            # Convert miner runs to score round data points
            score_round_data = []
            for miner_run in miner_runs:
                if miner_run.score is not None:
                    top_score = top_scores_by_round.get(miner_run.roundId)
                    data_point = ScoreRoundDataPoint(
                        validator_round_id=miner_run.roundId,
                        score=format_score_as_percentage_float(miner_run.score),
                        rank=miner_run.ranking,
                        top_score=format_score_as_percentage_float(top_score) if top_score is not None else None,
                        reward=0.0,  # Default reward, could be enhanced later
                        timestamp=datetime.fromisoformat(miner_run.startTime.replace('Z', '+00:00'))
                    )
                    score_round_data.append(data_point)
            
            # Sort by validator_round_id descending (most recent first)
            score_round_data.sort(key=lambda x: x.validator_round_id, reverse=True)
            
            return score_round_data
        except Exception as e:
            print(f"Error getting score round data: {e}")
            return []
    
    def _get_agent_type_from_miner(self, miner: Miner) -> AgentType:
        """Convert miner to agent type."""
        if miner.isSota:
            if "openai" in miner.name.lower():
                return AgentType.OPENAI
            elif "anthropic" in miner.name.lower():
                return AgentType.ANTHROPIC
            elif "browser" in miner.name.lower():
                return AgentType.BROWSER_USE
            else:
                return AgentType.CUSTOM
        else:
            if "autoppia" in miner.name.lower():
                return AgentType.AUTOPPIA
            else:
                return AgentType.CUSTOM
    
    def _convert_miner_status_to_agent_status(self, miner_status: MinerStatus) -> AgentStatus:
        """Convert miner status to agent status."""
        if miner_status == MinerStatus.ACTIVE:
            return AgentStatus.ACTIVE
        elif miner_status == MinerStatus.INACTIVE:
            return AgentStatus.INACTIVE
        elif miner_status == MinerStatus.MAINTENANCE:
            return AgentStatus.MAINTENANCE
        else:
            return AgentStatus.ACTIVE
    
    def _calculate_current_rank(self, miner) -> int:
        """Calculate current rank based on average score."""
        # Mock calculation - in real implementation, this would be based on current leaderboard
        if miner.averageScore >= 0.9:
            return random.randint(1, 5)
        elif miner.averageScore >= 0.8:
            return random.randint(6, 15)
        elif miner.averageScore >= 0.7:
            return random.randint(16, 30)
        else:
            return random.randint(31, 50)
    
    def _calculate_best_rank_ever(self, miner) -> int:
        """Calculate best rank ever achieved."""
        # Mock calculation - best rank should be better than or equal to current rank
        current_rank = self._calculate_current_rank(miner)
        return random.randint(1, max(1, current_rank - 1))
    
    def _calculate_alpha_prizes(self, miner) -> float:
        """Calculate alpha won in prizes."""
        # Mock calculation based on performance
        base_prize = 100.0
        performance_multiplier = miner.averageScore
        rounds_multiplier = min(miner.totalRuns / 100, 2.0)  # Cap at 2x for rounds
        return round(base_prize * performance_multiplier * rounds_multiplier, 2)
    
    
    
    def _convert_miner_run_to_agent_run(self, miner_run: MinerRun) -> AgentRun:
        """Convert miner run to agent run format."""
        return AgentRun(
            runId=miner_run.runId,
            agentId=miner_run.agentId,
            validatorId=miner_run.validatorId,
            roundId=miner_run.roundId,
            score=miner_run.score,
            ranking=miner_run.ranking,
            status=RunStatus.COMPLETED if miner_run.status.value == "completed" else RunStatus.FAILED,
            duration=miner_run.duration,
            completedTasks=miner_run.completedTasks,
            totalTasks=miner_run.totalTasks,
            startTime=datetime.fromisoformat(miner_run.startTime.replace('Z', '+00:00')),
            endTime=datetime.fromisoformat(miner_run.endTime.replace('Z', '+00:00')) if miner_run.endTime else None,
            createdAt=datetime.fromisoformat(miner_run.createdAt.replace('Z', '+00:00'))
        )
    
    def get_agent_runs(self, agent_id: str, query: AgentRunsQuery) -> Tuple[List[AgentRun], int]:
        """Get paginated list of agent runs."""
        # Get agent to find UID
        agent = self.get_agent_by_id(agent_id)
        if not agent:
            return [], 0
        
        # Use miners service to get runs data
        try:
            uid = agent.uid
            if uid is None:
                return [], 0
            
            # Convert AgentRunsQuery to MinerRunsQuery
            miner_query = MinerRunsQuery(
                page=query.page,
                limit=query.limit,
                roundId=query.roundId,
                validatorId=query.validatorId,
                status=query.status,
                startDate=query.startDate,
                endDate=query.endDate,
                sortBy=query.sortBy,
                sortOrder=query.sortOrder
            )
            
            miner_runs = self._miners_service.get_miner_runs(uid, miner_query)
            
            if not miner_runs:
                return [], 0
            
            # Convert miner runs to agent runs format
            agent_runs = []
            for miner_run in miner_runs[0]:  # miner_runs is (runs, total)
                agent_run = self._convert_miner_run_to_agent_run(miner_run)
                agent_runs.append(agent_run)
            
            return agent_runs, miner_runs[1]  # Return runs and total
        except Exception as e:
            print(f"Error getting miner runs: {e}")
            return [], 0
    
    def get_agent_run_by_id(self, agent_id: str, run_id: str) -> Optional[AgentRun]:
        """Get agent run by ID."""
        return next((r for r in self._mock_runs 
                   if r.agentId == agent_id and r.runId == run_id), None)
    
    def get_agent_activity(self, agent_id: str, query: AgentActivityQuery) -> Tuple[List[AgentActivity], int]:
        """Get agent activity."""
        activities = [a for a in self._mock_activities if a.agentId == agent_id]
        
        # Apply filters
        if query.type:
            activities = [a for a in activities if a.type == query.type]
        
        if query.since:
            activities = [a for a in activities if a.timestamp >= query.since]
        
        # Sort by timestamp (most recent first)
        activities.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Apply pagination
        total = len(activities)
        start = query.offset
        end = start + query.limit
        activities = activities[start:end]
        
        return activities, total
    
    def get_all_agent_activity(self, query: AllAgentActivityQuery) -> Tuple[List[AgentActivity], int]:
        """Get activity across all agents."""
        activities = self._mock_activities.copy()
        
        # Apply filters
        if query.agentId:
            activities = [a for a in activities if a.agentId == query.agentId]
        
        if query.type:
            activities = [a for a in activities if a.type == query.type]
        
        if query.since:
            activities = [a for a in activities if a.timestamp >= query.since]
        
        # Sort by timestamp (most recent first)
        activities.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Apply pagination
        total = len(activities)
        start = query.offset
        end = start + query.limit
        activities = activities[start:end]
        
        return activities, total
    
    def compare_agents(self, request: AgentCompareRequest) -> Optional[AgentComparisonResponse]:
        """Compare multiple agents."""
        # Validate agent IDs
        valid_agents = [a for a in self._mock_agents if a.id in request.agentIds]
        if len(valid_agents) != len(request.agentIds):
            return None
        
        # Calculate time range
        end_date = datetime.now()
        if request.endDate:
            end_date = request.endDate
        elif request.timeRange == TimeRange.SEVEN_DAYS:
            start_date = end_date - timedelta(days=7)
        else:
            start_date = datetime(2024, 1, 1)
        
        if request.startDate:
            start_date = request.startDate
        
        # Get performance data for each agent
        agent_comparisons = []
        for agent in valid_agents:
            performance = self.get_agent_performance(
                agent.id, 
                AgentPerformanceQuery(
                    timeRange=request.timeRange or TimeRange.SEVEN_DAYS,
                    startDate=request.startDate,
                    endDate=request.endDate
                )
            )
            
            if performance:
                metrics = AgentComparisonMetrics(
                    averageScore=performance.averageScore,
                    successRate=performance.successRate,
                    averageDuration=performance.averageDuration,
                    totalRuns=performance.totalRuns,
                    ranking=1  # Simplified ranking
                )
                
                agent_comparisons.append(AgentComparison(
                    agentId=agent.id,
                    name=agent.name,
                    metrics=metrics
                ))
        
        # Calculate comparison metrics
        if agent_comparisons:
            best_performer = max(agent_comparisons, key=lambda x: x.metrics.averageScore)
            most_reliable = max(agent_comparisons, key=lambda x: x.metrics.successRate)
            fastest = min(agent_comparisons, key=lambda x: x.metrics.averageDuration)
            most_active = max(agent_comparisons, key=lambda x: x.metrics.totalRuns)
            
            comparison_metrics = ComparisonMetrics(
                bestPerformer=best_performer.agentId,
                mostReliable=most_reliable.agentId,
                fastest=fastest.agentId,
                mostActive=most_active.agentId
            )
        else:
            comparison_metrics = ComparisonMetrics(
                bestPerformer="",
                mostReliable="",
                fastest="",
                mostActive=""
            )
        
        return AgentComparisonResponse(
            agents=agent_comparisons,
            comparisonMetrics=comparison_metrics,
            timeRange={"start": start_date.isoformat(), "end": end_date.isoformat()}
        )
    
    def get_agent_statistics(self) -> AgentStatistics:
        """Get overall agent statistics."""
        total_agents = len(self._mock_agents)
        active_agents = len([a for a in self._mock_agents if a.status == AgentStatus.ACTIVE])
        inactive_agents = total_agents - active_agents
        
        total_runs = sum(a.totalRuns for a in self._mock_agents)
        successful_runs = sum(a.successfulRuns for a in self._mock_agents)
        average_success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0.0
        
        scores = [a.currentScore for a in self._mock_agents if a.currentScore > 0]
        average_score = sum(scores) / len(scores) if scores else 0.0
        
        # Top performing agent
        top_agent = max(self._mock_agents, key=lambda x: x.currentScore)
        top_performing_agent = TopAgent(
            id=top_agent.id,
            name=top_agent.name,
            score=top_agent.currentScore
        )
        
        # Most active agent
        most_active = max(self._mock_agents, key=lambda x: x.totalRuns)
        most_active_agent = MostActiveAgent(
            id=most_active.id,
            name=most_active.name,
            runs=most_active.totalRuns
        )
        
        # Performance distribution
        excellent = len([a for a in self._mock_agents if a.currentScore >= 0.9])
        good = len([a for a in self._mock_agents if 0.7 <= a.currentScore < 0.9])
        average = len([a for a in self._mock_agents if 0.5 <= a.currentScore < 0.7])
        poor = len([a for a in self._mock_agents if a.currentScore < 0.5])
        
        performance_distribution = PerformanceDistribution(
            excellent=excellent,
            good=good,
            average=average,
            poor=poor
        )
        
        return AgentStatistics(
            totalAgents=total_agents,
            activeAgents=active_agents,
            inactiveAgents=inactive_agents,
            totalRuns=total_runs,
            successfulRuns=successful_runs,
            averageSuccessRate=average_success_rate,
            averageScore=average_score,
            topPerformingAgent=top_performing_agent,
            mostActiveAgent=most_active_agent,
            performanceDistribution=performance_distribution,
            lastUpdated=datetime.now()
        )
    
    def _generate_mock_agents(self) -> List[Agent]:
        """Generate mock agent data."""
        agents = [
            Agent(
                id="autoppia-bittensor",
                name="Autoppia Bittensor",
                type=AgentType.AUTOPPIA,
                imageUrl="https://autoppia.com/icons/bittensor.webp",
                description="Autoppia's native Bittensor agent for web automation tasks",
                version="7.0.0",
                status=AgentStatus.ACTIVE,
                totalRuns=1247,
                successfulRuns=1089,
                currentScore=0.87,
                currentTopScore=0.95,
                currentRank=3,
                bestRankEver=1,
                roundsParticipated=1247,
                alphaWonInPrizes=108.5,
                averageDuration=32.5,
                totalTasks=12470,
                completedTasks=10890,
                lastSeen=datetime.now() - timedelta(minutes=5),
                createdAt=datetime(2024, 1, 15, 10, 0, 0),
                updatedAt=datetime.now()
            ),
            Agent(
                id="openai-cua",
                uid=None,
                name="OpenAI CUA",
                type=AgentType.OPENAI,
                imageUrl="https://openai.com/icons/openai.webp",
                description="OpenAI's Computer Use Agent for web automation",
                version="1.0.0",
                status=AgentStatus.ACTIVE,
                isSota=True,
                totalRuns=892,
                successfulRuns=756,
                currentScore=0.82,
                currentTopScore=0.91,
                currentRank=7,
                bestRankEver=2,
                roundsParticipated=892,
                alphaWonInPrizes=73.1,
                averageDuration=28.3,
                totalTasks=8920,
                completedTasks=7560,
                lastSeen=datetime.now() - timedelta(minutes=12),
                createdAt=datetime(2024, 1, 10, 8, 0, 0),
                updatedAt=datetime.now()
            ),
            Agent(
                id="anthropic-cua",
                uid=None,
                name="Anthropic CUA",
                type=AgentType.ANTHROPIC,
                imageUrl="https://anthropic.com/icons/anthropic.webp",
                description="Anthropic's Computer Use Agent",
                version="2.1.0",
                status=AgentStatus.ACTIVE,
                isSota=True,
                totalRuns=654,
                successfulRuns=567,
                currentScore=0.79,
                currentTopScore=0.88,
                currentRank=12,
                bestRankEver=5,
                roundsParticipated=654,
                alphaWonInPrizes=51.7,
                averageDuration=35.2,
                totalTasks=6540,
                completedTasks=5670,
                lastSeen=datetime.now() - timedelta(minutes=8),
                createdAt=datetime(2024, 1, 12, 14, 0, 0),
                updatedAt=datetime.now()
            ),
            Agent(
                id="browser-use-agent",
                uid=None,
                name="Browser Use Agent",
                type=AgentType.BROWSER_USE,
                imageUrl="https://browser-use.com/icons/browser-use.webp",
                description="Browser Use framework agent",
                version="0.3.0",
                status=AgentStatus.ACTIVE,
                isSota=True,
                totalRuns=423,
                successfulRuns=345,
                currentScore=0.74,
                currentTopScore=0.85,
                currentRank=18,
                bestRankEver=8,
                roundsParticipated=423,
                alphaWonInPrizes=31.3,
                averageDuration=42.1,
                totalTasks=4230,
                completedTasks=3450,
                lastSeen=datetime.now() - timedelta(minutes=15),
                createdAt=datetime(2024, 1, 8, 16, 0, 0),
                updatedAt=datetime.now()
            ),
            Agent(
                id="custom-agent-1",
                name="Custom Agent Alpha",
                type=AgentType.CUSTOM,
                imageUrl="https://stagehand.com/icons/stagehand.webp",
                description="Custom implementation for specialized tasks",
                version="1.2.0",
                status=AgentStatus.MAINTENANCE,
                totalRuns=234,
                successfulRuns=198,
                currentScore=0.71,
                currentTopScore=0.82,
                currentRank=25,
                bestRankEver=12,
                roundsParticipated=234,
                alphaWonInPrizes=16.6,
                averageDuration=38.7,
                totalTasks=2340,
                completedTasks=1980,
                lastSeen=datetime.now() - timedelta(hours=2),
                createdAt=datetime(2024, 1, 5, 12, 0, 0),
                updatedAt=datetime.now()
            )
        ]
        return agents
    
    def _generate_mock_runs(self) -> List[AgentRun]:
        """Generate mock agent run data."""
        runs = []
        agent_ids = ["autoppia-bittensor", "openai-cua", "anthropic-cua", "browser-use-agent", "custom-agent-1"]
        websites = ["Autozone", "Amazon", "Google", "GitHub", "Stack Overflow", "Reddit", "Wikipedia", "YouTube"]
        use_cases = ["Login", "Search", "Purchase", "Navigation", "Form Fill", "Data Extraction", "API Call", "File Upload"]
        
        for i in range(1000):
            agent_id = random.choice(agent_ids)
            start_time = datetime.now() - timedelta(days=random.randint(0, 30))
            duration = random.randint(300, 3600)  # 5 minutes to 1 hour
            end_time = start_time + timedelta(seconds=duration)
            
            # Generate tasks
            num_tasks = random.randint(5, 15)
            tasks = []
            completed_tasks = 0
            
            for j in range(num_tasks):
                task_duration = random.randint(30, 300)
                task_start = start_time + timedelta(seconds=j * 60)
                task_end = task_start + timedelta(seconds=task_duration)
                
                task_status = random.choice([TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.FAILED])
                if task_status == TaskStatus.COMPLETED:
                    completed_tasks += 1
                
                from app.models.ui.agents import Task
                task = Task(
                    taskId=f"task_{i}_{j}",
                    website=random.choice(websites),
                    useCase=random.choice(use_cases),
                    status=task_status,
                    score=random.uniform(0.6, 1.0) if task_status == TaskStatus.COMPLETED else 0.0,
                    duration=task_duration,
                    startTime=task_start,
                    endTime=task_end if task_status == TaskStatus.COMPLETED else None,
                    error=f"Task failed: {random.choice(['Timeout', 'Network error', 'Element not found'])}" if task_status == TaskStatus.FAILED else None
                )
                tasks.append(task)
            
            # Calculate run score and status
            if completed_tasks == num_tasks:
                status = RunStatus.COMPLETED
                score = random.uniform(0.8, 1.0)
            elif completed_tasks > num_tasks // 2:
                status = RunStatus.COMPLETED
                score = random.uniform(0.6, 0.8)
            else:
                status = random.choice([RunStatus.FAILED, RunStatus.TIMEOUT])
                score = random.uniform(0.0, 0.5)
            
            run = AgentRun(
                runId=f"run_{agent_id}_{i}",
                agentId=agent_id,
                roundId=random.randint(1, 25),
                validatorId=random.choice(["autoppia", "kraken", "roundtable21", "yuma", "tao5"]),
                startTime=start_time,
                endTime=end_time,
                status=status,
                totalTasks=num_tasks,
                completedTasks=completed_tasks,
                score=score,
                duration=duration,
                ranking=random.randint(1, 50) if status == RunStatus.COMPLETED else None,
                tasks=tasks,
                metadata={
                    "environment": "production",
                    "version": "7.0.0",
                    "resources": {
                        "cpu": random.randint(30, 80),
                        "memory": random.randint(40, 90),
                        "storage": random.randint(20, 70)
                    }
                }
            )
            runs.append(run)
        
        return runs
    
    def _generate_mock_activities(self) -> List[AgentActivity]:
        """Generate mock agent activity data."""
        activities = []
        agent_ids = ["autoppia-bittensor", "openai-cua", "anthropic-cua", "browser-use-agent", "custom-agent-1"]
        activity_types = [ActivityType.RUN_STARTED, ActivityType.RUN_COMPLETED, ActivityType.RUN_FAILED]
        
        for i in range(500):
            agent_id = random.choice(agent_ids)
            activity_type = random.choice(activity_types)
            timestamp = datetime.now() - timedelta(hours=random.randint(0, 72))
            
            if activity_type == ActivityType.RUN_STARTED:
                message = f"Agent {agent_id} started a new run"
                metadata = {
                    "runId": f"run_{agent_id}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"])
                }
            elif activity_type == ActivityType.RUN_COMPLETED:
                score = random.uniform(0.6, 1.0)
                duration = random.randint(300, 3600)
                message = f"Agent {agent_id} completed run with score {score:.2f}"
                metadata = {
                    "runId": f"run_{agent_id}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"]),
                    "score": score,
                    "duration": duration
                }
            else:  # RUN_FAILED
                message = f"Agent {agent_id} run failed"
                metadata = {
                    "runId": f"run_{agent_id}_{i}",
                    "roundId": random.randint(1, 25),
                    "validatorId": random.choice(["autoppia", "kraken", "roundtable21"]),
                    "error": random.choice(["Timeout", "Network error", "Validation failed"])
                }
            
            activity = AgentActivity(
                id=f"activity_{agent_id}_{i}",
                type=activity_type,
                agentId=agent_id,
                agentName=agent_id.replace("-", " ").title(),
                message=message,
                timestamp=timestamp,
                metadata=metadata
            )
            activities.append(activity)
        
        return activities
    
    def _generate_performance_trend(self, runs: List[AgentRun], start_date: datetime, 
                                  end_date: datetime, granularity: Granularity) -> List[PerformanceTrend]:
        """Generate performance trend data."""
        trend = []
        
        if granularity == Granularity.DAY:
            current = start_date
            while current <= end_date:
                next_day = current + timedelta(days=1)
                period_runs = [r for r in runs if current <= r.startTime < next_day]
                
                if period_runs:
                    scores = [r.score for r in period_runs if r.score > 0]
                    successful = len([r for r in period_runs if r.status == RunStatus.COMPLETED])
                    durations = [r.duration for r in period_runs if r.duration > 0]
                    
                    trend.append(PerformanceTrend(
                        period=current.strftime("%Y-%m-%d"),
                        score=sum(scores) / len(scores) if scores else 0.0,
                        successRate=(successful / len(period_runs) * 100) if period_runs else 0.0,
                        duration=sum(durations) / len(durations) if durations else 0.0
                    ))
                
                current = next_day
        
        return trend

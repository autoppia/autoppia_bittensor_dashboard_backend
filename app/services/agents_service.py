from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentEvaluationRunORM
from app.models.core import MinerInfo
from app.models.ui.agents import (
    Agent,
    AgentDetailResponse,
    AgentActivity,
    AgentActivityResponse,
    AgentComparison,
    AgentComparisonMetrics,
    AgentComparisonResponse,
    AgentListResponse,
    ActivityType,
    AgentPerformanceMetrics,
    AgentPerformanceResponse,
    AgentRunsResponse,
    AgentStatus,
    AgentType,
    AgentRun,
    PerformanceTrend,
    ScoreDistribution,
    AgentStatistics,
    AgentStatisticsResponse,
    ComparisonMetrics,
    MostActiveAgent,
    PerformanceDistribution,
    TopAgent,
    ScoreRoundDataPoint,
)
from app.services.rounds_service import AgentRunContext, RoundsService
from app.services.agent_runs_service import AgentRunsService

logger = logging.getLogger(__name__)


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _round_id_to_int(round_id: str) -> int:
    if "_" in round_id:
        try:
            return int(round_id.split("_", 1)[1])
        except ValueError:
            return 0
    return 0


def _ts(value: Optional[float]) -> float:
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    return float(value)


def _iso_ts(value: Optional[float]) -> str:
    if value is None or value == float("inf"):
        value = 0.0
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(0, tz=timezone.utc).isoformat()


@dataclass
class AgentAggregate:
    agent_id: str
    uid: Optional[int]
    miner: Optional[MinerInfo]
    is_sota: bool
    version: Optional[str]
    runs: List[AgentRunContext] = field(default_factory=list)
    total_runs: int = 0
    successful_runs: int = 0
    total_score: float = 0.0
    best_score: float = 0.0
    durations: List[float] = field(default_factory=list)
    total_tasks: int = 0
    completed_tasks: int = 0
    ranks: List[int] = field(default_factory=list)
    rounds: set = field(default_factory=set)
    first_seen: float = field(default_factory=lambda: float("inf"))
    last_seen: float = field(default_factory=lambda: 0.0)


class AgentsService:
    """SQL-backed service for agent summaries."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)
        self.agent_runs_service = AgentRunsService(session)

    async def list_agents(
        self,
        page: int,
        limit: int,
        agent_type: Optional[AgentType] = None,
        status: Optional[AgentStatus] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        search: Optional[str] = None,
    ) -> AgentListResponse:
        aggregates = await self._aggregate_agents()
        agents = [self._aggregate_to_agent(agg) for agg in aggregates.values()]

        if agent_type:
            agents = [agent for agent in agents if agent.type == agent_type]

        if status:
            agents = [agent for agent in agents if agent.status == status]

        if search:
            lowered = search.lower()
            agents = [
                agent
                for agent in agents
                if lowered in agent.name.lower() or lowered in agent.id.lower()
            ]

        agents = self._sort_agents(agents, sort_by, sort_order)

        total = len(agents)
        start = (page - 1) * limit
        end = start + limit
        paginated = agents[start:end]

        return AgentListResponse(
            agents=paginated,
            total=total,
            page=page,
            limit=limit,
        )

    async def get_agent(self, agent_id: str) -> AgentDetailResponse:
        aggregates = await self._aggregate_agents()
        aggregate = aggregates.get(agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")

        agent_model = self._aggregate_to_agent(aggregate)
        score_round_data = [
            ScoreRoundDataPoint(
                validator_round_id=_round_id_to_int(context.round.validator_round_id),
                score=score,
                rank=context.run.rank,
                top_score=aggregate.best_score,
                reward=None,
                timestamp=datetime.fromtimestamp(
                    context.round.started_at or context.run.started_at or _ts(None),
                    tz=timezone.utc,
                ),
            )
            for context, score in self._run_scores(aggregate)
        ]

        return AgentDetailResponse(agent=agent_model, scoreRoundData=score_round_data)

    async def get_performance(
        self,
        agent_id: str,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        granularity: Optional[str] = None,
    ) -> AgentPerformanceResponse:
        aggregates = await self._aggregate_agents()
        aggregate = aggregates.get(agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")
        metrics = self._build_performance_metrics(aggregate)
        return AgentPerformanceResponse(metrics=metrics)

    async def list_agent_runs(
        self,
        agent_id: str,
        page: int,
        limit: int,
    ) -> AgentRunsResponse:
        contexts = await self._fetch_agent_contexts(agent_id)
        total = len(contexts)
        start = (page - 1) * limit
        end = start + limit
        paginated_contexts = contexts[start:end]

        runs = []
        for context in paginated_contexts:
            agent_run = self.agent_runs_service._build_agent_run(context)  # type: ignore[attr-defined]
            runs.append(AgentRun(**agent_run.model_dump()))

        return AgentRunsResponse(runs=runs, total=total, page=page, limit=limit)

    async def get_agent_activity(
        self,
        agent_id: str,
        limit: int,
        offset: int,
        activity_type: Optional[ActivityType] = None,
        since: Optional[datetime] = None,
    ) -> AgentActivityResponse:
        aggregates = await self._aggregate_agents()
        aggregate = aggregates.get(agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")

        activities = self._activities_from_aggregate(aggregate)
        filtered = self._filter_activities(activities, activity_type, since)
        total = len(filtered)
        paginated = filtered[offset : offset + limit]
        return AgentActivityResponse(activities=paginated, total=total)

    async def get_all_activity(
        self,
        limit: int,
        offset: int,
        activity_type: Optional[ActivityType] = None,
        since: Optional[datetime] = None,
        agent_id: Optional[str] = None,
    ) -> AgentActivityResponse:
        aggregates = await self._aggregate_agents()
        selected = (
            {agent_id: aggregates[agent_id]}
            if agent_id and agent_id in aggregates
            else aggregates
        )

        activities: List[AgentActivity] = []
        for aggregate in selected.values():
            activities.extend(self._activities_from_aggregate(aggregate))
        activities.sort(key=lambda activity: activity.timestamp, reverse=True)

        filtered = self._filter_activities(activities, activity_type, since)
        total = len(filtered)
        paginated = filtered[offset : offset + limit]
        return AgentActivityResponse(activities=paginated, total=total)

    async def statistics(self) -> AgentStatisticsResponse:
        aggregates = await self._aggregate_agents()
        if not aggregates:
            empty_top = TopAgent(id="", name="", score=0.0)
            empty_active = MostActiveAgent(id="", name="", runs=0)
            return AgentStatisticsResponse(
                statistics=AgentStatistics(
                    totalAgents=0,
                    activeAgents=0,
                    inactiveAgents=0,
                    totalRuns=0,
                    successfulRuns=0,
                    averageSuccessRate=0.0,
                    averageScore=0.0,
                    topPerformingAgent=empty_top,
                    mostActiveAgent=empty_active,
                    performanceDistribution=PerformanceDistribution(),
                    lastUpdated=datetime.fromtimestamp(0, tz=timezone.utc),
                )
            )

        total_agents = len(aggregates)
        total_runs = sum(aggregate.total_runs for aggregate in aggregates.values())
        successful_runs = sum(aggregate.successful_runs for aggregate in aggregates.values())

        per_agent_success = [
            (aggregate.successful_runs / aggregate.total_runs * 100.0)
            if aggregate.total_runs
            else 0.0
            for aggregate in aggregates.values()
        ]
        per_agent_scores = [
            (aggregate.total_score / aggregate.total_runs) if aggregate.total_runs else 0.0
            for aggregate in aggregates.values()
        ]

        average_success_rate = sum(per_agent_success) / total_agents if total_agents else 0.0
        average_score = sum(per_agent_scores) / total_agents if total_agents else 0.0

        def _avg_score(aggregate: AgentAggregate) -> float:
            return (aggregate.total_score / aggregate.total_runs) if aggregate.total_runs else 0.0

        top_aggregate = max(aggregates.values(), key=_avg_score)
        top_agent = TopAgent(
            id=top_aggregate.agent_id,
            name=self._aggregate_name(top_aggregate),
            score=round(_avg_score(top_aggregate), 3),
        )

        most_active = max(aggregates.values(), key=lambda agg: agg.total_runs)
        most_active_agent = MostActiveAgent(
            id=most_active.agent_id,
            name=self._aggregate_name(most_active),
            runs=most_active.total_runs,
        )

        distribution = PerformanceDistribution()
        for aggregate in aggregates.values():
            score = _avg_score(aggregate)
            if score >= 0.9:
                distribution.excellent += 1
            elif score >= 0.7:
                distribution.good += 1
            elif score >= 0.5:
                distribution.average += 1
            else:
                distribution.poor += 1

        last_updated_ts = max(aggregate.last_seen for aggregate in aggregates.values())

        return AgentStatisticsResponse(
            statistics=AgentStatistics(
                totalAgents=total_agents,
                activeAgents=total_agents,
                inactiveAgents=0,
                totalRuns=total_runs,
                successfulRuns=successful_runs,
                averageSuccessRate=round(average_success_rate, 2),
                averageScore=round(average_score, 3),
                topPerformingAgent=top_agent,
                mostActiveAgent=most_active_agent,
                performanceDistribution=distribution,
                lastUpdated=datetime.fromtimestamp(last_updated_ts or 0.0, tz=timezone.utc),
            )
        )

    async def compare_agents(self, agent_ids: List[str]) -> AgentComparisonResponse:
        aggregates = await self._aggregate_agents()
        selected = [aggregates[agent_id] for agent_id in agent_ids if agent_id in aggregates]
        if not selected:
            raise ValueError("No matching agents found")

        comparisons: List[AgentComparison] = []
        for aggregate in selected:
            avg_score = (aggregate.total_score / aggregate.total_runs) if aggregate.total_runs else 0.0
            success_rate = (
                aggregate.successful_runs / aggregate.total_runs * 100.0
                if aggregate.total_runs
                else 0.0
            )
            avg_duration = sum(aggregate.durations) / len(aggregate.durations) if aggregate.durations else 0.0
            ranking = min(aggregate.ranks) if aggregate.ranks else 0

            comparisons.append(
                AgentComparison(
                    agentId=aggregate.agent_id,
                    name=self._aggregate_name(aggregate),
                    metrics=AgentComparisonMetrics(
                        averageScore=round(avg_score, 3),
                        successRate=round(success_rate, 2),
                        averageDuration=round(avg_duration, 2),
                        totalRuns=aggregate.total_runs,
                        ranking=ranking,
                    ),
                )
            )

        def _select(metric: str, reverse: bool = True, default: str = "") -> str:
            if not comparisons:
                return default
            key_func = {
                "averageScore": lambda comp: comp.metrics.averageScore,
                "successRate": lambda comp: comp.metrics.successRate,
                "averageDuration": lambda comp: comp.metrics.averageDuration,
                "totalRuns": lambda comp: comp.metrics.totalRuns,
            }[metric]
            sorted_items = sorted(comparisons, key=key_func, reverse=reverse)
            return sorted_items[0].agentId if sorted_items else default

        comparison_metrics = ComparisonMetrics(
            bestPerformer=_select("averageScore"),
            mostReliable=_select("successRate"),
            fastest=_select("averageDuration", reverse=False),
            mostActive=_select("totalRuns"),
        )

        time_range = {
            "start": _iso_ts(
                min(aggregate.first_seen for aggregate in selected) if selected else None
            ),
            "end": _iso_ts(
                max(aggregate.last_seen for aggregate in selected) if selected else None
            ),
        }

        comparisons.sort(key=lambda comp: comp.agentId)
        return AgentComparisonResponse(
            agents=comparisons,
            comparisonMetrics=comparison_metrics,
            timeRange=time_range,
        )

    def _build_performance_metrics(self, aggregate: AgentAggregate) -> AgentPerformanceMetrics:
        scores = [self._compute_run_score(context) for context in aggregate.runs]
        durations = [self._compute_run_duration(context) or 0.0 for context in aggregate.runs]

        total_runs = aggregate.total_runs
        successful_runs = aggregate.successful_runs
        failed_runs = max(total_runs - successful_runs, 0)

        average_score = sum(scores) / len(scores) if scores else 0.0
        best_score = max(scores) if scores else 0.0
        worst_score = min(scores) if scores else 0.0
        success_rate = (successful_runs / total_runs * 100.0) if total_runs else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        task_completion_rate = (
            (aggregate.completed_tasks / aggregate.total_tasks) * 100.0
            if aggregate.total_tasks
            else 0.0
        )

        excellent = len([score for score in scores if score >= 0.9])
        good = len([score for score in scores if 0.7 <= score < 0.9])
        average_bucket = len([score for score in scores if 0.5 <= score < 0.7])
        poor = len(scores) - excellent - good - average_bucket

        score_distribution = ScoreDistribution(
            excellent=excellent,
            good=good,
            average=average_bucket,
            poor=max(poor, 0),
        )

        sorted_runs = sorted(
            aggregate.runs,
            key=lambda ctx: ctx.run.started_at or ctx.round.started_at or _ts(None),
        )
        trend: List[PerformanceTrend] = []
        for context in sorted_runs:
            start_ts = context.run.started_at or context.round.started_at or _ts(None)
            score = self._compute_run_score(context)
            duration = self._compute_run_duration(context) or 0.0
            trend.append(
                PerformanceTrend(
                    period=datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                    score=round(score, 3),
                    successRate=100.0 if score >= 0.5 else 0.0,
                    duration=duration,
                )
            )

        time_range = {
            "start": _iso_ts(aggregate.first_seen if aggregate.first_seen != float("inf") else None),
            "end": _iso_ts(aggregate.last_seen if aggregate.last_seen else None),
        }

        return AgentPerformanceMetrics(
            agentId=aggregate.agent_id,
            timeRange=time_range,
            totalRuns=total_runs,
            successfulRuns=successful_runs,
            failedRuns=failed_runs,
            averageScore=round(average_score, 3),
            bestScore=round(best_score, 3),
            worstScore=round(worst_score, 3),
            successRate=round(success_rate, 2),
            averageDuration=round(average_duration, 2),
            totalTasks=aggregate.total_tasks,
            completedTasks=aggregate.completed_tasks,
            taskCompletionRate=round(task_completion_rate, 2),
            scoreDistribution=score_distribution,
            performanceTrend=trend,
        )

    async def _aggregate_agents(self) -> Dict[str, AgentAggregate]:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
        )
        rows = await self.session.scalars(stmt)
        aggregates: Dict[str, AgentAggregate] = {}

        for run_row in rows:
            context = self.rounds_service._build_agent_run_context(run_row)  # type: ignore[attr-defined]
            agent_id = _format_agent_id(context.run.miner_uid)
            aggregate = aggregates.get(agent_id)
            if aggregate is None:
                miner_info = self._find_miner_info(context)
                aggregate = AgentAggregate(
                    agent_id=agent_id,
                    uid=context.run.miner_uid,
                    miner=miner_info,
                    is_sota=context.run.is_sota,
                    version=context.run.version,
                )
                aggregates[agent_id] = aggregate

            aggregate.runs.append(context)
            aggregate.total_runs += 1
            run_score = self._compute_run_score(context)
            aggregate.total_score += run_score
            aggregate.best_score = max(aggregate.best_score, run_score)
            if run_score >= 0.5:
                aggregate.successful_runs += 1

            duration = self._compute_run_duration(context)
            if duration is not None:
                aggregate.durations.append(duration)

            aggregate.total_tasks += len(context.tasks)
            aggregate.completed_tasks += len(
                [er for er in context.evaluation_results if er.final_score >= 0.5]
            )

            if context.run.rank is not None:
                aggregate.ranks.append(context.run.rank)

            aggregate.rounds.add(context.round.validator_round_id)

            started = context.run.started_at or context.round.started_at or _ts(None)
            ended = context.run.ended_at or started
            aggregate.first_seen = min(aggregate.first_seen, started)
            aggregate.last_seen = max(aggregate.last_seen, ended)

        return aggregates

    async def _fetch_agent_contexts(self, agent_id: str) -> List[AgentRunContext]:
        uid = None
        if "-" in agent_id:
            try:
                uid = int(agent_id.split("-", 1)[1])
            except ValueError:
                uid = None

        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
        )
        if uid is not None:
            stmt = stmt.where(AgentEvaluationRunORM.miner_uid == uid)

        rows = await self.session.scalars(stmt)
        contexts = [
            self.rounds_service._build_agent_run_context(row)  # type: ignore[attr-defined]
            for row in rows
        ]
        contexts.sort(
            key=lambda ctx: ctx.run.started_at or ctx.round.started_at or _ts(None),
            reverse=True,
        )
        return contexts

    def _aggregate_name(self, aggregate: AgentAggregate) -> str:
        if aggregate.miner and aggregate.miner.agent_name:
            return aggregate.miner.agent_name
        return aggregate.agent_id

    def _activities_from_aggregate(self, aggregate: AgentAggregate) -> List[AgentActivity]:
        activities: List[AgentActivity] = []
        agent_name = self._aggregate_name(aggregate)
        for context in aggregate.runs:
            start_ts = context.run.started_at or context.round.started_at or _ts(None)
            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            score = self._compute_run_score(context)
            metadata = {
                "runId": context.run.agent_run_id,
                "roundId": _round_id_to_int(context.round.validator_round_id),
                "validatorId": f"validator-{context.run.validator_uid}",
                "score": round(score, 3),
            }
            activities.append(
                AgentActivity(
                    id=f"{context.run.agent_run_id}-started",
                    type=ActivityType.RUN_STARTED,
                    agentId=aggregate.agent_id,
                    agentName=agent_name,
                    message="Agent run started",
                    timestamp=start_dt,
                    metadata=metadata,
                )
            )

            if context.run.ended_at:
                end_dt = datetime.fromtimestamp(context.run.ended_at, tz=timezone.utc)
                duration = self._compute_run_duration(context) or 0.0
                metadata = {
                    **metadata,
                    "duration": duration,
                }
                end_type = ActivityType.RUN_COMPLETED if score >= 0.5 else ActivityType.RUN_FAILED
                end_message = (
                    "Agent run completed successfully"
                    if end_type == ActivityType.RUN_COMPLETED
                    else "Agent run completed with failures"
                )
                activities.append(
                    AgentActivity(
                        id=f"{context.run.agent_run_id}-completed",
                        type=end_type,
                        agentId=aggregate.agent_id,
                        agentName=agent_name,
                        message=end_message,
                        timestamp=end_dt,
                        metadata=metadata,
                    )
                )
        activities.sort(key=lambda activity: activity.timestamp, reverse=True)
        return activities

    @staticmethod
    def _filter_activities(
        activities: List[AgentActivity],
        activity_type: Optional[ActivityType],
        since: Optional[datetime],
    ) -> List[AgentActivity]:
        filtered = activities
        if activity_type:
            filtered = [activity for activity in filtered if activity.type == activity_type]
        if since:
            filtered = [
                activity for activity in filtered if activity.timestamp >= since
            ]
        return filtered

    def _aggregate_to_agent(self, aggregate: AgentAggregate) -> Agent:
        miner = aggregate.miner
        name = miner.agent_name if miner and miner.agent_name else aggregate.agent_id
        hotkey = miner.hotkey if miner else None
        image_url = miner.agent_image if miner and miner.agent_image else ""
        github = miner.github if miner else None
        description = miner.description if miner else ""

        average_score = aggregate.total_score / aggregate.total_runs if aggregate.total_runs else 0.0
        average_duration = (
            sum(aggregate.durations) / len(aggregate.durations) if aggregate.durations else 0.0
        )
        min_rank = min(aggregate.ranks) if aggregate.ranks else 0

        last_seen_dt = datetime.fromtimestamp(aggregate.last_seen or _ts(None), tz=timezone.utc)
        first_seen_dt = datetime.fromtimestamp(
            aggregate.first_seen if aggregate.first_seen != float("inf") else aggregate.last_seen or _ts(None),
            tz=timezone.utc,
        )

        return Agent(
            id=aggregate.agent_id,
            uid=aggregate.uid,
            name=name,
            hotkey=hotkey,
            type=AgentType.AUTOPPIA,
            imageUrl=image_url,
            githubUrl=github,
            taostatsUrl=f"https://taostats.io/miner/{aggregate.uid}" if aggregate.uid is not None else None,
            isSota=aggregate.is_sota,
            description=description,
            version=aggregate.version,
            status=AgentStatus.ACTIVE,
            totalRuns=aggregate.total_runs,
            successfulRuns=aggregate.successful_runs,
            currentScore=average_score,
            currentTopScore=aggregate.best_score,
            currentRank=min_rank,
            bestRankEver=min_rank,
            roundsParticipated=len(aggregate.rounds),
            alphaWonInPrizes=0.0,
            averageDuration=average_duration,
            totalTasks=aggregate.total_tasks,
            completedTasks=aggregate.completed_tasks,
            lastSeen=last_seen_dt,
            createdAt=first_seen_dt,
            updatedAt=last_seen_dt,
        )

    def _sort_agents(self, agents: List[Agent], sort_by: str, sort_order: str) -> List[Agent]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(agents, key=lambda agent: getattr(agent, sort_by), reverse=reverse)
        except AttributeError:
            return agents

    def _compute_run_score(self, context: AgentRunContext) -> float:
        if not context.evaluation_results:
            return 0.0
        return (
            sum(result.final_score for result in context.evaluation_results)
            / len(context.evaluation_results)
        )

    def _compute_run_duration(self, context: AgentRunContext) -> Optional[float]:
        if context.run.started_at and context.run.ended_at:
            return context.run.ended_at - context.run.started_at
        return None

    def _run_scores(self, aggregate: AgentAggregate) -> List[tuple[AgentRunContext, float]]:
        return [(context, self._compute_run_score(context)) for context in aggregate.runs]

    def _find_miner_info(self, context: AgentRunContext) -> Optional[MinerInfo]:
        if context.run.miner_info:
            return context.run.miner_info

        if context.round.miners:
            for miner in context.round.miners:
                if miner.uid == context.run.miner_uid:
                    return miner

        if context.round.sota_agents:
            for miner in context.round.sota_agents:
                if miner.uid == context.run.miner_uid:
                    return miner

        return None

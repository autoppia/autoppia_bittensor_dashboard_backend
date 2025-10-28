from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings

from app.db.models import AgentEvaluationRunORM, RoundORM
from app.models.core import MinerInfo, ValidatorRound
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
    AgentRoundMetrics,
)
from app.services.ui.rounds_service import AgentRunContext, RoundsService
from app.services.ui.agent_runs_service import AgentRunsService
from app.utils.images import resolve_agent_image
from app.services.subnet_utils import get_price_cached as get_subnet_price
from app.utils.urls import build_taostats_miner_url

logger = logging.getLogger(__name__)

ALPHA_EMISSION_PER_EPOCH = 148.0
_EPSILON = 1e-6


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _round_id_to_int(round_id: str) -> int:
    if not round_id:
        return 0
    matches = re.findall(r"\d+", round_id)
    if not matches:
        return 0
    try:
        return int(matches[-1])
    except ValueError:
        return 0


def _context_round_number(context: AgentRunContext) -> int:
    return int(
        context.round.round_number or _round_id_to_int(context.round.validator_round_id)
    )


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
    round_scores: Dict[int, List[float]] = field(default_factory=dict)
    round_ranks: Dict[int, List[int]] = field(default_factory=dict)
    global_round_ranks: Dict[int, int] = field(default_factory=dict)
    latest_rank: Optional[int] = None
    latest_rank_time: float = field(default_factory=lambda: float("-inf"))
    latest_round_number: Optional[int] = None
    latest_round_score: Optional[float] = None
    latest_round_top_score: Optional[float] = None
    latest_round_rank: Optional[int] = None
    latest_round_global_rank: Optional[int] = None
    rounds: Set[int] = field(default_factory=set)
    first_seen: float = field(default_factory=lambda: float("inf"))
    last_seen: float = field(default_factory=lambda: 0.0)
    round_rewards: Dict[int, float] = field(default_factory=dict)
    winning_rounds: Set[int] = field(default_factory=set)
    alpha_reward: float = 0.0
    best_round_average: float = 0.0
    # Track which round achieved best_round_average
    best_round_number: Optional[int] = None


@dataclass
class RoundAgentSnapshot:
    """Aggregated snapshot of an agent's performance within a specific round."""

    aggregate: AgentAggregate
    round_number: int
    average_score: float
    best_score: float
    total_runs: int
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    validator_details: List[Dict[str, Any]] = field(default_factory=list)
    durations: List[float] = field(default_factory=list)
    rank: int = 0

    @property
    def average_duration(self) -> float:
        if not self.durations:
            return 0.0
        return sum(self.durations) / len(self.durations)

    @property
    def success_rate(self) -> float:
        if self.total_tasks <= 0:
            return 0.0
        return self.completed_tasks / self.total_tasks

    @property
    def validator_uids(self) -> List[int]:
        return [
            detail["uid"]
            for detail in self.validator_details
            if detail.get("uid") is not None
        ]


_CACHE_TTL_ENV = "AGENTS_CACHE_TTL_SECONDS"
_DEFAULT_CACHE_TTL = 30
try:
    _CACHE_TTL_SECONDS = max(int(os.getenv(_CACHE_TTL_ENV, str(_DEFAULT_CACHE_TTL))), 0)
except ValueError:
    _CACHE_TTL_SECONDS = _DEFAULT_CACHE_TTL

if settings.API_CACHE_DISABLED:
    _CACHE_TTL_SECONDS = 0
_AGGREGATE_CACHE: Optional[Dict[str, AgentAggregate]] = None
_AGGREGATE_CACHE_TIMESTAMP: float = 0.0
_AGGREGATE_CACHE_BENCHMARKS: Dict[int, Dict[str, Dict[str, Any]]] = {}
_AGGREGATE_CACHE_SIGNATURE: Optional[Tuple[int, Optional[datetime]]] = None
_AGGREGATE_CACHE_LOCK = asyncio.Lock()


def _clone_round_benchmark_cache(
    cache: Dict[int, Dict[str, Dict[str, Any]]]
) -> Dict[int, Dict[str, Dict[str, Any]]]:
    return {
        round_id: {key: dict(entry) for key, entry in entries.items()}
        for round_id, entries in cache.items()
    }


def _cache_valid(now: float) -> bool:
    if _AGGREGATE_CACHE is None:
        return False
    if _CACHE_TTL_SECONDS <= 0:
        return False
    return (now - _AGGREGATE_CACHE_TIMESTAMP) <= _CACHE_TTL_SECONDS


class AgentsService:
    """SQL-backed service for agent summaries."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)
        self.agent_runs_service = AgentRunsService(session)
        self._round_benchmark_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}

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

    async def get_agent(
        self,
        agent_id: str,
        round_number: Optional[int] = None,
    ) -> AgentDetailResponse:
        aggregates = await self._aggregate_agents()
        aggregate = self._resolve_aggregate(aggregates, agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")

        agent_model = self._aggregate_to_agent(aggregate)
        score_round_data = self._build_round_score_series(aggregate, aggregates)

        available_rounds = sorted(
            {round_id for round_id in aggregate.rounds if isinstance(round_id, int) and round_id > 0},
            reverse=True,
        )

        requested_round = round_number if round_number and round_number > 0 else None
        snapshot_round = requested_round or (available_rounds[0] if available_rounds else None)

        round_metrics = None
        if snapshot_round is not None:
            snapshots = await self.build_round_snapshots(snapshot_round, aggregates)
            snapshot = next(
                (item for item in snapshots if item.aggregate.agent_id == aggregate.agent_id),
                None,
            )
            top_score = snapshots[0].average_score if snapshots else 0.0

            if snapshot:
                round_metrics = AgentRoundMetrics(
                    roundId=snapshot_round,
                    score=snapshot.average_score,
                    topScore=top_score,
                    rank=snapshot.rank if snapshot.rank > 0 else None,
                    totalRuns=snapshot.total_runs,
                    totalValidators=len(snapshot.validator_details),
                    validatorUids=snapshot.validator_uids,
                    validators=snapshot.validator_details,
                    totalTasks=snapshot.total_tasks,
                    completedTasks=snapshot.completed_tasks,
                    failedTasks=snapshot.failed_tasks,
                    successRate=snapshot.success_rate,
                    averageResponseTime=snapshot.average_duration,
                )
            elif requested_round is not None:
                round_metrics = AgentRoundMetrics(
                    roundId=requested_round,
                    score=0.0,
                    topScore=top_score,
                    rank=None,
                    totalRuns=0,
                    totalValidators=0,
                    validatorUids=[],
                    validators=[],
                    totalTasks=0,
                    completedTasks=0,
                    failedTasks=0,
                    successRate=0.0,
                    averageResponseTime=0.0,
                )

        return AgentDetailResponse(
            agent=agent_model,
            scoreRoundData=score_round_data,
            availableRounds=available_rounds,
            roundMetrics=round_metrics,
        )

    async def get_performance(
        self,
        agent_id: str,
        time_range: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        granularity: Optional[str] = None,
    ) -> AgentPerformanceResponse:
        aggregates = await self._aggregate_agents()
        aggregate = self._resolve_aggregate(aggregates, agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")
        start_dt, end_dt = self._resolve_time_window(time_range, start_date, end_date)
        contexts = self._filter_contexts_by_time_window(aggregate.runs, start_dt, end_dt)
        metrics = self._build_performance_metrics(
            aggregate,
            contexts,
            start_dt=start_dt,
            end_dt=end_dt,
            granularity=granularity,
        )
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

        round_numbers = sorted(
            {
                int(context.round.round_number or _round_id_to_int(context.round.validator_round_id))
                for context in contexts
                if int(context.round.round_number or _round_id_to_int(context.round.validator_round_id))
            },
            reverse=True,
        )

        return AgentRunsResponse(
            runs=runs,
            total=total,
            page=page,
            limit=limit,
            availableRounds=round_numbers,
            selectedRound=round_numbers[0] if round_numbers else None,
        )

    async def get_agent_activity(
        self,
        agent_id: str,
        limit: int,
        offset: int,
        activity_type: Optional[ActivityType] = None,
        since: Optional[datetime] = None,
    ) -> AgentActivityResponse:
        aggregates = await self._aggregate_agents()
        aggregate = self._resolve_aggregate(aggregates, agent_id)
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
        if agent_id:
            aggregate = self._resolve_aggregate(aggregates, agent_id)
            selected = {aggregate.agent_id: aggregate} if aggregate else {}
        else:
            selected = aggregates

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
                    averageCurrentScore=0.0,
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
                averageCurrentScore=round(average_score, 3),
                topPerformingAgent=top_agent,
                mostActiveAgent=most_active_agent,
                performanceDistribution=distribution,
                lastUpdated=datetime.fromtimestamp(last_updated_ts or 0.0, tz=timezone.utc),
            )
        )

    async def compare_agents(self, agent_ids: List[str]) -> AgentComparisonResponse:
        aggregates = await self._aggregate_agents()
        resolved = [
            self._resolve_aggregate(aggregates, agent_id)
            for agent_id in agent_ids
        ]
        selected = [aggregate for aggregate in resolved if aggregate is not None]
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
            latest_top_score = (
                aggregate.latest_round_top_score
                if aggregate.latest_round_top_score is not None
                else aggregate.best_score
            )

            comparisons.append(
                AgentComparison(
                    agentId=aggregate.agent_id,
                    name=self._aggregate_name(aggregate),
                    metrics=AgentComparisonMetrics(
                        currentScore=round(avg_score, 3),
                        currentTopScore=round(latest_top_score or 0.0, 3),
                        successRate=round(success_rate, 2),
                        averageResponseTime=round(avg_duration, 2),
                        totalRuns=aggregate.total_runs,
                        currentRank=ranking,
                    ),
                )
            )

        def _select(metric: str, reverse: bool = True, default: str = "") -> str:
            if not comparisons:
                return default
            key_func = {
                "currentScore": lambda comp: comp.metrics.currentScore,
                "successRate": lambda comp: comp.metrics.successRate,
                "averageResponseTime": lambda comp: comp.metrics.averageResponseTime,
                "totalRuns": lambda comp: comp.metrics.totalRuns,
            }[metric]
            sorted_items = sorted(comparisons, key=key_func, reverse=reverse)
            return sorted_items[0].agentId if sorted_items else default

        comparison_metrics = ComparisonMetrics(
            bestPerformer=_select("currentScore"),
            mostReliable=_select("successRate"),
            fastest=_select("averageResponseTime", reverse=False),
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

    def _build_performance_metrics(
        self,
        aggregate: AgentAggregate,
        contexts: List[AgentRunContext],
        *,
        start_dt: Optional[datetime],
        end_dt: Optional[datetime],
        granularity: Optional[str],
    ) -> AgentPerformanceMetrics:
        if not contexts:
            requested_start = (
                start_dt.timestamp()
                if start_dt
                else None
                if aggregate.first_seen == float("inf")
                else aggregate.first_seen
            )
            requested_end = (
                end_dt.timestamp()
                if end_dt
                else None
                if not aggregate.last_seen
                else aggregate.last_seen
            )
            time_range = {
                "start": _iso_ts(requested_start),
                "end": _iso_ts(requested_end),
            }
            return AgentPerformanceMetrics(
                agentId=aggregate.agent_id,
                timeRange=time_range,
                totalRuns=0,
                successfulRuns=0,
                failedRuns=0,
                successRate=0.0,
                currentScore=0.0,
                currentTopScore=0.0,
                worstScore=0.0,
                averageResponseTime=0.0,
                totalTasks=0,
                completedTasks=0,
                taskCompletionRate=0.0,
                scoreDistribution=ScoreDistribution(),
                performanceTrend=[],
            )

        scores: List[float] = []
        durations: List[float] = []
        total_tasks = 0
        completed_tasks = 0
        successes = 0
        trend_map: Dict[int, Dict[str, Any]] = {}

        for context in contexts:
            score = self._compute_run_score(context)
            scores.append(score)
            duration = self._compute_run_duration(context)
            if duration is not None:
                durations.append(duration)
            if duration is not None:
                durations.append(duration)

            task_total = context.run.total_tasks or len(context.tasks)
            total_tasks += task_total

            completed_from_run = context.run.completed_tasks
            if completed_from_run is not None:
                completed_tasks += completed_from_run
            elif context.evaluation_results:
                completed_tasks += len(
                    [er for er in context.evaluation_results if er.final_score >= 0.5]
                )

            if score >= 0.5:
                successes += 1

            round_number = self._round_number(context)
            if round_number > 0:
                data = trend_map.setdefault(
                    round_number,
                    {
                        "scores": [],
                        "durations": [],
                        "successes": 0,
                        "count": 0,
                    },
                )
                data["scores"].append(score)
                data["durations"].append(duration or 0.0)
                data["successes"] += 1 if score >= 0.5 else 0
                data["count"] += 1

        total_runs = len(contexts)
        failed_runs = max(total_runs - successes, 0)

        average_score = sum(scores) / len(scores) if scores else 0.0
        best_score = max(scores) if scores else 0.0
        worst_score = min(scores) if scores else 0.0
        success_rate = (successes / total_runs * 100.0) if total_runs else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        task_completion_rate = (
            (completed_tasks / total_tasks) * 100.0 if total_tasks else 0.0
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

        trend: List[PerformanceTrend] = []
        for round_number in sorted(trend_map.keys()):
            bucket = trend_map[round_number]
            bucket_scores = bucket["scores"]
            bucket_durations = bucket["durations"]
            bucket_successes = bucket["successes"]
            bucket_count = bucket["count"]

            trend.append(
                PerformanceTrend(
                    score=round(
                        sum(bucket_scores) / len(bucket_scores) if bucket_scores else 0.0,
                        3,
                    ),
                    round=round_number,
                    responseTime=round(
                        sum(bucket_durations) / len(bucket_durations)
                        if bucket_durations
                        else 0.0,
                        2,
                    ),
                    successRate=round(
                        (bucket_successes / bucket_count * 100.0) if bucket_count else 0.0,
                        2,
                    ),
                )
            )

        sorted_runs = sorted(
            contexts,
            key=lambda ctx: ctx.run.started_at or ctx.round.started_at or _ts(None),
        )

        first_ts = (
            start_dt.timestamp()
            if start_dt
            else sorted_runs[0].run.started_at
            or sorted_runs[0].round.started_at
            or aggregate.first_seen
        )
        last_ts = (
            end_dt.timestamp()
            if end_dt
            else sorted_runs[-1].run.ended_at
            or sorted_runs[-1].round.ended_at
            or aggregate.last_seen
        )

        time_range = {
            "start": _iso_ts(first_ts),
            "end": _iso_ts(last_ts),
        }

        return AgentPerformanceMetrics(
            agentId=aggregate.agent_id,
            timeRange=time_range,
            totalRuns=total_runs,
            successfulRuns=successes,
            failedRuns=failed_runs,
            successRate=round(success_rate, 2),
            currentScore=round(average_score, 3),
            currentTopScore=round(best_score, 3),
            worstScore=round(worst_score, 3),
            averageResponseTime=round(average_duration, 2),
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            taskCompletionRate=round(task_completion_rate, 2),
            scoreDistribution=score_distribution,
            performanceTrend=trend,
        )

    async def _aggregate_agents(self) -> Dict[str, AgentAggregate]:
        global _AGGREGATE_CACHE, _AGGREGATE_CACHE_TIMESTAMP, _AGGREGATE_CACHE_BENCHMARKS, _AGGREGATE_CACHE_SIGNATURE

        now = time.monotonic()
        cached = await self._try_get_cached_aggregates(now)
        if cached is not None:
            return cached

        async with _AGGREGATE_CACHE_LOCK:
            now = time.monotonic()
            cached = await self._try_get_cached_aggregates(now)
            if cached is not None:
                return cached

            aggregates, round_benchmark_scores, signature = await self._build_agent_aggregates()
            round_cache = _clone_round_benchmark_cache(round_benchmark_scores)
            self._round_benchmark_cache = round_cache

            if _CACHE_TTL_SECONDS > 0:
                _AGGREGATE_CACHE = aggregates
                _AGGREGATE_CACHE_TIMESTAMP = now
                _AGGREGATE_CACHE_BENCHMARKS = round_cache
                _AGGREGATE_CACHE_SIGNATURE = signature

            return aggregates

    async def build_round_snapshots(
        self,
        round_number: int,
        aggregates: Optional[Dict[str, AgentAggregate]] = None,
    ) -> List[RoundAgentSnapshot]:
        if round_number <= 0:
            return []

        if aggregates is None:
            aggregates = await self._aggregate_agents()

        snapshots: List[RoundAgentSnapshot] = []
        for aggregate in aggregates.values():
            round_contexts = [
                context
                for context in aggregate.runs
                if _context_round_number(context) == round_number
            ]
            if not round_contexts:
                continue

            scores: List[float] = []
            durations: List[float] = []
            total_tasks = 0
            completed_tasks = 0
            failed_tasks = 0
            validator_details: Dict[int, Dict[str, Any]] = {}

            for context in round_contexts:
                scores.append(self._compute_run_score(context))
                duration = self._compute_run_duration(context)
                if duration is not None:
                    durations.append(duration)

                total_tasks += context.run.total_tasks or len(context.tasks)
                completed_tasks += context.run.completed_tasks or 0
                failed_tasks += context.run.failed_tasks or 0

                validator_uid = context.run.validator_uid
                validator_hotkey = context.run.validator_hotkey

                detail_key = validator_uid if validator_uid is not None else -1
                entry = validator_details.get(detail_key)
                if entry is None:
                    entry = {
                        "uid": validator_uid,
                        "hotkey": validator_hotkey,
                        "name": None,
                    }
                    validator_details[detail_key] = entry

                if validator_hotkey and not entry.get("hotkey"):
                    entry["hotkey"] = validator_hotkey

                round_metadata = getattr(context.round, "metadata", {}) or {}
                validator_meta = round_metadata.get("validator") or {}
                validator_name = (
                    validator_meta.get("name")
                    or validator_meta.get("validator_name")
                    or validator_meta.get("display_name")
                )
                if validator_name and not entry.get("name"):
                    entry["name"] = validator_name

            if not scores:
                continue

            snapshot = RoundAgentSnapshot(
                aggregate=aggregate,
                round_number=round_number,
                average_score=sum(scores) / len(scores),
                best_score=max(scores),
                total_runs=len(round_contexts),
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                failed_tasks=failed_tasks,
                validator_details=list(validator_details.values()),
                durations=durations,
            )
            snapshots.append(snapshot)

        snapshots.sort(key=lambda snap: snap.average_score, reverse=True)
        current_rank = 0
        last_score: Optional[float] = None
        for index, snapshot in enumerate(snapshots, start=1):
            if last_score is None or abs(snapshot.average_score - last_score) > 1e-6:
                current_rank = index
                last_score = snapshot.average_score
            snapshot.rank = current_rank

        return snapshots

    async def _try_get_cached_aggregates(self, now: float) -> Optional[Dict[str, AgentAggregate]]:
        if not _cache_valid(now):
            return None

        cached = _AGGREGATE_CACHE
        signature = _AGGREGATE_CACHE_SIGNATURE
        if cached is None or signature is None:
            return None

        current_signature = await self._fetch_current_signature()
        if current_signature != signature:
            return None

        self._round_benchmark_cache = _clone_round_benchmark_cache(_AGGREGATE_CACHE_BENCHMARKS)
        return cached

    async def _build_agent_aggregates(
        self,
    ) -> Tuple[
        Dict[str, AgentAggregate],
        Dict[int, Dict[str, Dict[str, Any]]],
        Tuple[int, Optional[datetime]],
    ]:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.miner_snapshots
                ),
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.validator_snapshots
                ),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
        )
        result = await self.session.scalars(stmt)
        run_rows = list(result)
        last_updated = max(
            (row.updated_at for row in run_rows if getattr(row, "updated_at", None) is not None),
            default=None,
        )
        if last_updated is not None and last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        signature: Tuple[int, Optional[datetime]] = (len(run_rows), last_updated)
        tasks_by_round = await self.rounds_service._load_tasks_for_rounds(  # type: ignore[attr-defined]
            {row.validator_round_id for row in run_rows}
        )
        contexts: List[AgentRunContext] = []
        for run_row in run_rows:
            round_tasks = tasks_by_round.get(run_row.validator_round_id, {})
            context = self.rounds_service._build_agent_run_context(  # type: ignore[attr-defined]
                run_row,
                tasks_for_round=round_tasks,
            )
            contexts.append(context)

        contexts_by_round: Dict[str, List[AgentRunContext]] = defaultdict(list)
        for context in contexts:
            contexts_by_round[context.round.validator_round_id].append(context)

        rankings_by_run_id: Dict[str, int] = {}
        for context_list in contexts_by_round.values():
            non_sota_contexts = [
                ctx for ctx in context_list if not ctx.run.is_sota
            ]
            # Deterministic tie-break: higher score first, then lexicographic agent_id
            non_sota_contexts.sort(
                key=lambda ctx: (
                    -self._compute_run_score(ctx),
                    _format_agent_id(ctx.run.miner_uid),
                )
            )
            current_rank = 0
            last_score: Optional[float] = None
            for position, ctx in enumerate(non_sota_contexts, start=1):
                score = self._compute_run_score(ctx)
                if last_score is None or abs(score - last_score) > 1e-6:
                    current_rank = position
                    last_score = score
                rankings_by_run_id[ctx.run.agent_run_id] = current_rank

        aggregates: Dict[str, AgentAggregate] = {}
        round_benchmark_scores: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        round_leaderboards: Dict[int, List[tuple[str, float]]] = defaultdict(list)
        # Track epoch length per unique numeric round to compute reward amounts
        round_epoch_lengths: Dict[int, float] = {}

        for context in contexts:
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

            round_identifier = int(
                context.round.round_number or _round_id_to_int(context.round.validator_round_id)
            )
            aggregate.rounds.add(round_identifier)
            if round_identifier:
                aggregate.round_scores.setdefault(round_identifier, []).append(run_score)
                epoch_length = self._round_epoch_length(context.round)
                if epoch_length > 0:
                    current = round_epoch_lengths.get(round_identifier)
                    if current is None or epoch_length > current:
                        round_epoch_lengths[round_identifier] = epoch_length

            duration = self._compute_run_duration(context)
            if duration is not None:
                aggregate.durations.append(duration)

            task_total = context.run.total_tasks or len(context.tasks)
            aggregate.total_tasks += task_total

            completed_from_run = context.run.completed_tasks or None
            if completed_from_run is not None and completed_from_run > 0:
                aggregate.completed_tasks += completed_from_run
            elif context.evaluation_results:
                aggregate.completed_tasks += len(
                    [er for er in context.evaluation_results if er.final_score >= 0.5]
                )

            rank_value: Optional[int] = None
            if context.run.agent_run_id in rankings_by_run_id:
                rank_value = rankings_by_run_id[context.run.agent_run_id]
            elif context.run.rank is not None:
                rank_value = context.run.rank
            elif context.round.winners:
                for winner in context.round.winners:
                    winner_uid = winner.get("miner_uid")
                    if winner_uid is not None and winner_uid == context.run.miner_uid:
                        rank_candidate = winner.get("rank") or winner.get("position") or winner.get("placement")
                        if rank_candidate is not None:
                            try:
                                rank_value = int(rank_candidate)
                            except (TypeError, ValueError):
                                rank_value = None
                        break

            if rank_value is not None and rank_value > 0:
                aggregate.ranks.append(rank_value)
                if context.run.rank is None or context.run.rank != rank_value:
                    context.run.rank = rank_value
                if round_identifier:
                    aggregate.round_ranks.setdefault(round_identifier, []).append(rank_value)

            started = context.run.started_at or context.round.started_at or _ts(None)
            ended = context.run.ended_at or started
            aggregate.first_seen = min(aggregate.first_seen, started)
            aggregate.last_seen = max(aggregate.last_seen, ended)
            if rank_value is not None and started >= aggregate.latest_rank_time:
                aggregate.latest_rank = rank_value
                aggregate.latest_rank_time = started

            if context.run.is_sota:
                bench_key = self._benchmark_key(context)
                miner_details = getattr(context.run, "miner_info", None)
                bench_name = (
                    miner_details.agent_name
                    if miner_details and miner_details.agent_name
                    else context.run.agent_run_id
                )
                bench_provider = (
                    miner_details.provider if miner_details and miner_details.provider else None
                )
                entry = {
                    "name": bench_name,
                    "provider": bench_provider,
                    "score": run_score,
                }
                existing_entry = round_benchmark_scores[round_identifier].get(bench_key)
                if existing_entry is None or run_score > existing_entry.get("score", 0.0):
                    round_benchmark_scores[round_identifier][bench_key] = entry

        # Compute global winners per round using aggregated average scores across all validators.
        # This avoids relying on per-validator winners payloads and ensures one winner per numeric round.
        round_winners: Dict[int, tuple[str, float]] = {}
        for aggregate in aggregates.values():
            best_avg = aggregate.best_round_average
            best_round_num = aggregate.best_round_number
            for round_number, scores in aggregate.round_scores.items():
                if not scores:
                    continue
                avg_score = sum(scores) / len(scores)
                if avg_score > best_avg:
                    best_avg = avg_score
                    best_round_num = round_number
                if aggregate.is_sota:
                    continue
                existing_winner = round_winners.get(round_number)
                if (
                    existing_winner is None
                    or avg_score > existing_winner[1] + _EPSILON
                    or (abs(avg_score - existing_winner[1]) <= _EPSILON and aggregate.agent_id < existing_winner[0])
                ):
                    round_winners[round_number] = (aggregate.agent_id, avg_score)
            aggregate.best_round_average = best_avg
            aggregate.best_round_number = best_round_num

        # Award alpha to the global winner for each round, once per numeric round
        for round_number, (winner_id, _) in round_winners.items():
            epochs = round_epoch_lengths.get(round_number)
            if epochs is None or epochs <= 0:
                epochs = self._fallback_round_epochs()
            reward = ALPHA_EMISSION_PER_EPOCH * epochs
            winner_aggregate = aggregates.get(winner_id)
            if winner_aggregate is not None:
                winner_aggregate.round_rewards[round_number] = reward
                winner_aggregate.alpha_reward += reward
                winner_aggregate.winning_rounds.add(round_number)

        # Compute a full global leaderboard for every round using per-agent averages
        # across all validators, so bestRankEver and roundsWon align consistently.
        # Also recompute latest_round_* summaries using those global tops.
        # First, derive latest round + latest average score per agent
        for aggregate in aggregates.values():
            if aggregate.round_scores:
                latest_round = max(aggregate.round_scores.keys())
                aggregate.latest_round_number = latest_round
                latest_scores = aggregate.round_scores.get(latest_round, [])
                aggregate.latest_round_score = (
                    sum(latest_scores) / len(latest_scores) if latest_scores else None
                )
            # Keep runs ordering for UI
            aggregate.runs.sort(
                key=lambda ctx: (
                    int(ctx.round.round_number or _round_id_to_int(ctx.round.validator_round_id)),
                    ctx.run.started_at or ctx.round.started_at or 0,
                ),
                reverse=True,
            )

        from collections import defaultdict as _dd
        round_leaderboards: Dict[int, List[tuple[str, float]]] = _dd(list)
        for aggregate in aggregates.values():
            if aggregate.is_sota:
                continue
            for round_number, scores in aggregate.round_scores.items():
                if not scores:
                    continue
                avg = sum(scores) / len(scores)
                round_leaderboards[round_number].append((aggregate.agent_id, avg))

        round_best_scores: Dict[int, float] = {}
        for round_number, entries in round_leaderboards.items():
            sorted_entries = sorted(entries, key=lambda item: (-item[1], item[0]))
            if sorted_entries:
                round_best_scores[round_number] = sorted_entries[0][1]
            for rank, (agent_id, _) in enumerate(sorted_entries, start=1):
                aggregates[agent_id].global_round_ranks[round_number] = rank

        for aggregate in aggregates.values():
            if aggregate.latest_round_number is not None:
                aggregate.latest_round_top_score = round_best_scores.get(
                    aggregate.latest_round_number,
                    aggregate.latest_round_score if aggregate.latest_round_score is not None else aggregate.best_score,
                )
            if (
                aggregate.latest_round_top_score is None
                and aggregate.latest_round_score is not None
            ):
                aggregate.latest_round_top_score = aggregate.latest_round_score

        round_cache = {
            round_id: {key: dict(entry) for key, entry in entries.items()}
            for round_id, entries in round_benchmark_scores.items()
        }

        return aggregates, round_cache, signature

    async def _fetch_current_signature(self) -> Tuple[int, Optional[datetime]]:
        stmt = select(
            func.count(AgentEvaluationRunORM.id),
            func.max(AgentEvaluationRunORM.updated_at),
        )
        result = await self.session.execute(stmt)
        total_runs, last_updated = result.one()
        total = int(total_runs or 0)
        if isinstance(last_updated, str):
            # Some drivers may return ISO strings for datetime columns
            last_updated = datetime.fromisoformat(last_updated.replace(" ", "T"))
        if isinstance(last_updated, datetime) and last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        return total, last_updated

    async def _fetch_agent_contexts(self, agent_id: str) -> List[AgentRunContext]:
        uid = self._extract_uid(agent_id)

        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.miner_snapshots
                ),
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.validator_snapshots
                ),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
        )
        if uid is not None:
            stmt = stmt.where(AgentEvaluationRunORM.miner_uid == uid)

        result = await self.session.scalars(stmt)
        run_rows = list(result)
        tasks_by_round = await self.rounds_service._load_tasks_for_rounds(  # type: ignore[attr-defined]
            {row.validator_round_id for row in run_rows}
        )
        contexts = [
            self.rounds_service._build_agent_run_context(  # type: ignore[attr-defined]
                row,
                tasks_for_round=tasks_by_round.get(row.validator_round_id, {}),
            )
            for row in run_rows
        ]
        self.rounds_service._assign_ranks(contexts)  # type: ignore[attr-defined]
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
        image_url = resolve_agent_image(miner, miner.agent_image if miner else None)
        github = miner.github if miner else None
        description = miner.description if miner else ""

        average_score = aggregate.total_score / aggregate.total_runs if aggregate.total_runs else 0.0
        latest_round_score = (
            aggregate.latest_round_score
            if aggregate.latest_round_score is not None
            else average_score
        )
        latest_round_top_score = (
            aggregate.latest_round_top_score
            if aggregate.latest_round_top_score is not None
            else aggregate.best_score
        )
        average_duration = (
            sum(aggregate.durations) / len(aggregate.durations) if aggregate.durations else 0.0
        )
        best_rank = (
            min(aggregate.global_round_ranks.values())
            if aggregate.global_round_ranks
            else None
        )
        best_rank_round = 0
        if aggregate.global_round_ranks and best_rank is not None:
            try:
                candidates = [
                    r for r, v in aggregate.global_round_ranks.items() if v == best_rank
                ]
                if candidates:
                    best_rank_round = int(sorted(candidates)[0])
            except Exception:
                best_rank_round = 0
        if aggregate.latest_round_global_rank is not None:
            current_rank_value = aggregate.latest_round_global_rank
        elif aggregate.latest_rank is not None:
            current_rank_value = aggregate.latest_rank
        elif aggregate.global_round_ranks:
            latest_round = max(aggregate.global_round_ranks.keys())
            current_rank_value = aggregate.global_round_ranks.get(latest_round)
        else:
            current_rank_value = aggregate.ranks[-1] if aggregate.ranks else None

        last_seen_dt = datetime.fromtimestamp(aggregate.last_seen or _ts(None), tz=timezone.utc)
        first_seen_dt = datetime.fromtimestamp(
            aggregate.first_seen if aggregate.first_seen != float("inf") else aggregate.last_seen or _ts(None),
            tz=timezone.utc,
        )

        taostats_url = build_taostats_miner_url(hotkey)
        # Resolve live subnet price with env fallback; avoid recompute per call via internal cache
        try:
            subnet_rate = float(get_subnet_price(settings.VALIDATOR_NETUID))
            if subnet_rate <= 0:
                subnet_rate = float(getattr(settings, "SUBNET_PRICE_FALLBACK", 1.0) or 1.0)
        except Exception:
            subnet_rate = float(getattr(settings, "SUBNET_PRICE_FALLBACK", 1.0) or 1.0)

        return Agent(
            id=aggregate.agent_id,
            uid=aggregate.uid,
            name=name,
            hotkey=hotkey,
            type=AgentType.AUTOPPIA,
            imageUrl=image_url,
            githubUrl=github,
            taostatsUrl=taostats_url,
            isSota=aggregate.is_sota,
            description=description,
            version=aggregate.version,
            status=AgentStatus.ACTIVE,
            totalRuns=aggregate.total_runs,
            successfulRuns=aggregate.successful_runs,
            currentScore=latest_round_score,
            currentTopScore=latest_round_top_score,
            currentRank=current_rank_value or 0,
            bestRankEver=best_rank or 0,
            bestRankRoundId=best_rank_round,
            roundsParticipated=len({round_id for round_id in aggregate.rounds if round_id}),
            roundsWon=len(aggregate.winning_rounds),
            alphaWonInPrizes=aggregate.alpha_reward,
            taoWonInPrizes=float(aggregate.alpha_reward) * float(subnet_rate),
            bestRoundScore=aggregate.best_round_average,
            bestRoundId=int(aggregate.best_round_number or 0),
            averageResponseTime=average_duration,
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
        candidate = getattr(context.run, "average_score", None)
        if candidate is not None:
            try:
                return float(candidate)
            except (TypeError, ValueError):
                pass

        if context.evaluation_results:
            return (
                sum(result.final_score for result in context.evaluation_results)
                / len(context.evaluation_results)
            )

        fallback = getattr(context.run, "avg_eval_score", None)
        try:
            return float(fallback or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _compute_run_duration(self, context: AgentRunContext) -> Optional[float]:
        elapsed = getattr(context.run, "elapsed_sec", None)
        if elapsed is not None:
            try:
                return float(elapsed)
            except (TypeError, ValueError):
                pass
        if context.run.started_at and context.run.ended_at:
            return context.run.ended_at - context.run.started_at
        return None

    def _run_scores(self, aggregate: AgentAggregate) -> List[tuple[AgentRunContext, float]]:
        return [(context, self._compute_run_score(context)) for context in aggregate.runs]

    def _benchmark_key(self, context: AgentRunContext) -> str:
        miner_details = getattr(context.run, "miner_info", None)
        provider = ""
        if miner_details and miner_details.provider:
            provider = miner_details.provider.strip().lower()
        if provider:
            return re.sub(r"[^a-z0-9]+", "-", provider).strip("-")
        name = (
            miner_details.agent_name
            if miner_details and miner_details.agent_name
            else context.run.agent_run_id
        )
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

    def _round_number(self, context: AgentRunContext) -> int:
        return int(context.round.round_number or _round_id_to_int(context.round.validator_round_id))

    def _fallback_round_epochs(self) -> float:
        try:
            value = float(settings.ROUND_SIZE_EPOCHS)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
        return 1.0

    def _round_epoch_length(self, round_model: ValidatorRound) -> float:
        if round_model is None:
            return self._fallback_round_epochs()

        # Prefer actual elapsed epochs when available
        start_epoch = getattr(round_model, "start_epoch", None)
        end_epoch = getattr(round_model, "end_epoch", None)
        if start_epoch is not None and end_epoch is not None:
            try:
                span = float(end_epoch) - float(start_epoch)
                if span <= 0:
                    span = 1.0
                return span
            except (TypeError, ValueError):
                pass

        # Fallback to configured maximum only if no boundaries are present
        max_epochs = getattr(round_model, "max_epochs", None)
        if max_epochs is not None:
            try:
                value = float(max_epochs)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass

        return self._fallback_round_epochs()

    def _round_top_score(self, context: AgentRunContext) -> float:
        round_number = self._round_number(context)
        benchmark_scores = [
            entry.get("score", 0.0)
            for entry in self._round_benchmark_cache.get(round_number, {}).values()
        ]
        if context.round.winners:
            try:
                winner_scores = [float(winner.get("score", 0.0)) for winner in context.round.winners]
                benchmark_scores.extend(winner_scores)
            except (TypeError, ValueError):
                pass
        if not benchmark_scores:
            return self._compute_run_score(context)
        return max(benchmark_scores)

    def _build_round_score_series(
        self,
        aggregate: AgentAggregate,
        aggregates: Dict[str, AgentAggregate],
    ) -> List[ScoreRoundDataPoint]:
        contexts_by_round: Dict[int, List[AgentRunContext]] = defaultdict(list)
        for context in aggregate.runs:
            round_id = self._round_number(context)
            if round_id <= 0:
                continue
            contexts_by_round[round_id].append(context)

        if not contexts_by_round:
            return []

        top_scores_by_round: Dict[int, float] = {}
        for other in aggregates.values():
            if other.is_sota:
                continue
            for round_id, scores in other.round_scores.items():
                if not scores:
                    continue
                average = sum(scores) / len(scores)
                current = top_scores_by_round.get(round_id)
                if current is None or average > current:
                    top_scores_by_round[round_id] = average

        datapoints: List[ScoreRoundDataPoint] = []
        for round_id in sorted(contexts_by_round.keys()):
            contexts = contexts_by_round[round_id]
            scores = [self._compute_run_score(ctx) for ctx in contexts]
            if not scores:
                continue

            average_score = sum(scores) / len(scores)
            top_score = top_scores_by_round.get(round_id, average_score)

            rank: Optional[int] = None
            ranks = aggregate.round_ranks.get(round_id)
            if ranks:
                rank = min(ranks)
            else:
                rank_candidates = [ctx.run.rank for ctx in contexts if ctx.run.rank is not None]
                if rank_candidates:
                    rank = min(rank_candidates)

            timestamp_candidates: List[float] = []
            for ctx in contexts:
                for candidate in (ctx.run.ended_at, ctx.run.started_at, ctx.round.started_at):
                    if candidate is not None:
                        timestamp_candidates.append(float(candidate))
                        break
            timestamp_value = min(timestamp_candidates) if timestamp_candidates else _ts(None)
            timestamp_dt = datetime.fromtimestamp(float(timestamp_value), tz=timezone.utc)

            benchmark_entries = self._round_benchmark_entries(contexts[0])
            reward_value = aggregate.round_rewards.get(round_id, 0.0)

            datapoints.append(
                ScoreRoundDataPoint(
                    round_id=round_id,
                    score=round(average_score, 3),
                    rank=rank,
                    topScore=round(top_score, 3),
                    reward=round(reward_value, 6) if reward_value else 0.0,
                    timestamp=timestamp_dt,
                    benchmarks=benchmark_entries,
                )
            )

        return datapoints

    def _round_benchmark_entries(self, context: AgentRunContext) -> Optional[List[Dict[str, Any]]]:
        round_number = self._round_number(context)
        entries = list(self._round_benchmark_cache.get(round_number, {}).values())
        return entries or None

    def _find_miner_info(self, context: AgentRunContext) -> Optional[MinerInfo]:
        miner_info = getattr(context.run, "miner_info", None)
        if miner_info:
            return miner_info

        if context.round.miners:
            for miner in context.round.miners:
                if miner.uid == context.run.miner_uid:
                    return miner

        if context.round.sota_agents:
            for miner in context.round.sota_agents:
                if miner.uid == context.run.miner_uid:
                    return miner

        return None

    def _resolve_aggregate(
        self,
        aggregates: Dict[str, AgentAggregate],
        agent_id: str,
    ) -> Optional[AgentAggregate]:
        if not agent_id:
            return None

        normalized = agent_id.strip()
        if normalized in aggregates:
            return aggregates[normalized]

        suffix = self._extract_uid(normalized)
        if suffix is not None:
            canonical = f"agent-{suffix}"
            if canonical in aggregates:
                return aggregates[canonical]

        return None

    def _extract_uid(self, agent_id: str) -> Optional[int]:
        candidate = agent_id.strip()
        if not candidate:
            return None

        if candidate.isdigit():
            try:
                return int(candidate)
            except ValueError:
                return None

        if "-" in candidate:
            prefix, suffix = candidate.split("-", 1)
            if prefix.lower() == "agent" and suffix.isdigit():
                try:
                    return int(suffix)
                except ValueError:
                    return None

        return None

    def _resolve_time_window(
        self,
        time_range: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        if start_date or end_date:
            start = self._ensure_timezone(start_date)
            end = self._ensure_timezone(end_date)
            return start, end

        if not time_range:
            return None, None

        normalized = time_range.strip().lower()
        duration: Optional[timedelta] = None

        if normalized.endswith("d") and normalized[:-1].isdigit():
            duration = timedelta(days=int(normalized[:-1]))
        elif normalized.endswith("h") and normalized[:-1].isdigit():
            duration = timedelta(hours=int(normalized[:-1]))
        elif normalized in {"week", "1w"}:
            duration = timedelta(days=7)
        elif normalized in {"month", "30d"}:
            duration = timedelta(days=30)

        if duration is None:
            return None, None

        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - duration
        return start_dt, end_dt

    def _ensure_timezone(self, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _filter_contexts_by_time_window(
        self,
        contexts: List[AgentRunContext],
        start_dt: Optional[datetime],
        end_dt: Optional[datetime],
    ) -> List[AgentRunContext]:
        if start_dt is None and end_dt is None:
            return contexts

        filtered: List[AgentRunContext] = []
        start_ts = start_dt.timestamp() if start_dt else None
        end_ts = end_dt.timestamp() if end_dt else None

        for context in contexts:
            run_start = (
                context.run.started_at
                or context.round.started_at
                or _ts(None)
            )
            round_end = getattr(context.round, "ended_at", None)
            run_end = context.run.ended_at or round_end or run_start

            if start_ts is not None and run_end < start_ts:
                continue
            if end_ts is not None and run_start > end_ts:
                continue

            filtered.append(context)

        return filtered

    def _truncate_datetime(self, value: datetime, granularity: str) -> datetime:
        granularity = granularity.lower()
        if granularity == "hour":
            return value.replace(minute=0, second=0, microsecond=0)
        if granularity == "week":
            monday = value - timedelta(days=value.weekday())
            return monday.replace(hour=0, minute=0, second=0, microsecond=0)
        if granularity == "month":
            return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if granularity == "all":
            return datetime.fromtimestamp(0, tz=timezone.utc)
        return value.replace(hour=0, minute=0, second=0, microsecond=0)

    def _format_trend_period(self, value: datetime, granularity: str) -> str:
        granularity = granularity.lower()
        if granularity == "hour":
            return value.strftime("%Y-%m-%d %H:00")
        if granularity == "week":
            iso_year, iso_week, _ = value.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        if granularity == "month":
            return value.strftime("%Y-%m")
        if granularity == "all":
            return "All Time"
        return value.strftime("%Y-%m-%d")

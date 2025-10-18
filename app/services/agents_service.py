from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentEvaluationRunORM, RoundORM
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
from app.utils.images import resolve_agent_image

logger = logging.getLogger(__name__)


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
    rounds: set = field(default_factory=set)
    first_seen: float = field(default_factory=lambda: float("inf"))
    last_seen: float = field(default_factory=lambda: 0.0)


_CACHE_TTL_ENV = "AGENTS_CACHE_TTL_SECONDS"
_DEFAULT_CACHE_TTL = 30
try:
    _CACHE_TTL_SECONDS = max(int(os.getenv(_CACHE_TTL_ENV, str(_DEFAULT_CACHE_TTL))), 0)
except ValueError:
    _CACHE_TTL_SECONDS = _DEFAULT_CACHE_TTL
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

    async def get_agent(self, agent_id: str) -> AgentDetailResponse:
        aggregates = await self._aggregate_agents()
        aggregate = self._resolve_aggregate(aggregates, agent_id)
        if aggregate is None:
            raise ValueError(f"Agent {agent_id} not found")

        agent_model = self._aggregate_to_agent(aggregate)
        score_round_data = [
            ScoreRoundDataPoint(
                validator_round_id=int(
                    context.round.round_number or _round_id_to_int(context.round.validator_round_id)
                ),
                score=score,
                rank=context.run.rank,
                top_score=self._round_top_score(context),
                reward=0.0,
                timestamp=datetime.fromtimestamp(
                    context.round.started_at or context.run.started_at or _ts(None),
                    tz=timezone.utc,
                ),
                benchmarks=self._round_benchmark_entries(context),
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

            comparisons.append(
                AgentComparison(
                    agentId=aggregate.agent_id,
                    name=self._aggregate_name(aggregate),
                    metrics=AgentComparisonMetrics(
                        averageScore=round(avg_score, 3),
                        successRate=round(success_rate, 2),
                        averageResponseTime=round(avg_duration, 2),
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
                "averageResponseTime": lambda comp: comp.metrics.averageResponseTime,
                "totalRuns": lambda comp: comp.metrics.totalRuns,
            }[metric]
            sorted_items = sorted(comparisons, key=key_func, reverse=reverse)
            return sorted_items[0].agentId if sorted_items else default

        comparison_metrics = ComparisonMetrics(
            bestPerformer=_select("averageScore"),
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
                averageScore=0.0,
                bestScore=0.0,
                worstScore=0.0,
                successRate=0.0,
                averageResponseTime=0.0,
                totalTasks=0,
                completedTasks=0,
                taskCompletionRate=0.0,
                scoreDistribution=ScoreDistribution(),
                performanceTrend=[],
            )

        scores = []
        durations = []
        total_tasks = 0
        completed_tasks = 0
        successes = 0

        for context in contexts:
            score = self._compute_run_score(context)
            scores.append(score)
            duration = self._compute_run_duration(context)
            if duration is not None:
                durations.append(duration)
            task_count = len(context.tasks)
            total_tasks += task_count
            completed_tasks += len(
                [er for er in context.evaluation_results if er.final_score >= 0.5]
            )
            if score >= 0.5:
                successes += 1

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

        sorted_runs = sorted(
            contexts,
            key=lambda ctx: ctx.run.started_at or ctx.round.started_at or _ts(None),
        )

        buckets: Dict[datetime, Dict[str, Any]] = {}
        resolved_granularity = (granularity or "").lower() or "day"

        for context in sorted_runs:
            start_ts = context.run.started_at or context.round.started_at or _ts(None)
            start_dt_context = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            bucket_key = self._truncate_datetime(start_dt_context, resolved_granularity)
            bucket = buckets.setdefault(
                bucket_key,
                {
                    "scores": [],
                    "durations": [],
                    "successes": 0,
                    "count": 0,
                },
            )
            score = self._compute_run_score(context)
            duration = self._compute_run_duration(context) or 0.0

            bucket["scores"].append(score)
            bucket["durations"].append(duration)
            bucket["successes"] += 1 if score >= 0.5 else 0
            bucket["count"] += 1

        trend: List[PerformanceTrend] = []
        for bucket_dt in sorted(buckets.keys()):
            bucket = buckets[bucket_dt]
            bucket_scores = bucket["scores"]
            bucket_durations = bucket["durations"]
            bucket_successes = bucket["successes"]
            bucket_count = bucket["count"]

            period_label = self._format_trend_period(bucket_dt, resolved_granularity)

            trend.append(
                PerformanceTrend(
                    period=period_label,
                    score=round(
                        sum(bucket_scores) / len(bucket_scores) if bucket_scores else 0.0,
                        3,
                    ),
                    successRate=round(
                        (bucket_successes / bucket_count * 100.0) if bucket_count else 0.0,
                        2,
                    ),
                    responseTime=round(
                        sum(bucket_durations) / len(bucket_durations)
                        if bucket_durations
                        else 0.0,
                        2,
                    ),
                )
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
            averageScore=round(average_score, 3),
            bestScore=round(best_score, 3),
            worstScore=round(worst_score, 3),
            successRate=round(success_rate, 2),
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
                selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.miner_snapshots)
                .selectinload(RoundORM.validator_snapshots),
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
            non_sota_contexts.sort(
                key=lambda ctx: self._compute_run_score(ctx),
                reverse=True,
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

            duration = self._compute_run_duration(context)
            if duration is not None:
                aggregate.durations.append(duration)

            aggregate.total_tasks += len(context.tasks)
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
                bench_name = (
                    context.run.miner_info.agent_name
                    if context.run.miner_info and context.run.miner_info.agent_name
                    else context.run.agent_run_id
                )
                bench_provider = (
                    context.run.miner_info.provider
                    if context.run.miner_info and context.run.miner_info.provider
                    else None
                )
                entry = {
                    "name": bench_name,
                    "provider": bench_provider,
                    "score": run_score,
                }
                existing_entry = round_benchmark_scores[round_identifier].get(bench_key)
                if existing_entry is None or run_score > existing_entry.get("score", 0.0):
                    round_benchmark_scores[round_identifier][bench_key] = entry

        round_best_scores: Dict[int, float] = {}
        for aggregate in aggregates.values():
            if aggregate.round_scores:
                latest_round = max(aggregate.round_scores.keys())
                aggregate.latest_round_number = latest_round
                latest_scores = aggregate.round_scores.get(latest_round, [])
                aggregate.latest_round_score = (
                    sum(latest_scores) / len(latest_scores) if latest_scores else None
                )
                latest_ranks = aggregate.round_ranks.get(latest_round, [])
                if latest_ranks:
                    aggregate.latest_round_rank = min(latest_ranks)
                if aggregate.latest_round_score is not None and not aggregate.is_sota:
                    round_leaderboards[latest_round].append((aggregate.agent_id, aggregate.latest_round_score))
            aggregate.runs.sort(
                key=lambda ctx: (
                    int(ctx.round.round_number or _round_id_to_int(ctx.round.validator_round_id)),
                    ctx.run.started_at or ctx.round.started_at or 0,
                ),
                reverse=True,
            )

        for round_number, entries in round_leaderboards.items():
            sorted_entries = sorted(entries, key=lambda item: item[1], reverse=True)
            if sorted_entries:
                round_best_scores[round_number] = sorted_entries[0][1]
            for rank, (agent_id, _) in enumerate(sorted_entries, start=1):
                aggregate = aggregates[agent_id]
                aggregate.global_round_ranks[round_number] = rank
                if aggregate.latest_round_number == round_number:
                    aggregate.latest_round_global_rank = rank

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
            # SQLite may return ISO strings for datetime columns
            last_updated = datetime.fromisoformat(last_updated.replace(" ", "T"))
        if isinstance(last_updated, datetime) and last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        return total, last_updated

    async def _fetch_agent_contexts(self, agent_id: str) -> List[AgentRunContext]:
        uid = self._extract_uid(agent_id)

        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.miner_snapshots)
                .selectinload(RoundORM.validator_snapshots),
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
            currentScore=latest_round_score,
            currentTopScore=latest_round_top_score,
            currentRank=current_rank_value or 0,
            bestRankEver=best_rank or 0,
            roundsParticipated=len({round_id for round_id in aggregate.rounds if round_id}),
            alphaWonInPrizes=0.0,
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

    def _benchmark_key(self, context: AgentRunContext) -> str:
        provider = ""
        if context.run.miner_info and context.run.miner_info.provider:
            provider = context.run.miner_info.provider.strip().lower()
        if provider:
            return re.sub(r"[^a-z0-9]+", "-", provider).strip("-")
        name = (
            context.run.miner_info.agent_name
            if context.run.miner_info and context.run.miner_info.agent_name
            else context.run.agent_run_id
        )
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")

    def _round_number(self, context: AgentRunContext) -> int:
        return int(context.round.round_number or _round_id_to_int(context.round.validator_round_id))

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
            return 0.0
        return max(benchmark_scores)

    def _round_benchmark_entries(self, context: AgentRunContext) -> Optional[List[Dict[str, Any]]]:
        round_number = self._round_number(context)
        entries = list(self._round_benchmark_cache.get(round_number, {}).values())
        return entries or None

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

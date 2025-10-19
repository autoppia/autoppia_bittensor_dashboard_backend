from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.miners import (
    Miner,
    MinerDetailResponse,
    MinerListResponse,
    MinerPerformanceMetrics,
    MinerStatus,
    PerformanceTrend,
    ScoreDistribution,
    TimeRange,
    Granularity,
)
from app.models.ui.miners import Pagination
from app.services.agents_service import AgentsService, AgentAggregate
from app.utils.images import resolve_agent_image
from app.utils.urls import build_taostats_miner_url

logger = logging.getLogger(__name__)


def _ts_to_iso(ts: Optional[float]) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


class MinersService:
    """SQL-backed service for miners."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.agents_service = AgentsService(session)

    async def list_miners(
        self,
        page: int,
        limit: int,
        is_sota: Optional[bool] = None,
        status: Optional[MinerStatus] = None,
        sort_by: str = "averageScore",
        sort_order: str = "desc",
        search: Optional[str] = None,
    ) -> MinerListResponse:
        aggregates = await self.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        miners = [self._aggregate_to_miner(agg) for agg in aggregates.values()]

        if is_sota is not None:
            miners = [miner for miner in miners if miner.isSota == is_sota]

        if status:
            miners = [miner for miner in miners if miner.status == status]

        if search:
            lowered = search.lower()
            miners = [
                miner
                for miner in miners
                if lowered in miner.name.lower()
                or lowered in miner.hotkey.lower()
                or lowered in miner.id.lower()
            ]

        miners = self._sort_miners(miners, sort_by, sort_order)

        total = len(miners)
        start = (page - 1) * limit
        end = start + limit
        paginated = miners[start:end]
        total_pages = max(1, (total + limit - 1) // limit)

        pagination = Pagination(page=page, limit=limit, total=total, totalPages=total_pages)
        return MinerListResponse(miners=paginated, pagination=pagination)

    async def get_miner(self, uid: int) -> MinerDetailResponse:
        aggregates = await self.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        for aggregate in aggregates.values():
            if aggregate.uid == uid:
                return MinerDetailResponse(miner=self._aggregate_to_miner(aggregate))
        raise ValueError(f"Miner {uid} not found")

    async def get_miner_performance(
        self,
        uid: int,
        time_range: TimeRange = TimeRange.SEVEN_DAYS,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        granularity: Granularity = Granularity.DAY,
    ) -> MinerPerformanceMetrics:
        aggregates = await self.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        for aggregate in aggregates.values():
            if aggregate.uid != uid:
                continue

            start_ts, end_ts = self._compute_time_bounds(time_range, start_date, end_date)
            filtered = self._filter_aggregate_by_range(aggregate, start_ts, end_ts)
            performance = self.agents_service._build_performance_metrics(filtered)  # type: ignore[attr-defined]

            # Override the time range to reflect the requested window when bounds were supplied.
            performance_range = dict(performance.timeRange)
            if start_ts is not None:
                performance_range["start"] = _ts_to_iso(start_ts)
            if end_ts is not None:
                performance_range["end"] = _ts_to_iso(end_ts)

            return self._convert_performance_metrics(uid, performance, performance_range)

        raise ValueError(f"Miner {uid} not found")

    def _aggregate_to_miner(self, aggregate: AgentAggregate) -> Miner:
        miner_info = aggregate.miner
        name = miner_info.agent_name if miner_info and miner_info.agent_name else aggregate.agent_id
        hotkey = miner_info.hotkey if miner_info and miner_info.hotkey else ""
        image_url = resolve_agent_image(miner_info)
        github = miner_info.github if miner_info else None
        description = miner_info.description if miner_info else ""
        taostats_url = build_taostats_miner_url(hotkey) or ""
        average_score = (
            aggregate.total_score / aggregate.total_runs if aggregate.total_runs else 0.0
        )
        current_score = (
            aggregate.latest_round_score
            if aggregate.latest_round_score is not None
            else average_score
        )
        success_rate = (
            (aggregate.successful_runs / aggregate.total_runs) * 100 if aggregate.total_runs else 0.0
        )
        best_score = aggregate.best_score
        average_duration = (
            sum(aggregate.durations) / len(aggregate.durations) if aggregate.durations else 0.0
        )

        last_seen_iso = _ts_to_iso(aggregate.last_seen)
        created_iso = _ts_to_iso(
            aggregate.first_seen if aggregate.first_seen != float("inf") else aggregate.last_seen
        )
        status = self._determine_status(aggregate)

        return Miner(
            id=str(aggregate.uid) if aggregate.uid is not None else aggregate.agent_id,
            uid=aggregate.uid or -1,
            name=name,
            hotkey=hotkey,
            imageUrl=image_url,
            githubUrl=github,
            taostatsUrl=taostats_url,
            isSota=aggregate.is_sota,
            status=status,
            description=description,
            totalRuns=aggregate.total_runs,
            successfulRuns=aggregate.successful_runs,
            averageScore=current_score,
            bestScore=best_score,
            successRate=success_rate,
            averageResponseTime=average_duration,
            totalTasks=aggregate.total_tasks,
            completedTasks=aggregate.completed_tasks,
            lastSeen=last_seen_iso,
            createdAt=created_iso,
            updatedAt=last_seen_iso,
        )

    def _determine_status(self, aggregate: AgentAggregate) -> MinerStatus:
        now_ts = datetime.now(timezone.utc).timestamp()
        last_seen = aggregate.last_seen or 0.0

        if aggregate.total_runs == 0:
            return MinerStatus.MAINTENANCE

        if last_seen <= 0.0:
            return MinerStatus.INACTIVE

        inactivity = now_ts - last_seen
        if inactivity > 86400:  # 24 hours
            return MinerStatus.INACTIVE

        if aggregate.successful_runs == 0:
            return MinerStatus.MAINTENANCE

        return MinerStatus.ACTIVE

    @staticmethod
    def _dt_to_ts(value: Optional[datetime]) -> Optional[float]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.timestamp()

    def _compute_time_bounds(
        self,
        time_range: TimeRange,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> tuple[Optional[float], Optional[float]]:
        duration_map = {
            TimeRange.ONE_HOUR: 3600,
            TimeRange.TWENTY_FOUR_HOURS: 86400,
            TimeRange.SEVEN_DAYS: 7 * 86400,
            TimeRange.THIRTY_DAYS: 30 * 86400,
            TimeRange.NINETY_DAYS: 90 * 86400,
            TimeRange.ONE_YEAR: 365 * 86400,
            TimeRange.ALL: None,
        }

        duration = duration_map[time_range]
        start_ts = self._dt_to_ts(start_date)
        end_ts = self._dt_to_ts(end_date)

        now_ts = datetime.now(timezone.utc).timestamp()
        reference_end = end_ts or now_ts

        if duration is not None:
            if start_ts is None and end_ts is None:
                end_ts = reference_end
                start_ts = end_ts - duration
            elif start_ts is None and end_ts is not None:
                start_ts = end_ts - duration
            elif start_ts is not None and end_ts is None:
                end_ts = start_ts + duration

        return start_ts, end_ts

    def _filter_aggregate_by_range(
        self,
        aggregate: AgentAggregate,
        start_ts: Optional[float],
        end_ts: Optional[float],
    ) -> AgentAggregate:
        filtered = AgentAggregate(
            agent_id=aggregate.agent_id,
            uid=aggregate.uid,
            miner=aggregate.miner,
            is_sota=aggregate.is_sota,
            version=aggregate.version,
        )

        for context in aggregate.runs:
            run_start = context.run.started_at or context.round.started_at or 0.0
            if start_ts is not None and run_start < start_ts:
                continue
            if end_ts is not None and run_start > end_ts:
                continue

            filtered.runs.append(context)
            filtered.total_runs += 1

            score = self.agents_service._compute_run_score(context)  # type: ignore[attr-defined]
            filtered.total_score += score
            filtered.best_score = max(filtered.best_score, score)
            if score >= 0.5:
                filtered.successful_runs += 1

            duration = self.agents_service._compute_run_duration(context)  # type: ignore[attr-defined]
            if duration is not None:
                filtered.durations.append(duration)

            filtered.total_tasks += len(context.tasks)
            filtered.completed_tasks += len(
                [er for er in context.evaluation_results if er.final_score >= 0.5]
            )

            if context.run.rank is not None:
                filtered.ranks.append(context.run.rank)

            filtered.rounds.add(context.round.validator_round_id)

            if run_start > filtered.last_seen:
                filtered.last_seen = run_start
            if run_start < filtered.first_seen:
                filtered.first_seen = run_start

        if not filtered.runs:
            filtered.first_seen = aggregate.first_seen
            filtered.last_seen = aggregate.last_seen

        return filtered

    def _convert_performance_metrics(
        self,
        uid: int,
        metrics: Any,
        time_range: Dict[str, str],
    ) -> MinerPerformanceMetrics:
        score_distribution = ScoreDistribution(**metrics.scoreDistribution.model_dump())
        trend = [
            PerformanceTrend(**item.model_dump())
            for item in metrics.performanceTrend
        ]

        return MinerPerformanceMetrics(
            uid=uid,
            timeRange=time_range,
            totalRuns=metrics.totalRuns,
            successfulRuns=metrics.successfulRuns,
            failedRuns=metrics.failedRuns,
            averageScore=metrics.averageScore,
            bestScore=metrics.bestScore,
            worstScore=metrics.worstScore,
            successRate=metrics.successRate,
            averageResponseTime=metrics.averageResponseTime,
            totalTasks=metrics.totalTasks,
            completedTasks=metrics.completedTasks,
            taskCompletionRate=metrics.taskCompletionRate,
            scoreDistribution=score_distribution,
            performanceTrend=trend,
        )

    def _sort_miners(self, miners: List[Miner], sort_by: str, sort_order: str) -> List[Miner]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(miners, key=lambda miner: getattr(miner, sort_by), reverse=reverse)
        except AttributeError:
            return miners

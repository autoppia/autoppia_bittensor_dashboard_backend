from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvaluationRunORM, RoundORM

from app.models.core import EvaluationResult, Task, TaskSolution
from app.models.ui.agent_runs import (
    Action,
    AgentRun,
    AgentInfo,
    Event,
    EventType,
    Log,
    LogLevel,
    Metrics,
    Metric,
    PerformanceByUseCase,
    PerformanceByWebsite,
    Personas,
    RecentActivity,
    RoundInfo,
    RunStatus,
    ScoreDistribution,
    Statistics,
    Summary,
    Task as UITask,
    TaskStatus,
    TopPerformingUseCase,
    TopPerformingWebsite,
    ValidatorInfo,
    Website,
)
from app.services.redis_cache import REDIS_CACHE_TTL, redis_cache
from app.services.service_utils import rollback_on_error
from app.services.ui.rounds_service import AgentRunContext, RoundsService
from app.data import get_validator_metadata
from app.utils.images import resolve_agent_image, resolve_validator_image

logger = logging.getLogger(__name__)


AGENT_RUN_STATS_CACHE_PREFIX = "agent_run_statistics"
AGENT_RUN_STATS_CACHE_TTL = REDIS_CACHE_TTL.get(
    "agent_run_statistics_final",
    7 * 24 * 3600,
)
AGENT_RUN_STATS_ACTIVE_TTL = 60


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _extract_host(url: Optional[str]) -> str:
    if not url:
        return "unknown"
    parsed = urlparse(url)
    return parsed.netloc or parsed.path or "unknown"


def _map_website_port_to_name(url: Optional[str]) -> str:
    """
    Map localhost:PORT URLs to friendly website names.
    Returns the friendly name if found, otherwise returns the host as-is.
    """
    if not url:
        return "unknown"

    # Port to name mapping (aligned with overview_service.py and frontend)
    PORT_TO_NAME = {
        "8000": "AutoCinema",
        "8001": "AutoBooks",
        "8002": "Autozone",
        "8003": "AutoDining",
        "8004": "AutoCRM",
        "8005": "AutoMail",
        "8006": "AutoDelivery",
        "8007": "AutoLodge",
        "8008": "AutoConnect",
        "8009": "AutoWork",
        "8010": "AutoCalendar",
        "8011": "AutoList",
        "8012": "AutoDrive",
        "8013": "AutoHealth",
        "8014": "AutoFinance",
    }

    try:
        # Extract port from URL
        parsed = urlparse(url if url.startswith("http") else f"http://{url}")
        port = str(parsed.port) if parsed.port else None

        if port and port in PORT_TO_NAME:
            return PORT_TO_NAME[port]
    except Exception:
        pass

    # Fallback to extracting host
    return _extract_host(url)


def _safe_int(value: Optional[float]) -> int:
    if value is None:
        return 0
    return int(round(value))


class AgentRunsService:
    """SQL-backed business logic for agent evaluation runs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    @rollback_on_error
    async def list_agent_runs(
        self,
        page: int = 1,
        limit: int = 20,
        round_number: Optional[int] = None,
        validator_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc",
    ) -> Dict[str, object]:
        skip = max(0, (page - 1) * limit)

        status_filter = status.lower() if status else None
        validator_uid = _parse_identifier(validator_id) if validator_id else None
        miner_uid = _parse_identifier(agent_id) if agent_id else None
        start_ts = _to_timestamp(start_date)
        end_ts = _to_timestamp(end_date)
        query_term = query.lower() if query else None

        sort_columns: Dict[str, Any] = {
            "startTime": AgentEvaluationRunORM.started_at,
            "endTime": AgentEvaluationRunORM.ended_at,
            "averageScore": AgentEvaluationRunORM.average_score,
            "score": AgentEvaluationRunORM.average_score,
            "overallScore": AgentEvaluationRunORM.average_score,
            "totalTasks": AgentEvaluationRunORM.total_tasks,
            "completedTasks": AgentEvaluationRunORM.completed_tasks,
        }

        if sort_by in {"successRate"}:
            sort_columns["successRate"] = func.coalesce(
                AgentEvaluationRunORM.completed_tasks
                * 100.0
                / func.nullif(AgentEvaluationRunORM.total_tasks, 0),
                0.0,
            )

        order_expr = sort_columns.get(sort_by, AgentEvaluationRunORM.started_at)
        if isinstance(order_expr, (int, float)):
            order_expr = AgentEvaluationRunORM.started_at

        if sort_order.lower() == "desc":
            order_clause = order_expr.desc()
        else:
            order_clause = order_expr.asc()

        filters: List[Any] = []
        if validator_uid is not None:
            filters.append(AgentEvaluationRunORM.validator_uid == validator_uid)
        if miner_uid is not None:
            filters.append(AgentEvaluationRunORM.miner_uid == miner_uid)
        if round_number is not None:
            filters.append(
                AgentEvaluationRunORM.validator_round.has(
                    RoundORM.round_number == round_number
                )
            )
        if start_ts is not None:
            filters.append(AgentEvaluationRunORM.started_at >= start_ts)
        if end_ts is not None:
            filters.append(AgentEvaluationRunORM.started_at <= end_ts)

        if status_filter == RunStatus.COMPLETED.value:
            filters.append(AgentEvaluationRunORM.ended_at.is_not(None))
        elif status_filter == RunStatus.RUNNING.value:
            filters.append(
                and_(
                    AgentEvaluationRunORM.started_at.is_not(None),
                    AgentEvaluationRunORM.ended_at.is_(None),
                )
            )
        elif status_filter == RunStatus.PENDING.value:
            filters.append(AgentEvaluationRunORM.started_at.is_(None))
        elif status_filter in {RunStatus.FAILED.value, RunStatus.CANCELLED.value}:
            available_rounds = await self._list_available_round_numbers()
            return {
                "runs": [],
                "total": 0,
                "page": page,
                "limit": limit,
                "availableRounds": available_rounds,
                "selectedRound": round_number,
            }

        if query_term:
            like_pattern = f"%{query_term}%"
            filters.append(
                or_(
                    func.lower(AgentEvaluationRunORM.agent_run_id).like(like_pattern),
                    func.lower(AgentEvaluationRunORM.miner_hotkey).like(like_pattern),
                    func.lower(AgentEvaluationRunORM.validator_hotkey).like(
                        like_pattern
                    ),
                    cast(AgentEvaluationRunORM.validator_uid, String).like(
                        like_pattern
                    ),
                    cast(AgentEvaluationRunORM.miner_uid, String).like(like_pattern),
                )
            )

        base_stmt = (
            select(
                AgentEvaluationRunORM.agent_run_id,
                func.count().over().label("full_count"),
            )
            .where(*filters)
            .order_by(
                order_clause,
                AgentEvaluationRunORM.agent_run_id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(base_stmt)
        rows = result.all()

        agent_run_ids: List[str] = [row.agent_run_id for row in rows]
        total: int = int(rows[0].full_count) if rows else 0

        if not agent_run_ids:
            available_rounds = await self._list_available_round_numbers()
            return {
                "runs": [],
                "total": total,
                "page": page,
                "limit": limit,
                "availableRounds": available_rounds,
                "selectedRound": round_number,
            }

        contexts: List[AgentRunContext] = await self.rounds_service.list_agent_run_contexts(
            include_details=True,
            agent_run_ids=agent_run_ids,
        )

        runs = [self._build_run_summary(context) for context in contexts]

        available_rounds = await self._list_available_round_numbers()

        return {
            "runs": runs,
            "total": total,
            "page": page,
            "limit": limit,
            "availableRounds": available_rounds,
            "selectedRound": round_number,
        }

    async def _list_available_round_numbers(self) -> List[int]:
        stmt = (
            select(func.distinct(RoundORM.round_number))
            .where(RoundORM.round_number.is_not(None))
            .order_by(RoundORM.round_number.desc())
            .limit(2)  # fuerza a devolver solo 2 registros
        )
        result = await self.session.scalars(stmt)
        return [int(value) for value in result if value is not None]

    @rollback_on_error
    async def get_agent_run(self, agent_run_id: str) -> Optional[AgentRun]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_agent_run(context)

    @rollback_on_error
    async def get_personas(self, agent_run_id: str) -> Optional[Personas]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_personas(context)

    @rollback_on_error
    async def get_statistics(self, agent_run_id: str) -> Optional[Statistics]:
        cache_key = f"{AGENT_RUN_STATS_CACHE_PREFIX}:{agent_run_id}"

        cached_stats = redis_cache.get(cache_key)
        if cached_stats is not None:
            logger.debug("agent_run_statistics cache hit for %s", agent_run_id)
            return cached_stats

        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        statistics = self._build_statistics(context)

        if statistics is None:
            return None

        run_finished = bool(getattr(context.run, "ended_at", None))
        ttl = AGENT_RUN_STATS_CACHE_TTL if run_finished else AGENT_RUN_STATS_ACTIVE_TTL
        redis_cache.set(cache_key, statistics, ttl=ttl)
        logger.debug(
            "agent_run_statistics cached for %s (ttl=%ss, finished=%s)",
            agent_run_id,
            ttl,
            run_finished,
        )

        return statistics

    @rollback_on_error
    async def get_summary(self, agent_run_id: str) -> Optional[Summary]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_summary(context)

    @rollback_on_error
    async def get_tasks(self, agent_run_id: str) -> Optional[List[UITask]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        _, _, task_map = self._index_results(context)
        return list(task_map.values())

    @rollback_on_error
    async def get_timeline(self, agent_run_id: str) -> Optional[List[Event]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        events: List[Event] = []
        start_time = (
            _ts_to_iso(context.run.started_at) or datetime.now(timezone.utc).isoformat()
        )
        events.append(
            Event(
                timestamp=start_time,
                type=EventType.RUN_STARTED,
                message="Agent run started",
            )
        )

        for evaluation in context.evaluation_results:
            task_event_time = start_time
            if evaluation.stats and evaluation.stats.start_time:
                task_event_time = _ts_to_iso(evaluation.stats.start_time) or start_time
            events.append(
                Event(
                    timestamp=task_event_time,
                    type=EventType.TASK_COMPLETED,
                    message=f"Task {evaluation.task_id} evaluated",
                    taskId=evaluation.task_id,
                )
            )

        if context.run.ended_at:
            events.append(
                Event(
                    timestamp=_ts_to_iso(context.run.ended_at) or start_time,
                    type=EventType.RUN_COMPLETED,
                    message="Agent run completed",
                )
            )

        return events

    @rollback_on_error
    async def get_logs(self, agent_run_id: str) -> Optional[List[Log]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        logs: List[Log] = []
        for evaluation in context.evaluation_results:
            if evaluation.feedback and evaluation.feedback.execution_history:
                for entry in evaluation.feedback.execution_history:
                    message = str(entry)
                    logs.append(
                        Log(
                            timestamp=_ts_to_iso(context.run.started_at) or "",
                            level=LogLevel.INFO,
                            message=message,
                        )
                    )
        return logs

    @rollback_on_error
    async def get_metrics(self, agent_run_id: str) -> Optional[Metrics]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        timestamps = []
        if context.run.started_at:
            timestamps.append(context.run.started_at)
        if context.run.ended_at:
            timestamps.append(context.run.ended_at)
        if not timestamps:
            timestamps.append(datetime.now(timezone.utc).timestamp())

        metrics_time = [
            Metric(timestamp=_ts_to_iso(ts) or "", value=float(index + 1))
            for index, ts in enumerate(sorted(timestamps))
        ]

        duration = int(
            (context.run.ended_at or context.run.started_at or 0)
            - (context.run.started_at or 0)
        )

        return Metrics(
            cpu=metrics_time,
            memory=metrics_time,
            network=metrics_time,
            duration=duration,
            peakCpu=max((metric.value for metric in metrics_time), default=0.0),
            peakMemory=max((metric.value for metric in metrics_time), default=0.0),
            totalNetworkTraffic=len(metrics_time) * 100,
        )

    async def compare_runs(self, run_ids: List[str]) -> Dict[str, Any]:
        contexts: List[AgentRunContext] = []
        for run_id in run_ids:
            try:
                context = await self.rounds_service.get_agent_run_context(run_id)
            except ValueError:
                continue
            contexts.append(context)

        runs: List[AgentRun] = [self._build_agent_run(context) for context in contexts]

        if not runs:
            return {
                "runs": [],
                "comparison": {
                    "bestScore": "",
                    "fastest": "",
                    "mostTasks": "",
                    "bestSuccessRate": "",
                },
            }

        def _success_rate(run: AgentRun) -> float:
            return (
                run.successfulTasks / run.totalTasks * 100.0 if run.totalTasks else 0.0
            )

        best_score_run = max(
            runs, key=lambda run: run.score if run.score is not None else 0.0
        )
        fastest_run = min(
            runs,
            key=lambda run: run.duration if run.duration is not None else float("inf"),
        )
        most_tasks_run = max(runs, key=lambda run: run.totalTasks)
        best_success_run = max(runs, key=_success_rate)

        return {
            "runs": [run.model_dump() for run in runs],
            "comparison": {
                "bestScore": best_score_run.runId,
                "fastest": fastest_run.runId,
                "mostTasks": most_tasks_run.runId,
                "bestSuccessRate": best_success_run.runId,
            },
        }

    def _build_agent_run(self, context: AgentRunContext) -> AgentRun:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)

        average_score = self._compute_average_score(context.evaluation_results)
        overall_score = _safe_int(average_score * 100)
        average_evaluation_time = self._average_evaluation_time(context)

        validator_name, validator_image = self._resolve_validator_identity(context)
        (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            agent_identifier,
            agent_description,
        ) = self._resolve_agent_identity(context)
        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)
        return AgentRun(
            runId=context.run.agent_run_id,
            agentId=agent_identifier,
            agentUid=agent_uid,
            agentHotkey=agent_hotkey,
            agentName=agent_name,
            roundId=round_id_value or 0,
            validatorId=_format_validator_id(context.run.validator_uid),
            validatorName=validator_name,
            validatorImage=validator_image,
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at) or "",
            status=self._run_status(context),
            totalTasks=total_tasks,
            completedTasks=success_count,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            score=average_score,
            ranking=context.run.rank or 0,
            duration=_safe_int(
                (context.run.ended_at or context.run.started_at or 0)
                - (context.run.started_at or 0)
            ),
            overallScore=overall_score,
            averageEvaluationTime=(
                round(average_evaluation_time, 3)
                if average_evaluation_time is not None
                else None
            ),
            totalWebsites=len(websites),
            websites=websites,
            tasks=ui_tasks,
            metadata={
                **(context.run.metadata or {}),
                "agentImage": agent_image,
                "agentDescription": agent_description,
            },
        )

    def _build_personas(self, context: AgentRunContext) -> Personas:
        validator_name, validator_image = self._resolve_validator_identity(context)
        (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            agent_identifier,
            agent_description,
        ) = self._resolve_agent_identity(context)

        round_number_value = context.round.round_number
        if round_number_value is None:
            round_number_value = _round_id_to_int(context.round.validator_round_id)

        round_info = RoundInfo(
            id=round_number_value or 0,
            name=context.round.validator_round_id,
            status=context.round.status,
            startTime=_ts_to_iso(context.round.started_at) or "",
            endTime=_ts_to_iso(context.round.ended_at),
        )

        validator_info = ValidatorInfo(
            id=_format_validator_id(context.run.validator_uid),
            name=validator_name,
            image=validator_image,
            description="",
            website="",
            github="",
        )

        agent_info = AgentInfo(
            id=agent_identifier,
            uid=agent_uid,
            hotkey=agent_hotkey,
            name=agent_name,
            type="sota" if context.run.is_sota else "miner",
            image=agent_image,
            description=agent_description,
        )

        return Personas(round=round_info, validator=validator_info, agent=agent_info)

    def _summarize_ui_tasks(
        self,
        ui_tasks: List[UITask],
    ) -> Tuple[
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, Dict[str, float]]],
        float,
    ]:
        website_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0.0,
                "successful": 0.0,
                "score_sum": 0.0,
                "duration_sum": 0.0,
            }
        )
        use_case_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0.0,
                "successful": 0.0,
                "score_sum": 0.0,
                "duration_sum": 0.0,
            }
        )
        # New: website + use_case combined stats
        website_usecase_stats: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "tasks": 0.0,
                    "successful": 0.0,
                    "score_sum": 0.0,
                    "duration_sum": 0.0,
                }
            )
        )
        total_duration = 0.0

        for task in ui_tasks:
            duration = float(getattr(task, "duration", 0) or 0)
            score = float(getattr(task, "score", 0.0) or 0.0)
            success = task.status == TaskStatus.COMPLETED

            total_duration += duration

            host = _map_website_port_to_name(task.website)
            host_stats = website_stats[host]
            host_stats["tasks"] += 1
            host_stats["score_sum"] += score
            host_stats["duration_sum"] += duration
            if success:
                host_stats["successful"] += 1

            use_case = task.useCase or "unknown"
            use_case_entry = use_case_stats[use_case]
            use_case_entry["tasks"] += 1
            use_case_entry["score_sum"] += score
            use_case_entry["duration_sum"] += duration
            if success:
                use_case_entry["successful"] += 1

            # Track stats for (website, use_case) combination
            website_uc_entry = website_usecase_stats[host][use_case]
            website_uc_entry["tasks"] += 1
            website_uc_entry["score_sum"] += score
            website_uc_entry["duration_sum"] += duration
            if success:
                website_uc_entry["successful"] += 1

        return website_stats, use_case_stats, website_usecase_stats, total_duration

    def _build_statistics(self, context: AgentRunContext) -> Statistics:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(
            self._compute_average_score(context.evaluation_results) * 100
        )

        website_stats_map, use_case_stats_map, website_usecase_stats, total_duration = (
            self._summarize_ui_tasks(ui_tasks)
        )

        performance_by_website = []
        for website_key, values in website_stats_map.items():
            # Build use cases specific to this website
            use_cases_for_website = []
            if website_key in website_usecase_stats:
                for uc_name, uc_values in website_usecase_stats[website_key].items():
                    use_cases_for_website.append(
                        PerformanceByUseCase(
                            useCase=uc_name,
                            tasks=int(uc_values["tasks"]),
                            successful=int(uc_values["successful"]),
                            failed=int(
                                max(uc_values["tasks"] - uc_values["successful"], 0)
                            ),
                            averageScore=(
                                (uc_values["score_sum"] / uc_values["tasks"])
                                if uc_values["tasks"]
                                else 0.0
                            ),
                            averageDuration=(
                                (uc_values["duration_sum"] / uc_values["tasks"])
                                if uc_values["tasks"]
                                else 0.0
                            ),
                        )
                    )

            performance_by_website.append(
                PerformanceByWebsite(
                    website=website_key,
                    tasks=int(values["tasks"]),
                    successful=int(values["successful"]),
                    failed=int(max(values["tasks"] - values["successful"], 0)),
                    averageScore=(
                        (values["score_sum"] / values["tasks"])
                        if values["tasks"]
                        else 0.0
                    ),
                    averageDuration=(
                        (values["duration_sum"] / values["tasks"])
                        if values["tasks"]
                        else 0.0
                    ),
                    useCases=use_cases_for_website,
                )
            )

        excellent = len(
            [er for er in context.evaluation_results if er.final_score >= 0.9]
        )
        good = len(
            [er for er in context.evaluation_results if 0.7 <= er.final_score < 0.9]
        )
        average = len(
            [er for er in context.evaluation_results if 0.5 <= er.final_score < 0.7]
        )
        poor = len(context.evaluation_results) - excellent - good - average

        score_distribution = ScoreDistribution(
            excellent=excellent,
            good=good,
            average=average,
            poor=max(poor, 0),
        )

        return Statistics(
            runId=context.run.agent_run_id,
            overallScore=overall_score,
            totalTasks=total_tasks,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            websites=len(website_stats_map) or len(websites),
            averageTaskDuration=(total_duration / total_tasks) if total_tasks else 0.0,
            successRate=(success_count / total_tasks * 100) if total_tasks else 0.0,
            scoreDistribution=score_distribution,
            performanceByWebsite=performance_by_website,
        )

    def _build_summary(self, context: AgentRunContext) -> Summary:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(
            self._compute_average_score(context.evaluation_results) * 100
        )
        agent_name, _, agent_uid, agent_hotkey, agent_identifier, _ = (
            self._resolve_agent_identity(context)
        )

        website_stats_map, use_case_stats_map, _, _ = self._summarize_ui_tasks(ui_tasks)

        top_website_name = "unknown"
        top_website_score = 0.0
        top_website_tasks = 0
        top_website_entry = max(
            website_stats_map.items(),
            key=lambda item: (
                (item[1]["score_sum"] / item[1]["tasks"]) if item[1]["tasks"] else 0.0
            ),
            default=None,
        )
        if top_website_entry:
            name, values = top_website_entry
            top_website_name = name
            top_website_score = (
                (values["score_sum"] / values["tasks"]) if values["tasks"] else 0.0
            )
            top_website_tasks = int(values["tasks"])
        elif websites:
            top_candidate = max(websites, key=lambda w: w.score, default=None)
            if top_candidate:
                top_website_name = top_candidate.website
                top_website_score = top_candidate.score
                top_website_tasks = top_candidate.tasks

        top_use_case_name = "unknown"
        top_use_case_score = 0.0
        top_use_case_tasks = 0
        top_use_case_entry = max(
            use_case_stats_map.items(),
            key=lambda item: (
                (item[1]["score_sum"] / item[1]["tasks"]) if item[1]["tasks"] else 0.0
            ),
            default=None,
        )
        if top_use_case_entry:
            name, values = top_use_case_entry
            top_use_case_name = name
            top_use_case_score = (
                (values["score_sum"] / values["tasks"]) if values["tasks"] else 0.0
            )
            top_use_case_tasks = int(values["tasks"])
        elif ui_tasks:
            candidate = ui_tasks[0]
            top_use_case_name = candidate.useCase or "unknown"
            top_use_case_score = candidate.score or 0.0
            top_use_case_tasks = 1

        recent_activity = [
            RecentActivity(
                timestamp=_ts_to_iso(context.run.started_at) or "",
                action="Run started",
                details="Agent run initiated",
            )
        ]

        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)

        return Summary(
            runId=context.run.agent_run_id,
            agentId=agent_identifier,
            agentUid=agent_uid,
            agentHotkey=agent_hotkey,
            agentName=agent_name,
            roundId=round_id_value or 0,
            validatorId=_format_validator_id(context.run.validator_uid),
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at),
            status=self._run_status(context),
            overallScore=overall_score,
            totalTasks=total_tasks,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            duration=_safe_int(
                (context.run.ended_at or context.run.started_at or 0)
                - (context.run.started_at or 0)
            ),
            ranking=context.run.rank or 0,
            topPerformingWebsite=TopPerformingWebsite(
                website=top_website_name,
                score=top_website_score,
                tasks=top_website_tasks,
            ),
            topPerformingUseCase=TopPerformingUseCase(
                useCase=top_use_case_name,
                score=top_use_case_score,
                tasks=top_use_case_tasks,
            ),
            recentActivity=recent_activity,
        )

    def _build_run_summary(self, context: AgentRunContext) -> Dict[str, object]:
        run_model = context.run

        total_tasks = (
            getattr(run_model, "n_tasks_total", None)
            or run_model.total_tasks
            or len(context.tasks)
        )

        completed_tasks = (
            getattr(run_model, "n_tasks_completed", None)
            or run_model.completed_tasks
            or 0
        )
        failed_tasks = (
            getattr(run_model, "n_tasks_failed", None) or run_model.failed_tasks or 0
        )

        if completed_tasks == 0 and context.evaluation_results:
            completed_tasks = sum(
                1
                for evaluation in context.evaluation_results
                if evaluation.final_score >= 0.5
            )

        if failed_tasks == 0 and total_tasks:
            failed_tasks = max(total_tasks - completed_tasks, 0)

        average_score = (
            getattr(run_model, "avg_eval_score", None) or run_model.average_score
        )
        if average_score is None:
            average_score = self._compute_average_score(context.evaluation_results)
        average_score = float(average_score or 0.0)

        average_evaluation_time = self._average_evaluation_time(context)

        validator_name, validator_image = self._resolve_validator_identity(context)
        agent_name, _, agent_uid, agent_hotkey, agent_identifier, _ = (
            self._resolve_agent_identity(context)
        )
        success_count = completed_tasks
        success_rate = (success_count / total_tasks * 100.0) if total_tasks else 0.0
        overall_score = _safe_int(average_score * 100)

        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)

        duration_sec = None
        if getattr(run_model, "elapsed_sec", None) not in (None, 0):
            duration_sec = run_model.elapsed_sec
        if duration_sec is None:
            duration_sec = (run_model.ended_at or run_model.started_at or 0) - (
                run_model.started_at or 0
            )

        # Compute unique websites involved in this run only (based on
        # tasks that have a solution and/or evaluation result).
        websites_count = 0
        try:
            relevant_task_ids = set()
            try:
                relevant_task_ids.update(
                    result.task_id for result in (context.evaluation_results or [])
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                relevant_task_ids.update(
                    solution.task_id for solution in (context.task_solutions or [])
                )
            except Exception:  # noqa: BLE001
                pass

            if relevant_task_ids:
                task_by_id = {
                    getattr(t, "task_id", None): t for t in (context.tasks or [])
                }
                hosts = set()
                for task_id in relevant_task_ids:
                    task = task_by_id.get(task_id)
                    if not task:
                        continue
                    website = None
                    if isinstance(getattr(task, "relevant_data", None), dict):
                        website = task.relevant_data.get("website")
                    if not website:
                        website = getattr(task, "url", None)
                    hosts.add(_map_website_port_to_name(website))
                websites_count = len(hosts)
        except Exception:  # noqa: BLE001
            websites_count = 0

        return {
            "runId": run_model.agent_run_id,
            "agentId": agent_identifier,
            "agentUid": agent_uid,
            "agentHotkey": agent_hotkey,
            "agentName": agent_name,
            "roundId": round_id_value or 0,
            "validatorId": _format_validator_id(run_model.validator_uid),
            "validatorName": validator_name,
            "validatorImage": validator_image,
            "status": self._run_status(context).value,
            "startTime": _ts_to_iso(run_model.started_at),
            "endTime": _ts_to_iso(run_model.ended_at),
            "totalTasks": int(total_tasks),
            "completedTasks": int(completed_tasks),
            "successfulTasks": int(success_count),
            "failedTasks": int(failed_tasks),
            "averageScore": average_score,
            "score": average_score,
            "successRate": success_rate,
            "overallScore": overall_score,
            "ranking": run_model.rank or 0,
            "duration": _safe_int(duration_sec),
            # Provide both keys for UI compatibility
            "websitesCount": websites_count,
            "totalWebsites": websites_count,
            "averageEvaluationTime": (
                round(average_evaluation_time, 3)
                if average_evaluation_time is not None
                else None
            ),
        }

    def _sort_runs(
        self, runs: List[Dict[str, object]], sort_by: str, sort_order: str
    ) -> List[Dict[str, object]]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(
                runs, key=lambda item: item.get(sort_by) or 0, reverse=reverse
            )
        except Exception:  # noqa: BLE001
            return runs

    def _build_websites_and_tasks(
        self,
        context: AgentRunContext,
    ) -> Tuple[List[Website], List[UITask], int]:
        host_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"tasks": 0, "successful": 0, "score_sum": 0.0}
        )
        success_count = 0

        evaluation_map, solution_map, task_map = self._index_results(context)

        relevant_task_ids = set(evaluation_map.keys()) | set(solution_map.keys())
        if not relevant_task_ids:
            relevant_task_ids = set(task_map.keys())

        ui_tasks: List[UITask] = []
        for task_id in sorted(relevant_task_ids):
            ui_task = task_map.get(task_id)
            if ui_task is None:
                continue
            evaluation = evaluation_map.get(task_id)
            success = evaluation is not None and evaluation.final_score >= 0.5
            host = _map_website_port_to_name(ui_task.website)

            host_stats_entry = host_stats[host]
            host_stats_entry["tasks"] += 1
            host_stats_entry["score_sum"] += (
                evaluation.final_score if evaluation else 0.0
            )
            if success:
                host_stats_entry["successful"] += 1
                success_count += 1

            ui_tasks.append(ui_task)

        websites = [
            Website(
                website=host,
                tasks=int(stats["tasks"]),
                successful=int(stats["successful"]),
                failed=int(stats["tasks"] - stats["successful"]),
                score=(stats["score_sum"] / stats["tasks"]) if stats["tasks"] else 0.0,
            )
            for host, stats in host_stats.items()
        ]

        return websites, ui_tasks, success_count

    def _index_results(
        self,
        context: AgentRunContext,
    ) -> Tuple[Dict[str, EvaluationResult], Dict[str, TaskSolution], Dict[str, UITask]]:
        evaluation_by_task = {
            result.task_id: result for result in context.evaluation_results
        }
        solution_by_task = {
            solution.task_id: solution for solution in context.task_solutions
        }
        task_map: Dict[str, UITask] = {}

        for task in context.tasks:
            evaluation = evaluation_by_task.get(task.task_id)
            solution = solution_by_task.get(task.task_id)
            task_map[task.task_id] = self._build_ui_task(
                task, solution, evaluation, context.run, context.round
            )

        return evaluation_by_task, solution_by_task, task_map

    def _build_ui_task(
        self,
        task: Task,
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult],
        run,
        round_obj: ValidatorRound,
    ) -> UITask:
        status = (
            TaskStatus.COMPLETED
            if evaluation and evaluation.final_score >= 0.5
            else TaskStatus.FAILED
        )
        score = evaluation.final_score if evaluation else 0.0

        # Use evaluation_time directly from the database
        # This is the time the evaluator took to process the task
        duration = 0.0
        if evaluation and evaluation.evaluation_time:
            duration = float(evaluation.evaluation_time)

        logger.debug(
            f"📊 Task {task.task_id}: duration={duration}s (from evaluation_time)"
        )

        actions = []
        if solution and solution.actions:
            for index, action in enumerate(solution.actions):
                # Normalize action type for display (prefer 'input' over ambiguous 'type')
                raw_type = (
                    action.type
                    if hasattr(action, "type")
                    else action.get("type", "action")
                )
                try:
                    type_key = (
                        str(raw_type)
                        .lower()
                        .replace("action", "")
                        .replace("-", "_")
                        .strip()
                    )
                except Exception:
                    type_key = str(raw_type)
                if type_key in {"type", "type_text", "sendkeysiwa"}:
                    type_key = "input"

                # Extract selector and value, ensuring they are strings
                selector_raw = (
                    getattr(action, "attributes", {}).get("selector")
                    if hasattr(action, "attributes")
                    else action.get("attributes", {}).get("selector")
                )
                value_raw = (
                    getattr(action, "attributes", {}).get("value")
                    if hasattr(action, "attributes")
                    else action.get("attributes", {}).get("value")
                )

                # Convert to strings if they're dicts or other non-string types
                selector_str = None
                if selector_raw is not None:
                    if isinstance(selector_raw, str):
                        selector_str = selector_raw
                    elif isinstance(selector_raw, dict):
                        selector_str = json.dumps(selector_raw)
                    else:
                        selector_str = str(selector_raw)

                value_str = None
                if value_raw is not None:
                    if isinstance(value_raw, str):
                        value_str = value_raw
                    elif isinstance(value_raw, dict):
                        value_str = json.dumps(value_raw)
                    else:
                        value_str = str(value_raw)

                actions.append(
                    Action(
                        id=f"{task.task_id}_action_{index}",
                        type=type_key or "action",
                        selector=selector_str,
                        value=value_str,
                        timestamp=_ts_to_iso(run.started_at) or "",
                        duration=float(getattr(action, "duration", 0.0)),
                        success=bool(getattr(action, "success", True)),
                    )
                )

        website = (
            task.relevant_data.get("website")
            if isinstance(task.relevant_data, dict)
            else None
        )
        if not website:
            website = task.url

        # Normalize website to friendly name
        website = _map_website_port_to_name(website)

        use_case = _extract_use_case(task)

        return UITask(
            taskId=task.task_id,
            roundNumber=round_obj.round_number
            or _round_id_to_int(round_obj.validator_round_id),
            website=website,
            useCase=use_case,
            prompt=task.prompt,
            status=status,
            score=score,
            duration=round(duration, 2),  # Keep as float with 2 decimal places
            startTime=_ts_to_iso(run.started_at) or "",
            endTime=_ts_to_iso(run.ended_at),
            actions=actions,
            screenshots=list(getattr(evaluation, "screenshots", []) or []),
            logs=[],
        )

    @staticmethod
    def _average_evaluation_time(context: AgentRunContext) -> Optional[float]:
        durations: List[float] = []
        for result in context.evaluation_results:
            value = getattr(result, "evaluation_time", None)
            if value is None:
                continue
            try:
                durations.append(abs(float(value)))
            except (TypeError, ValueError):
                continue
        if not durations:
            return None
        return sum(durations) / len(durations)

    @staticmethod
    def _compute_average_score(evaluation_results: List[EvaluationResult]) -> float:
        if not evaluation_results:
            return 0.0
        return sum(result.final_score for result in evaluation_results) / len(
            evaluation_results
        )

    @staticmethod
    def _run_status(context: AgentRunContext) -> RunStatus:
        if context.run.ended_at:
            return RunStatus.COMPLETED
        return RunStatus.RUNNING if context.run.started_at else RunStatus.PENDING

    @staticmethod
    def _find_validator(context: AgentRunContext):
        return next(
            (
                validator
                for validator in context.round.validators
                if validator.uid == context.run.validator_uid
            ),
            None,
        )

    @staticmethod
    def _find_miner(context: AgentRunContext):
        if context.round.miners:
            return next(
                (
                    miner
                    for miner in context.round.miners
                    if miner.uid == context.run.miner_uid
                ),
                None,
            )
        return context.run.miner_info

    def _resolve_agent_identity(
        self,
        context: AgentRunContext,
    ) -> Tuple[str, str, Optional[int], Optional[str], str, str]:
        miner = self._find_miner(context)
        agent_uid = getattr(miner, "uid", None)
        if agent_uid is None:
            agent_uid = context.run.miner_uid

        agent_hotkey = getattr(miner, "hotkey", None) or getattr(
            context.run, "miner_hotkey", None
        )

        agent_name = getattr(miner, "agent_name", None) or getattr(miner, "name", None)
        if not agent_name:
            if agent_hotkey:
                agent_name = agent_hotkey
            elif agent_uid is not None:
                agent_name = f"Agent {agent_uid}"
            else:
                agent_name = "Agent"

        agent_image = resolve_agent_image(miner)
        agent_description = (getattr(miner, "description", "") or "") if miner else ""

        identifier = agent_hotkey or (
            f"agent-{agent_uid}" if agent_uid is not None else context.run.agent_run_id
        )

        return (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            identifier,
            agent_description,
        )

    def _resolve_validator_identity(self, context: AgentRunContext) -> Tuple[str, str]:
        validator = self._find_validator(context)
        validator_uid = context.run.validator_uid

        validator_info = getattr(context.round, "validator_info", None)
        metadata = (
            get_validator_metadata(validator_uid) if validator_uid is not None else {}
        )

        name_candidates = [
            getattr(validator, "name", None) if validator else None,
            getattr(validator_info, "name", None) if validator_info else None,
            metadata.get("name"),
            f"Validator {validator_uid}" if validator_uid is not None else "Validator",
        ]
        validator_name = next(
            (candidate for candidate in name_candidates if candidate), "Validator"
        )

        image_candidates = [
            getattr(validator, "image_url", None) if validator else None,
            getattr(validator_info, "image_url", None) if validator_info else None,
            metadata.get("image"),
        ]
        existing_image = next(
            (candidate for candidate in image_candidates if candidate), None
        )
        validator_image = resolve_validator_image(
            validator_name, existing=existing_image
        )

        return validator_name, validator_image


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _format_validator_id(validator_uid: Optional[int]) -> str:
    return (
        f"validator-{validator_uid}"
        if validator_uid is not None
        else "validator-unknown"
    )


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


def _parse_identifier(identifier: str) -> int:
    if "-" in identifier:
        identifier = identifier.split("-", 1)[1]
    if "_" in identifier:
        identifier = identifier.split("_", 1)[1]
    return int(identifier)


def _to_timestamp(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _extract_use_case(task: Task) -> str:
    if isinstance(task.use_case, dict):
        return task.use_case.get("name", "unknown")
    if isinstance(task.use_case, str):
        return task.use_case
    return "unknown"

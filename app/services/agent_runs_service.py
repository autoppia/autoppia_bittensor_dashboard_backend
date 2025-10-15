from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.rounds_service import AgentRunContext, RoundsService

logger = logging.getLogger(__name__)


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


def _safe_int(value: Optional[float]) -> int:
    if value is None:
        return 0
    return int(round(value))


class AgentRunsService:
    """SQL-backed business logic for agent evaluation runs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def list_agent_runs(
        self,
        page: int = 1,
        limit: int = 20,
        validator_round_id: Optional[int] = None,
        validator_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc",
    ) -> Dict[str, object]:
        skip = (page - 1) * limit
        round_filter = (
            f"round_{validator_round_id:03d}"
            if validator_round_id is not None
            else None
        )

        contexts = await self.rounds_service.list_agent_run_contexts(
            validator_round_id=round_filter,
            limit=limit,
            skip=skip,
        )

        status_filter = status.lower() if status else None
        start_ts = _to_timestamp(start_date)
        end_ts = _to_timestamp(end_date)
        query_lower = query.lower() if query else None

        runs = []
        for context in contexts:
            if validator_id:
                validator_uid = _parse_identifier(validator_id)
                if context.run.validator_uid != validator_uid:
                    continue
            if agent_id:
                miner_uid = _parse_identifier(agent_id)
                if context.run.miner_uid != miner_uid:
                    continue

            if status_filter:
                if self._run_status(context).value != status_filter:
                    continue

            run_start = context.run.started_at or context.round.started_at
            if start_ts is not None and (run_start or 0) < start_ts:
                continue
            if end_ts is not None and (run_start or 0) > end_ts:
                continue

            if query_lower:
                agent_identifier = _format_agent_id(context.run.miner_uid).lower()
                validator_identifier = _format_validator_id(context.run.validator_uid).lower()
                if (
                    query_lower not in context.run.agent_run_id.lower()
                    and query_lower not in agent_identifier
                    and query_lower not in validator_identifier
                ):
                    continue

            run_summary = self._build_run_summary(context)
            runs.append(run_summary)

        runs = self._sort_runs(runs, sort_by, sort_order)

        return {
            "runs": runs,
            "total": len(runs),
            "page": page,
            "limit": limit,
        }

    async def get_agent_run(self, agent_run_id: str) -> Optional[AgentRun]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_agent_run(context)

    async def get_personas(self, agent_run_id: str) -> Optional[Personas]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_personas(context)

    async def get_statistics(self, agent_run_id: str) -> Optional[Statistics]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_statistics(context)

    async def get_summary(self, agent_run_id: str) -> Optional[Summary]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_summary(context)

    async def get_tasks(self, agent_run_id: str) -> Optional[List[UITask]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        _, _, task_map = self._index_results(context)
        return list(task_map.values())

    async def get_timeline(self, agent_run_id: str) -> Optional[List[Event]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        events: List[Event] = []
        start_time = _ts_to_iso(context.run.started_at) or datetime.now(timezone.utc).isoformat()
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
            (context.run.ended_at or context.run.started_at or 0) - (context.run.started_at or 0)
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
                run.successfulTasks / run.totalTasks * 100.0
                if run.totalTasks
                else 0.0
            )

        best_score_run = max(runs, key=lambda run: run.score if run.score is not None else 0.0)
        fastest_run = min(runs, key=lambda run: run.duration if run.duration is not None else float("inf"))
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

        validator = self._find_validator(context)
        validator_name = validator.name if validator and validator.name else f"Validator {context.run.validator_uid}"
        validator_image = validator.coldkey if validator and validator.coldkey else ""

        return AgentRun(
            runId=context.run.agent_run_id,
            agentId=_format_agent_id(context.run.miner_uid),
            roundId=_round_id_to_int(context.round.validator_round_id),
            validatorId=_format_validator_id(context.run.validator_uid),
            validatorName=validator_name,
            validatorImage=validator_image,
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at),
            status=self._run_status(context),
            totalTasks=total_tasks,
            completedTasks=success_count,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            score=average_score,
            ranking=context.run.rank or 0,
            duration=_safe_int((context.run.ended_at or context.run.started_at or 0) - (context.run.started_at or 0)),
            overallScore=overall_score,
            websites=websites,
            tasks=ui_tasks,
            metadata=context.run.metadata or {},
        )

    def _build_personas(self, context: AgentRunContext) -> Personas:
        validator = self._find_validator(context)
        miner = self._find_miner(context)

        round_info = RoundInfo(
            id=_round_id_to_int(context.round.validator_round_id),
            name=context.round.validator_round_id,
            status=context.round.status,
            startTime=_ts_to_iso(context.round.started_at) or "",
            endTime=_ts_to_iso(context.round.ended_at),
        )

        validator_info = ValidatorInfo(
            id=_format_validator_id(context.run.validator_uid),
            name=validator.name if validator and validator.name else f"Validator {context.run.validator_uid}",
            image=validator.coldkey if validator and validator.coldkey else "",
            description="",
            website="",
            github="",
        )

        agent_info = AgentInfo(
            id=_format_agent_id(context.run.miner_uid),
            name=miner.agent_name if miner and miner.agent_name else f"Agent {context.run.miner_uid}",
            type="sota" if context.run.is_sota else "miner",
            image=miner.agent_image or "" if miner else "",
            description=(miner.description or "") if miner else "",
        )

        return Personas(round=round_info, validator=validator_info, agent=agent_info)

    def _build_statistics(self, context: AgentRunContext) -> Statistics:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(self._compute_average_score(context.evaluation_results) * 100)

        performance_by_website = [
            PerformanceByWebsite(
                website=website.website,
                tasks=website.tasks,
                successful=website.successful,
                failed=website.failed,
                averageScore=website.score,
                averageDuration=0.0,
            )
            for website in websites
        ]

        use_case_counts: Dict[str, Tuple[int, int]] = defaultdict(lambda: (0, 0))
        for task in context.tasks:
            use_case_name = _extract_use_case(task)
            evaluations = [
                eval_result for eval_result in context.evaluation_results if eval_result.task_id == task.task_id
            ]
            successes = len([er for er in evaluations if er.final_score >= 0.5])
            total = len(evaluations)
            prev_total, prev_success = use_case_counts[use_case_name]
            use_case_counts[use_case_name] = (prev_total + total, prev_success + successes)

        performance_by_use_case = [
            PerformanceByUseCase(
                useCase=use_case,
                tasks=totals,
                successful=successes,
                failed=max(totals - successes, 0),
                averageScore=(successes / totals) if totals else 0.0,
                averageDuration=0.0,
            )
            for use_case, (totals, successes) in use_case_counts.items()
        ]

        excellent = len([er for er in context.evaluation_results if er.final_score >= 0.9])
        good = len([er for er in context.evaluation_results if 0.7 <= er.final_score < 0.9])
        average = len([er for er in context.evaluation_results if 0.5 <= er.final_score < 0.7])
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
            websites=len(websites),
            averageTaskDuration=0.0,
            successRate=(success_count / total_tasks * 100) if total_tasks else 0.0,
            scoreDistribution=score_distribution,
            performanceByWebsite=performance_by_website,
            performanceByUseCase=performance_by_use_case,
        )

    def _build_summary(self, context: AgentRunContext) -> Summary:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(self._compute_average_score(context.evaluation_results) * 100)

        top_website = max(websites, key=lambda w: w.score, default=None)
        top_use_case = max(
            [_extract_use_case(task) for task in context.tasks],
            default="unknown",
        )
        top_use_case_score = 100 if success_count else 0

        recent_activity = [
            RecentActivity(
                timestamp=_ts_to_iso(context.run.started_at) or "",
                action="Run started",
                details="Agent run initiated",
            )
        ]

        return Summary(
            runId=context.run.agent_run_id,
            agentId=_format_agent_id(context.run.miner_uid),
            roundId=_round_id_to_int(context.round.validator_round_id),
            validatorId=_format_validator_id(context.run.validator_uid),
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at),
            status=self._run_status(context),
            overallScore=overall_score,
            totalTasks=total_tasks,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            duration=_safe_int((context.run.ended_at or context.run.started_at or 0) - (context.run.started_at or 0)),
            ranking=context.run.rank or 0,
            topPerformingWebsite=TopPerformingWebsite(
                website=top_website.website if top_website else "unknown",
                score=top_website.score if top_website else 0.0,
                tasks=top_website.tasks if top_website else 0,
            ),
            topPerformingUseCase=TopPerformingUseCase(
                useCase=top_use_case,
                score=top_use_case_score,
                tasks=total_tasks,
            ),
            recentActivity=recent_activity,
        )

    def _build_run_summary(self, context: AgentRunContext) -> Dict[str, object]:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        average_score = self._compute_average_score(context.evaluation_results)

        return {
            "runId": context.run.agent_run_id,
            "agentId": _format_agent_id(context.run.miner_uid),
            "roundId": _round_id_to_int(context.round.validator_round_id),
            "validatorId": _format_validator_id(context.run.validator_uid),
            "status": self._run_status(context).value,
            "startTime": _ts_to_iso(context.run.started_at),
            "endTime": _ts_to_iso(context.run.ended_at),
            "totalTasks": total_tasks,
            "completedTasks": success_count,
            "averageScore": average_score,
            "successRate": (success_count / total_tasks * 100) if total_tasks else 0.0,
            "overallScore": _safe_int(average_score * 100),
            "ranking": context.run.rank or 0,
            "duration": _safe_int((context.run.ended_at or context.run.started_at or 0) - (context.run.started_at or 0)),
        }

    def _sort_runs(self, runs: List[Dict[str, object]], sort_by: str, sort_order: str) -> List[Dict[str, object]]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(runs, key=lambda item: item.get(sort_by) or 0, reverse=reverse)
        except Exception:  # noqa: BLE001
            return runs

    def _build_websites_and_tasks(
        self,
        context: AgentRunContext,
    ) -> Tuple[List[Website], List[UITask], int]:
        host_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tasks": 0, "successful": 0, "score_sum": 0.0})
        success_count = 0

        evaluation_map, solution_map, task_map = self._index_results(context)

        ui_tasks: List[UITask] = []
        for task_id, ui_task in task_map.items():
            evaluation = evaluation_map.get(task_id)
            success = evaluation is not None and evaluation.final_score >= 0.5
            host = _extract_host(ui_task.website)

            host_stats_entry = host_stats[host]
            host_stats_entry["tasks"] += 1
            host_stats_entry["score_sum"] += evaluation.final_score if evaluation else 0.0
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
        evaluation_by_task = {result.task_id: result for result in context.evaluation_results}
        solution_by_task = {solution.task_id: solution for solution in context.task_solutions}
        task_map: Dict[str, UITask] = {}

        for task in context.tasks:
            evaluation = evaluation_by_task.get(task.task_id)
            solution = solution_by_task.get(task.task_id)
            task_map[task.task_id] = self._build_ui_task(task, solution, evaluation, context.run)

        return evaluation_by_task, solution_by_task, task_map

    def _build_ui_task(
        self,
        task: Task,
        solution: Optional[TaskSolution],
        evaluation: Optional[EvaluationResult],
        run,
    ) -> UITask:
        status = TaskStatus.COMPLETED if evaluation and evaluation.final_score >= 0.5 else TaskStatus.FAILED
        score = evaluation.final_score if evaluation else 0.0
        duration = evaluation.evaluation_time if evaluation else 0.0

        actions = []
        if solution and solution.actions:
            for index, action in enumerate(solution.actions):
                actions.append(
                    Action(
                        id=f"{task.task_id}_action_{index}",
                        type=action.type if hasattr(action, "type") else action.get("type", "action"),
                        selector=getattr(action, "attributes", {}).get("selector")
                        if hasattr(action, "attributes")
                        else action.get("attributes", {}).get("selector"),
                        value=getattr(action, "attributes", {}).get("value")
                        if hasattr(action, "attributes")
                        else action.get("attributes", {}).get("value"),
                        timestamp=_ts_to_iso(run.started_at) or "",
                        duration=float(getattr(action, "duration", 0.0)),
                        success=bool(getattr(action, "success", True)),
                    )
                )

        website = task.relevant_data.get("website") if isinstance(task.relevant_data, dict) else None
        if not website:
            website = task.url

        use_case = _extract_use_case(task)

        return UITask(
            taskId=task.task_id,
            website=website,
            useCase=use_case,
            prompt=task.prompt,
            status=status,
            score=score,
            duration=_safe_int(duration),
            startTime=_ts_to_iso(run.started_at) or "",
            endTime=_ts_to_iso(run.ended_at),
            actions=actions,
            screenshots=list(getattr(evaluation, "screenshots", []) or []),
            logs=[],
        )

    @staticmethod
    def _compute_average_score(evaluation_results: List[EvaluationResult]) -> float:
        if not evaluation_results:
            return 0.0
        return sum(result.final_score for result in evaluation_results) / len(evaluation_results)

    @staticmethod
    def _run_status(context: AgentRunContext) -> RunStatus:
        if context.run.ended_at:
            return RunStatus.COMPLETED
        return RunStatus.RUNNING if context.run.started_at else RunStatus.PENDING

    @staticmethod
    def _find_validator(context: AgentRunContext):
        return next((validator for validator in context.round.validators if validator.uid == context.run.validator_uid), None)

    @staticmethod
    def _find_miner(context: AgentRunContext):
        if context.round.miners:
            return next((miner for miner in context.round.miners if miner.uid == context.run.miner_uid), None)
        return context.run.miner_info


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _format_validator_id(validator_uid: Optional[int]) -> str:
    return f"validator-{validator_uid}" if validator_uid is not None else "validator-unknown"


def _round_id_to_int(round_id: str) -> int:
    if not round_id or "_" not in round_id:
        return 0
    try:
        return int(round_id.split("_")[1])
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

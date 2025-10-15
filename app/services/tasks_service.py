from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)
from app.models.core import (
    AgentEvaluationRun,
    EvaluationResult,
    Round,
    Task,
    TaskSolution,
)
from app.models.ui.tasks import (
    AgentInfo,
    CompareTasksResponse,
    PersonasData,
    RecentActivity,
    RoundInfo,
    Task as UITask,
    TaskAction,
    TaskAnalytics,
    TaskDetails,
    TaskInfo,
    TaskLog,
    TaskMetadata,
    TaskPerformance,
    TaskResults,
    TaskSummary,
    TaskScreenshot,
    TaskStatistics,
    TaskTimeline,
    TaskStatus,
    LogLevel,
    UseCasePerformance,
    ValidatorInfo,
    WebsitePerformance,
)

logger = logging.getLogger(__name__)


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _safe_int(value: Optional[float]) -> int:
    if value is None:
        return 0
    return int(round(value))


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


def _round_id_to_int(round_id: str) -> int:
    if "_" in round_id:
        _, suffix = round_id.split("_", 1)
        try:
            return int(suffix)
        except ValueError:
            return 0
    return 0


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _format_validator_id(validator_uid: Optional[int]) -> str:
    return f"validator-{validator_uid}" if validator_uid is not None else "validator-unknown"


@dataclass
class TaskContext:
    round: Round
    agent_run: AgentEvaluationRun
    task: Task
    solution: Optional[TaskSolution]
    evaluation: Optional[EvaluationResult]


class TasksService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tasks(
        self,
        page: int,
        limit: int,
        agent_run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        validator_id: Optional[str] = None,
        website: Optional[str] = None,
        use_case: Optional[str] = None,
        status: Optional[str] = None,
        query: Optional[str] = None,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc",
        include_facets: bool = False,
    ) -> Dict[str, object]:
        stmt = (
            select(TaskORM)
            .options(
                selectinload(TaskORM.agent_run)
                .selectinload(AgentEvaluationRunORM.round),
                selectinload(TaskORM.task_solutions),
                selectinload(TaskORM.evaluation_results),
            )
            .order_by(TaskORM.id.desc())
        )

        if agent_run_id:
            stmt = stmt.where(TaskORM.agent_run_id == agent_run_id)

        task_rows = await self.session.scalars(stmt)

        query_lower = query.lower() if query else None
        start_ts = _to_timestamp(start_date)
        end_ts = _to_timestamp(end_date)

        website_counts: Dict[str, int] = defaultdict(int)
        use_case_counts: Dict[str, int] = defaultdict(int)
        status_counts: Dict[str, int] = defaultdict(int)
        score_buckets: Dict[str, int] = defaultdict(int)

        score_ranges = [
            ("0.0-0.25", 0.0, 0.25),
            ("0.25-0.5", 0.25, 0.5),
            ("0.5-0.75", 0.5, 0.75),
            ("0.75-1.0", 0.75, 1.01),
        ]

        items: List[UITask] = []
        for task_row in task_rows:
            try:
                context = self._build_context(task_row)
            except ValueError as exc:
                logger.warning(str(exc))
                continue

            if agent_id:
                miner_uid = _parse_identifier(agent_id)
                if context.agent_run.miner_uid != miner_uid:
                    continue

            if validator_id:
                validator_uid = _parse_identifier(validator_id)
                if context.agent_run.validator_uid != validator_uid:
                    continue

            if website and context.task.url != website:
                continue

            if use_case:
                use_case_name = self._extract_use_case(context.task)
                if use_case_name != use_case:
                    continue

            ui_task = self._build_ui_task(context)
            evaluation_score = context.evaluation.final_score if context.evaluation else 0.0
            run_start_ts = context.agent_run.started_at or context.round.started_at or 0.0

            if status and ui_task.status.value.lower() != status.lower():
                continue

            if min_score is not None and evaluation_score < min_score:
                continue
            if max_score is not None and evaluation_score > max_score:
                continue

            if start_ts is not None and run_start_ts < start_ts:
                continue
            if end_ts is not None and run_start_ts > end_ts:
                continue

            if query_lower:
                prompt = context.task.prompt or ""
                url = context.task.url or ""
                if (
                    query_lower not in prompt.lower()
                    and query_lower not in url.lower()
                    and query_lower not in ui_task.taskId.lower()
                    and query_lower not in ui_task.agentRunId.lower()
                ):
                    continue

            items.append(ui_task)

            if include_facets:
                website_counts[ui_task.website] += 1
                use_case_counts[ui_task.useCase] += 1
                status_counts[ui_task.status.value] += 1
                for name, lower, upper in score_ranges:
                    if lower <= evaluation_score < upper:
                        score_buckets[name] += 1
                        break

        items = self._sort_tasks(items, sort_by, sort_order)

        total = len(items)
        start_index = (page - 1) * limit
        end_index = start_index + limit
        paginated = items[start_index:end_index]

        result: Dict[str, Any] = {
            "tasks": [task.model_dump() for task in paginated],
            "total": total,
            "page": page,
            "limit": limit,
        }

        if include_facets:
            result["facets"] = {
                "websites": [
                    {"name": name, "count": count}
                    for name, count in sorted(website_counts.items(), key=lambda item: item[1], reverse=True)
                ],
                "useCases": [
                    {"name": name, "count": count}
                    for name, count in sorted(use_case_counts.items(), key=lambda item: item[1], reverse=True)
                ],
                "statuses": [
                    {"name": name, "count": count}
                    for name, count in sorted(status_counts.items(), key=lambda item: item[1], reverse=True)
                ],
                "scoreRanges": [
                    {"name": name, "count": score_buckets.get(name, 0)}
                    for name, _, _ in score_ranges
                ],
            }

        return result

    async def search_tasks(
        self,
        page: int,
        limit: int,
        **filters: Any,
    ) -> Dict[str, object]:
        return await self.list_tasks(
            page=page,
            limit=limit,
            include_facets=True,
            **filters,
        )

    async def get_task(self, task_id: str) -> TaskContext:
        stmt = (
            select(TaskORM)
            .options(
                selectinload(TaskORM.agent_run)
                .selectinload(AgentEvaluationRunORM.round),
                selectinload(TaskORM.task_solutions),
                selectinload(TaskORM.evaluation_results),
            )
            .where(TaskORM.task_id == task_id)
        )
        task_row = await self.session.scalar(stmt)
        if not task_row:
            raise ValueError(f"Task {task_id} not found")
        return self._build_context(task_row)

    async def analytics(self) -> TaskAnalytics:
        stmt = (
            select(TaskORM)
            .options(
                selectinload(TaskORM.agent_run)
                .selectinload(AgentEvaluationRunORM.round),
                selectinload(TaskORM.task_solutions),
                selectinload(TaskORM.evaluation_results),
            )
        )
        rows = await self.session.scalars(stmt)

        contexts: List[TaskContext] = []
        for row in rows:
            try:
                contexts.append(self._build_context(row))
            except ValueError:
                continue

        total = len(contexts)
        completed = len([ctx for ctx in contexts if ctx.evaluation and ctx.evaluation.final_score >= 0.5])
        failed = len([ctx for ctx in contexts if ctx.evaluation and ctx.evaluation.final_score < 0.5])

        scores = [ctx.evaluation.final_score for ctx in contexts if ctx.evaluation]
        durations = [ctx.evaluation.evaluation_time for ctx in contexts if ctx.evaluation]

        average_score = sum(scores) / len(scores) if scores else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        success_rate = (completed / total * 100.0) if total else 0.0

        website_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tasks": 0, "successful": 0, "failed": 0, "score": 0.0, "duration": 0.0})
        use_case_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tasks": 0, "successful": 0, "failed": 0, "score": 0.0, "duration": 0.0})

        performance_over_time: List[Dict[str, Any]] = []
        for context in contexts:
            evaluation_score = context.evaluation.final_score if context.evaluation else 0.0
            evaluation_duration = context.evaluation.evaluation_time if context.evaluation else 0.0
            completed_flag = evaluation_score >= 0.5

            website = context.task.url
            stats = website_stats[website]
            stats["tasks"] += 1
            stats["score"] += evaluation_score
            stats["duration"] += evaluation_duration
            if completed_flag:
                stats["successful"] += 1
            else:
                stats["failed"] += 1

            use_case = self._extract_use_case(context.task)
            use_stats = use_case_stats[use_case]
            use_stats["tasks"] += 1
            use_stats["score"] += evaluation_score
            use_stats["duration"] += evaluation_duration
            if completed_flag:
                use_stats["successful"] += 1
            else:
                use_stats["failed"] += 1

            start_ts = context.agent_run.started_at or context.round.started_at or 0.0
            performance_over_time.append(
                {
                    "timestamp": _ts_to_iso(start_ts),
                    "score": evaluation_score,
                }
            )

        performance_by_website = [
            WebsitePerformance(
                website=website,
                tasks=int(values["tasks"]),
                successful=int(values["successful"]),
                failed=int(values["failed"]),
                averageScore=(values["score"] / values["tasks"]) if values["tasks"] else 0.0,
                averageDuration=(values["duration"] / values["tasks"]) if values["tasks"] else 0.0,
            )
            for website, values in website_stats.items()
        ]

        performance_by_use_case = [
            UseCasePerformance(
                useCase=use_case,
                tasks=int(values["tasks"]),
                successful=int(values["successful"]),
                failed=int(values["failed"]),
                averageScore=(values["score"] / values["tasks"]) if values["tasks"] else 0.0,
                averageDuration=(values["duration"] / values["tasks"]) if values["tasks"] else 0.0,
            )
            for use_case, values in use_case_stats.items()
        ]

        performance_over_time.sort(key=lambda item: item["timestamp"] or "")

        return TaskAnalytics(
            totalTasks=total,
            completedTasks=completed,
            failedTasks=failed,
            averageScore=average_score,
            averageDuration=average_duration,
            successRate=success_rate,
            performanceByWebsite=performance_by_website,
            performanceByUseCase=performance_by_use_case,
            performanceOverTime=performance_over_time,
        )

    async def compare_tasks(self, task_ids: List[str]) -> CompareTasksResponse:
        contexts: List[TaskContext] = []
        for task_id in task_ids:
            try:
                context = await self.get_task(task_id)
            except ValueError:
                continue
            contexts.append(context)

        compared_tasks = [self._build_ui_task(ctx) for ctx in contexts]
        best = max(compared_tasks, key=lambda t: t.score, default=None)
        fastest = min(compared_tasks, key=lambda t: t.duration, default=None)
        most_actions = max(compared_tasks, key=lambda t: len(t.actions or []), default=None)
        best_success = max(
            compared_tasks,
            key=lambda t: t.successRate,
            default=None,
        )

        comparison = {
            "bestScore": best.taskId if best else "",
            "fastest": fastest.taskId if fastest else "",
            "mostActions": most_actions.taskId if most_actions else "",
            "bestSuccessRate": best_success.taskId if best_success else "",
        }

        return CompareTasksResponse(tasks=compared_tasks, comparison=comparison)

    def build_task_detail(self, context: TaskContext) -> TaskDetails:
        task = self._build_ui_task(context)
        performance = TaskPerformance(
            totalActions=len(task.actions or []),
            successfulActions=len([a for a in task.actions or [] if a.success]),
            failedActions=len([a for a in task.actions or [] if not a.success]),
            averageActionDuration=self._average_action_duration(task.actions),
            totalWaitTime=0.0,
            totalNavigationTime=0.0,
        )

        metadata = TaskMetadata(
            environment="production",
            browser="chrome",
            viewport={"width": 1920, "height": 1080},
            userAgent="Auto-generated",
            resources={
                "cpu": 1.0,
                "memory": 512,
                "network": 100,
            },
        )

        task_payload = task.model_dump()
        task_payload["performance"] = performance
        task_payload["metadata"] = metadata
        return TaskDetails(**task_payload)

    def build_personas(self, context: TaskContext) -> PersonasData:
        round_info = RoundInfo(
            id=_round_id_to_int(context.round.validator_round_id),
            name=context.round.validator_round_id,
            status=context.round.status,
            startTime=_parse_iso(context.round.started_at),
            endTime=_parse_iso(context.round.ended_at),
        )

        validator = None
        if context.round.validators:
            validator = next(
                (val for val in context.round.validators if val.uid == context.agent_run.validator_uid),
                context.round.validators[0],
            )

        validator_info = ValidatorInfo(
            id=_format_validator_id(context.agent_run.validator_uid),
            name=validator.name if validator and validator.name else _format_validator_id(context.agent_run.validator_uid),
            image="https://placehold.co/64x64?text=V",
            description="",
            website="",
            github="",
        )

        miner = context.agent_run.miner_info
        agent_info = AgentInfo(
            id=_format_agent_id(context.agent_run.miner_uid),
            name=miner.agent_name if miner and miner.agent_name else _format_agent_id(context.agent_run.miner_uid),
            type="sota" if context.agent_run.is_sota else "miner",
            image=miner.agent_image if miner and miner.agent_image else "",
            description=miner.description if miner and miner.description else "",
        )

        evaluation_score = context.evaluation.final_score if context.evaluation else 0.0
        task_status = TaskStatus.COMPLETED if evaluation_score >= 0.5 else TaskStatus.FAILED

        task_info = TaskInfo(
            id=context.task.task_id,
            website=context.task.url,
            useCase=self._extract_use_case(context.task),
            status=task_status,
            score=evaluation_score,
        )

        return PersonasData(round=round_info, validator=validator_info, agent=agent_info, task=task_info)

    def build_task_statistics(self, context: TaskContext) -> TaskStatistics:
        evaluation_score = context.evaluation.final_score if context.evaluation else 0.0
        duration = context.evaluation.evaluation_time if context.evaluation else 0.0
        completed = 1 if evaluation_score >= 0.5 else 0
        failed = 1 - completed if context.evaluation else 0
        running = 0 if context.evaluation else 1

        website = context.task.url
        use_case = self._extract_use_case(context.task)

        website_performance = [
            WebsitePerformance(
                website=website,
                tasks=1,
                successful=completed,
                failed=failed,
                averageScore=evaluation_score,
                averageDuration=duration,
            )
        ]

        use_case_performance = [
            UseCasePerformance(
                useCase=use_case,
                tasks=1,
                successful=completed,
                failed=failed,
                averageScore=evaluation_score,
                averageDuration=duration,
            )
        ]

        base_ts = context.agent_run.started_at or context.round.started_at or 0.0
        recent_activity: List[RecentActivity] = [
            RecentActivity(
                timestamp=_parse_iso(base_ts),
                action="task_started",
                details=f"Task {context.task.task_id} started",
            )
        ]
        if context.evaluation:
            completion_ts = context.agent_run.ended_at or base_ts
            recent_activity.append(
                RecentActivity(
                    timestamp=_parse_iso(completion_ts),
                    action="task_completed" if completed else "task_failed",
                    details=f"Task {context.task.task_id} completed",
                )
            )

        return TaskStatistics(
            totalTasks=1,
            completedTasks=completed,
            failedTasks=failed,
            runningTasks=running,
            averageScore=evaluation_score,
            averageDuration=duration,
            successRate=completed * 100.0,
            performanceByWebsite=website_performance,
            performanceByUseCase=use_case_performance,
            recentActivity=recent_activity,
        )

    def build_task_results(self, context: TaskContext) -> TaskResults:
        action_models = self.build_actions(context)

        total_actions = len(action_models)
        successful_actions = len([action for action in action_models if action.success])
        failed_actions = total_actions - successful_actions
        action_type_counts: Dict[str, int] = defaultdict(int)
        for action in action_models:
            action_type_counts[action.type] += 1

        summary = TaskSummary(
            totalActions=total_actions,
            successfulActions=successful_actions,
            failedActions=failed_actions,
            actionTypes=dict(action_type_counts),
        )

        return TaskResults(
            taskId=context.task.task_id,
            status="completed"
            if context.evaluation and context.evaluation.final_score >= 0.5
            else "failed",
            score=context.evaluation.final_score if context.evaluation else 0.0,
            duration=_safe_int(getattr(context.evaluation, "evaluation_time", 0.0)),
            actions=action_models,
            screenshots=self.build_screenshots(context),
            logs=[],
            summary=summary,
            timeline=[],
        )

    def build_actions(self, context: TaskContext) -> List[TaskAction]:
        actions: List[TaskAction] = []
        if not context.solution or not context.solution.actions:
            return actions

        for index, action in enumerate(context.solution.actions):
            attributes = getattr(action, "attributes", {}) if hasattr(action, "attributes") else action.get("attributes", {})
            actions.append(
                TaskAction(
                    id=str(getattr(action, "id", index)),
                    type=getattr(action, "type", "action"),
                    selector=attributes.get("selector"),
                    value=attributes.get("value"),
                    timestamp=self._action_timestamp(context, index),
                    duration=float(getattr(action, "duration", 0.0)),
                    success=bool(getattr(action, "success", True)),
                )
            )
        return actions

    def build_screenshots(self, context: TaskContext) -> List[TaskScreenshot]:
        screenshots: List[TaskScreenshot] = []
        base_ts = context.agent_run.started_at or context.round.started_at or 0.0
        timestamp = _parse_iso(base_ts)

        if getattr(context.task, "screenshot", None):
            screenshots.append(
                TaskScreenshot(
                    id=f"{context.task.task_id}_screenshot",
                    url=context.task.screenshot,
                    timestamp=timestamp,
                    actionId=None,
                    description=context.task.screenshot_description,
                )
            )

        if context.evaluation and getattr(context.evaluation, "gif_recording", None):
            screenshots.append(
                TaskScreenshot(
                    id=f"{context.task.task_id}_recording",
                    url=context.evaluation.gif_recording,
                    timestamp=timestamp,
                    actionId=None,
                    description="Evaluation recording",
                )
            )

        return screenshots

    def build_logs(self, context: TaskContext) -> List[TaskLog]:
        logs: List[TaskLog] = []
        if context.evaluation and context.evaluation.execution_history:
            base_ts = context.agent_run.started_at or context.round.started_at or 0.0
            for index, entry in enumerate(context.evaluation.execution_history):
                logs.append(
                    TaskLog(
                        timestamp=datetime.fromtimestamp(base_ts + index, tz=timezone.utc),
                        level=LogLevel.INFO,
                        message=str(entry),
                        metadata={"taskId": context.task.task_id},
                    )
                )
        return logs

    def build_timeline(self, context: TaskContext) -> List[TaskTimeline]:
        timeline: List[TaskTimeline] = []
        if context.solution and context.solution.actions:
            base_ts = context.agent_run.started_at or context.round.started_at or 0.0
            for index, action in enumerate(context.solution.actions):
                timeline.append(
                    TaskTimeline(
                        timestamp=datetime.fromtimestamp(base_ts + index, tz=timezone.utc),
                        action=getattr(action, "type", "action"),
                        duration=float(getattr(action, "duration", 0.0)),
                        success=bool(getattr(action, "success", True)),
                        metadata={},
                    )
                )
        return timeline

    def build_metrics(self, context: TaskContext) -> Dict[str, object]:
        return {
            "duration": _safe_int(getattr(context.evaluation, "evaluation_time", 0.0)),
            "actionsPerSecond": 0.0,
            "averageActionDuration": self._average_action_duration(
                self._build_ui_task(context).actions
            ),
            "totalWaitTime": 0.0,
            "totalNavigationTime": 0.0,
            "memoryUsage": [],
            "cpuUsage": [],
        }

    @staticmethod
    def _action_timestamp(context: TaskContext, offset: int) -> datetime:
        base_ts = context.agent_run.started_at or context.round.started_at or 0.0
        return datetime.fromtimestamp(base_ts + offset, tz=timezone.utc)

    def _build_context(self, task_row: TaskORM) -> TaskContext:
        agent_run_row = task_row.agent_run
        if agent_run_row is None:
            raise ValueError(f"Task {task_row.task_id} missing agent run relationship")
        round_row = agent_run_row.round
        if round_row is None:
            raise ValueError(
                f"Agent run {agent_run_row.agent_run_id} missing round relationship"
            )

        round_model = self._deserialize_round(round_row)
        agent_run_model = self._deserialize_agent_run(agent_run_row)
        task_model = self._deserialize_task(task_row)

        solution_model = None
        if task_row.task_solutions:
            solution_model = self._deserialize_task_solution(task_row.task_solutions[0])

        evaluation_model = None
        if task_row.evaluation_results:
            evaluation_model = self._deserialize_evaluation(task_row.evaluation_results[0])

        return TaskContext(
            round=round_model,
            agent_run=agent_run_model,
            task=task_model,
            solution=solution_model,
            evaluation=evaluation_model,
        )

    def _build_ui_task(self, context: TaskContext) -> UITask:
        evaluation = context.evaluation
        score = evaluation.final_score if evaluation else 0.0
        status = TaskStatus.COMPLETED if score >= 0.5 else TaskStatus.FAILED
        success_rate = int(score * 100)

        actions = []
        if context.solution and context.solution.actions:
            for index, action in enumerate(context.solution.actions):
                actions.append(
                    TaskAction(
                        id=str(getattr(action, "id", index)),
                        type=getattr(action, "type", "action"),
                        selector=None,
                        value=None,
                        timestamp=self._action_timestamp(context, index),
                        duration=float(getattr(action, "duration", 0.0)),
                        success=bool(getattr(action, "success", True)),
                    )
                )

        start_time = context.agent_run.started_at or context.round.started_at
        end_time = context.agent_run.ended_at or start_time

        return UITask(
            taskId=context.task.task_id,
            agentRunId=context.agent_run.agent_run_id,
            website=context.task.url,
            useCase=self._extract_use_case(context.task) or "unknown",
            prompt=context.task.prompt,
            status=status,
            score=score,
            successRate=success_rate,
            duration=_safe_int(getattr(evaluation, "evaluation_time", 0.0)),
            startTime=_parse_iso(start_time),
            endTime=_parse_iso(end_time),
            createdAt=_parse_iso(start_time),
            updatedAt=_parse_iso(end_time),
            actions=actions,
            screenshots=[],
            logs=[],
            metadata=None,
        )

    @staticmethod
    def _average_action_duration(actions: Optional[List[TaskAction]]) -> float:
        if not actions:
            return 0.0
        durations = [action.duration for action in actions]
        return sum(durations) / len(durations)

    @staticmethod
    def _extract_use_case(task: Task) -> Optional[str]:
        if isinstance(task.use_case, dict):
            return task.use_case.get("name")
        if isinstance(task.use_case, str):
            return task.use_case
        return None

    @staticmethod
    def _deserialize_round(round_row: RoundORM) -> Round:
        data = dict(round_row.data or {})
        data.setdefault("validator_round_id", round_row.validator_round_id)
        return Round(**data)

    @staticmethod
    def _deserialize_agent_run(run_row: AgentEvaluationRunORM) -> AgentEvaluationRun:
        data = dict(run_row.data or {})
        data.setdefault("agent_run_id", run_row.agent_run_id)
        data.setdefault("validator_round_id", run_row.validator_round_id)
        data.setdefault("validator_uid", run_row.validator_uid)
        data.setdefault("miner_uid", run_row.miner_uid)
        data.setdefault("is_sota", run_row.is_sota)
        return AgentEvaluationRun(**data)

    @staticmethod
    def _deserialize_task(task_row: TaskORM) -> Task:
        data = dict(task_row.data or {})
        data.setdefault("task_id", task_row.task_id)
        data.setdefault("validator_round_id", task_row.validator_round_id)
        data.setdefault("agent_run_id", task_row.agent_run_id)
        return Task(**data)

    @staticmethod
    def _deserialize_task_solution(solution_row: TaskSolutionORM) -> TaskSolution:
        data = dict(solution_row.data or {})
        data.setdefault("solution_id", solution_row.solution_id)
        data.setdefault("task_id", solution_row.task_id)
        data.setdefault("agent_run_id", solution_row.agent_run_id)
        data.setdefault("validator_round_id", solution_row.validator_round_id)
        data.setdefault("validator_uid", solution_row.validator_uid)
        data.setdefault("miner_uid", solution_row.miner_uid)
        return TaskSolution(**data)

    @staticmethod
    def _deserialize_evaluation(evaluation_row: EvaluationResultORM) -> EvaluationResult:
        data = dict(evaluation_row.data or {})
        data.setdefault("evaluation_id", evaluation_row.evaluation_id)
        data.setdefault("task_id", evaluation_row.task_id)
        data.setdefault("task_solution_id", evaluation_row.task_solution_id)
        data.setdefault("agent_run_id", evaluation_row.agent_run_id)
        data.setdefault("validator_round_id", evaluation_row.validator_round_id)
        data.setdefault("validator_uid", evaluation_row.validator_uid)
        data.setdefault("miner_uid", evaluation_row.miner_uid)
        return EvaluationResult(**data)

    @staticmethod
    def _sort_tasks(tasks: List[UITask], sort_by: str, sort_order: str) -> List[UITask]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(tasks, key=lambda task: getattr(task, sort_by), reverse=reverse)
        except Exception:  # noqa: BLE001
            return tasks


def _parse_iso(value: Optional[float]) -> datetime:
    if value is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(0, tz=timezone.utc)

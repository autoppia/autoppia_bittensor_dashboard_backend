from __future__ import annotations

import logging
from collections import defaultdict
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

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
    ValidatorRound,
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
    ActionType,
    TaskAnalytics,
    TaskDetails,
    TaskRelationships,
    TaskInfo,
    TaskLog,
    TaskMetadata,
    TaskAgentRunSummary,
    TaskEvaluationSummary,
    TaskMinerSummary,
    TaskPerformance,
    TaskRoundSummary,
    TaskResults,
    TaskSolutionSummary,
    TaskSummary,
    TaskScreenshot,
    TaskStatistics,
    TaskTimeline,
    TaskStatus,
    LogLevel,
    UseCasePerformance,
    ValidatorInfo,
    TaskValidatorSummary,
    WebsitePerformance,
)
from app.services.ui.rounds_service import AgentRunContext, RoundsService
from app.utils.images import resolve_agent_image, resolve_validator_image
from app.config import settings
from app.services.round_calc import compute_boundaries_for_round

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
    if not round_id:
        return 0
    suffix = round_id
    if round_id.startswith("round_"):
        suffix = round_id.split("round_", 1)[1]
    elif "_" in round_id:
        suffix = round_id.split("_", 1)[1]
    digits: list[str] = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return 0
    try:
        return int("".join(digits))
    except ValueError:
        return 0


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _format_validator_id(validator_uid: Optional[int]) -> str:
    return (
        f"validator-{validator_uid}"
        if validator_uid is not None
        else "validator-unknown"
    )


def _normalize_media_url(
    value: Optional[str], mime: str = "image/gif"
) -> Optional[str]:
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://", "data:")):
        return candidate
    if candidate.startswith("//"):
        return f"https:{candidate}"
    try:
        base64.b64decode(candidate, validate=True)
        return f"data:{mime};base64,{candidate}"
    except Exception:  # noqa: BLE001
        pass
    base = settings.ASSET_BASE_URL.rstrip("/") if settings.ASSET_BASE_URL else ""
    normalized = candidate.lstrip("/")
    if base:
        return f"{base}/{normalized}"
    return f"/{normalized}"


@dataclass
class TaskContext:
    round: ValidatorRound
    agent_run: AgentEvaluationRun
    task: Task
    solution: Optional[TaskSolution]
    evaluation: Optional[EvaluationResult]


class TasksService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def list_tasks(
        self,
        page: int,
        limit: int,
        include_details: bool = False,
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
        # Only load relationships if we need details
        # For basic list, we skip solutions/evaluations (much faster)
        if include_details:
            stmt = (
                select(TaskORM)
                .options(
                    selectinload(TaskORM.task_solutions),
                    selectinload(TaskORM.evaluation_results),
                )
                .order_by(TaskORM.id.desc())
            )
        else:
            # Fast path: no relationships loaded
            stmt = select(TaskORM).order_by(TaskORM.id.desc())

        # Materialize rows (and selectinloaded relationships) eagerly to avoid
        # AsyncSession lazy-loads triggering MissingGreenlet later.
        task_rows_result = await self.session.scalars(stmt)
        task_rows = task_rows_result.unique().all()

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

        context_cache: Dict[str, AgentRunContext] = {}
        items: List[UITask] = []
        for task_row in task_rows:
            try:
                context = await self._build_context(task_row, context_cache)
            except ValueError as exc:
                logger.warning(str(exc))
                continue

            if agent_run_id and context.agent_run.agent_run_id != agent_run_id:
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

            ui_task = self._build_ui_task(context, include_details=include_details)
            evaluation_score = (
                context.evaluation.final_score if context.evaluation else 0.0
            )
            run_start_ts = (
                context.agent_run.started_at or context.round.started_at or 0.0
            )

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
                    for name, count in sorted(
                        website_counts.items(), key=lambda item: item[1], reverse=True
                    )
                ],
                "useCases": [
                    {"name": name, "count": count}
                    for name, count in sorted(
                        use_case_counts.items(), key=lambda item: item[1], reverse=True
                    )
                ],
                "statuses": [
                    {"name": name, "count": count}
                    for name, count in sorted(
                        status_counts.items(), key=lambda item: item[1], reverse=True
                    )
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
                selectinload(TaskORM.task_solutions),
                selectinload(TaskORM.evaluation_results),
            )
            .where(TaskORM.task_id == task_id)
        )
        task_row = await self.session.scalar(stmt)
        if not task_row:
            raise ValueError(f"Task {task_id} not found")
        context_cache: Dict[str, AgentRunContext] = {}
        return await self._build_context(task_row, context_cache)

    async def analytics(self) -> TaskAnalytics:
        stmt = select(TaskORM).options(
            selectinload(TaskORM.task_solutions),
            selectinload(TaskORM.evaluation_results),
        )
        rows = await self.session.scalars(stmt)

        context_cache: Dict[str, AgentRunContext] = {}
        contexts: List[TaskContext] = []
        for row in rows:
            try:
                contexts.append(await self._build_context(row, context_cache))
            except ValueError:
                continue

        total = len(contexts)
        completed = len(
            [
                ctx
                for ctx in contexts
                if ctx.evaluation and ctx.evaluation.final_score >= 0.5
            ]
        )
        failed = len(
            [
                ctx
                for ctx in contexts
                if ctx.evaluation and ctx.evaluation.final_score < 0.5
            ]
        )

        scores = [ctx.evaluation.final_score for ctx in contexts if ctx.evaluation]
        durations = [
            ctx.evaluation.evaluation_time for ctx in contexts if ctx.evaluation
        ]

        average_score = sum(scores) / len(scores) if scores else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        success_rate = (completed / total * 100.0) if total else 0.0

        website_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0,
                "successful": 0,
                "failed": 0,
                "score": 0.0,
                "duration": 0.0,
            }
        )
        use_case_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0,
                "successful": 0,
                "failed": 0,
                "score": 0.0,
                "duration": 0.0,
            }
        )

        performance_over_time: List[Dict[str, Any]] = []
        for context in contexts:
            evaluation_score = (
                context.evaluation.final_score if context.evaluation else 0.0
            )
            evaluation_duration = (
                context.evaluation.evaluation_time if context.evaluation else 0.0
            )
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
                averageScore=(
                    (values["score"] / values["tasks"]) if values["tasks"] else 0.0
                ),
                averageDuration=(
                    (values["duration"] / values["tasks"]) if values["tasks"] else 0.0
                ),
            )
            for website, values in website_stats.items()
        ]

        performance_by_use_case = [
            UseCasePerformance(
                useCase=use_case,
                tasks=int(values["tasks"]),
                successful=int(values["successful"]),
                failed=int(values["failed"]),
                averageScore=(
                    (values["score"] / values["tasks"]) if values["tasks"] else 0.0
                ),
                averageDuration=(
                    (values["duration"] / values["tasks"]) if values["tasks"] else 0.0
                ),
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

        compared_tasks = [self._build_ui_task(ctx, include_details=False) for ctx in contexts]
        best = max(compared_tasks, key=lambda t: t.score, default=None)
        fastest = min(compared_tasks, key=lambda t: t.duration, default=None)
        most_actions = max(
            compared_tasks, key=lambda t: len(t.actions or []), default=None
        )
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
        task = self._build_ui_task(context, include_details=True)
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

        # Ensure epochs are present for finished rounds even if seeding set them to None
        start_epoch_val = getattr(context.round, "start_epoch", None)
        end_epoch_val = getattr(context.round, "end_epoch", None)
        if end_epoch_val is None:
            try:
                status_lower = str(context.round.status or "").lower()
                if status_lower in {"completed", "finished", "complete"}:
                    bounds = compute_boundaries_for_round(
                        int(context.round.round_number or 0)
                    )
                    end_epoch_val = int(bounds.end_epoch)
                    if start_epoch_val is None:
                        start_epoch_val = int(bounds.start_epoch)
            except Exception:  # noqa: BLE001
                pass

        round_summary = TaskRoundSummary(
            validatorRoundId=context.round.validator_round_id,
            roundNumber=context.round.round_number,
            status=context.round.status,
            startedAt=_parse_iso(context.round.started_at),
            endedAt=(
                _parse_iso(context.round.ended_at) if context.round.ended_at else None
            ),
            startEpoch=start_epoch_val,
            endEpoch=end_epoch_val,
        )

        validator_model: Optional[ValidatorInfo] = None
        if context.round.validators:
            validator_model = next(
                (
                    val
                    for val in context.round.validators
                    if val.uid == context.agent_run.validator_uid
                ),
                context.round.validators[0],
            )
        elif getattr(context.round, "validator_info", None):
            validator_model = context.round.validator_info

        if validator_model is None:
            validator_model = ValidatorInfo(
                uid=context.agent_run.validator_uid,
                hotkey=_format_validator_id(context.agent_run.validator_uid),
                coldkey=None,
                stake=0.0,
                vtrust=0.0,
                name=None,
                version=None,
            )

        # Ensure UID is non-negative and resolve validator image with fallback
        validator_summary = TaskValidatorSummary(
            uid=abs(int(validator_model.uid)) if validator_model.uid is not None else 0,
            hotkey=validator_model.hotkey,
            coldkey=validator_model.coldkey,
            name=validator_model.name,
            stake=float(getattr(validator_model, "stake", 0.0) or 0.0),
            vtrust=float(getattr(validator_model, "vtrust", 0.0) or 0.0),
            version=getattr(validator_model, "version", None),
            image=resolve_validator_image(
                name=validator_model.name,
                existing=getattr(validator_model, "image_url", None),
            ),
        )

        miner_model = context.agent_run.miner_info
        miner_summary = TaskMinerSummary(
            uid=(
                abs(int(miner_model.uid))
                if (miner_model and miner_model.uid is not None)
                else abs(int(context.agent_run.miner_uid))
            ),
            hotkey=miner_model.hotkey if miner_model else None,
            name=(
                miner_model.agent_name
                if miner_model and miner_model.agent_name
                else _format_agent_id(context.agent_run.miner_uid)
            ),
            github=getattr(miner_model, "github", None) if miner_model else None,
            image=resolve_agent_image(miner_model),
            isSota=context.agent_run.is_sota,
        )

        started_at_dt = datetime.fromtimestamp(
            context.agent_run.started_at, tz=timezone.utc
        )
        ended_at_dt = (
            datetime.fromtimestamp(context.agent_run.ended_at, tz=timezone.utc)
            if context.agent_run.ended_at
            else None
        )
        if context.agent_run.elapsed_sec is not None:
            duration = _safe_int(context.agent_run.elapsed_sec)
        elif ended_at_dt:
            duration = int((ended_at_dt - started_at_dt).total_seconds())
        else:
            duration = None

        if context.evaluation:
            evaluation_status = (
                TaskStatus.COMPLETED
                if context.evaluation.final_score >= 0.5
                else TaskStatus.FAILED
            )
        elif context.agent_run.ended_at:
            evaluation_status = (
                TaskStatus.FAILED if task.score < 0.5 else TaskStatus.COMPLETED
            )
        else:
            evaluation_status = TaskStatus.RUNNING

        agent_run_summary = TaskAgentRunSummary(
            agentRunId=context.agent_run.agent_run_id,
            validatorUid=context.agent_run.validator_uid,
            minerUid=context.agent_run.miner_uid,
            isSota=context.agent_run.is_sota,
            startedAt=started_at_dt,
            endedAt=ended_at_dt,
            duration=duration,
            taskCount=context.agent_run.n_tasks_total
            or len(context.agent_run.task_ids or []),
            completedTasks=context.agent_run.n_tasks_completed,
            failedTasks=context.agent_run.n_tasks_failed,
            averageScore=context.agent_run.avg_eval_score,
        )

        evaluation_summary: Optional[TaskEvaluationSummary] = None
        if context.evaluation:
            evaluation_summary = TaskEvaluationSummary(
                evaluationId=context.evaluation.evaluation_id,
                finalScore=context.evaluation.final_score,
                rawScore=context.evaluation.raw_score or context.evaluation.final_score,
                evaluationTime=context.evaluation.evaluation_time,
                status=evaluation_status,
                validatorUid=context.evaluation.validator_uid,
                minerUid=context.evaluation.miner_uid,
                webAgentId=context.evaluation.web_agent_id,
                hasFeedback=bool(context.evaluation.feedback),
                hasRecording=bool(context.evaluation.gif_recording),
            )

        solution_summary: Optional[TaskSolutionSummary] = None
        if context.solution:
            solution_summary = TaskSolutionSummary(
                solutionId=context.solution.solution_id,
                agentRunId=context.solution.agent_run_id,
                minerUid=context.solution.miner_uid,
                validatorUid=context.solution.validator_uid,
                actionsCount=len(context.solution.actions or []),
                webAgentId=context.solution.web_agent_id,
                hasRecording=bool(context.solution.recording),
            )

        relationships = TaskRelationships(
            round=round_summary,
            validator=validator_summary,
            miner=miner_summary,
            agentRun=agent_run_summary,
            evaluation=evaluation_summary,
            solution=solution_summary,
        )

        task_payload = task.model_dump()
        task_payload["performance"] = performance
        task_payload["metadata"] = metadata
        task_payload["relationships"] = relationships
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
                (
                    val
                    for val in context.round.validators
                    if val.uid == context.agent_run.validator_uid
                ),
                context.round.validators[0],
            )

        validator_info = ValidatorInfo(
            id=_format_validator_id(context.agent_run.validator_uid),
            name=(
                validator.name
                if validator and validator.name
                else _format_validator_id(context.agent_run.validator_uid)
            ),
            image="https://placehold.co/64x64?text=V",
            description="",
            website="",
            github="",
        )

        miner = context.agent_run.miner_info
        agent_info = AgentInfo(
            id=_format_agent_id(context.agent_run.miner_uid),
            name=(
                miner.agent_name
                if miner and miner.agent_name
                else _format_agent_id(context.agent_run.miner_uid)
            ),
            type="sota" if context.agent_run.is_sota else "miner",
            image=resolve_agent_image(miner),
            description=miner.description if miner and miner.description else "",
        )

        evaluation_score = context.evaluation.final_score if context.evaluation else 0.0
        task_status = (
            TaskStatus.COMPLETED if evaluation_score >= 0.5 else TaskStatus.FAILED
        )

        task_info = TaskInfo(
            id=context.task.task_id,
            website=context.task.url,
            useCase=self._extract_use_case(context.task),
            status=task_status,
            score=evaluation_score,
        )

        return PersonasData(
            round=round_info, validator=validator_info, agent=agent_info, task=task_info
        )

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
            status=(
                "completed"
                if context.evaluation and context.evaluation.final_score >= 0.5
                else "failed"
            ),
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
            # Get action as dict if possible
            if isinstance(action, dict):
                action_dict = action
            elif hasattr(action, "model_dump"):
                action_dict = action.model_dump()
            elif hasattr(action, "dict"):
                action_dict = action.dict()
            else:
                action_dict = {}

            # Get attributes
            attributes: Dict[str, Any] = {}
            if hasattr(action, "attributes"):
                attributes = getattr(action, "attributes", {}) or {}
            elif isinstance(action, dict):
                attributes = action.get("attributes", {}) or {}

            # Extract and normalize type
            raw_type = getattr(action, "type", None) or action_dict.get(
                "type", ActionType.OTHER.value
            )
            if isinstance(raw_type, ActionType):
                raw_type_value = raw_type.value
            else:
                raw_type_value = str(raw_type).lower()

            # Convert "NavigateAction" → "navigate", "ClickAction" → "click", etc.
            normalized_type_key = (
                raw_type_value.replace("action", "").replace("-", "_").strip()
            )

            alias_map = {
                # Navigation actions
                "navigate": ActionType.NAVIGATE,
                "navigation": ActionType.NAVIGATE,
                "goto": ActionType.NAVIGATE,
                "visit": ActionType.NAVIGATE,
                "load": ActionType.NAVIGATE,
                # Click actions (all mouse click variants)
                "click": ActionType.CLICK,
                "doubleclick": ActionType.CLICK,
                "rightclick": ActionType.CLICK,
                "middleclick": ActionType.CLICK,
                "tripleclick": ActionType.CLICK,
                "mousedown": ActionType.CLICK,
                "mouseup": ActionType.CLICK,
                "mousemove": ActionType.CLICK,
                "hover": ActionType.CLICK,  # HoverAction maps to CLICK
                "tap": ActionType.CLICK,
                "press": ActionType.CLICK,
                "select": ActionType.CLICK,
                # Input/typing actions
                # Prefer a single label (INPUT) for entering text
                "type": ActionType.INPUT,
                "input": ActionType.INPUT,
                "fill": ActionType.INPUT,
                "type_text": ActionType.INPUT,
                "enter": ActionType.INPUT,
                "write": ActionType.INPUT,
                "text": ActionType.INPUT,
                "sendkeysiwa": ActionType.INPUT,  # SendKeysIWAAction
                "holdkey": ActionType.INPUT,  # HoldKeyAction
                # Search actions
                "search": ActionType.SEARCH,
                "find": ActionType.SEARCH,
                "lookup": ActionType.SEARCH,
                # Extract/scrape actions
                "extract": ActionType.EXTRACT,
                "scrape": ActionType.EXTRACT,
                "get": ActionType.EXTRACT,
                "read": ActionType.EXTRACT,
                "parse": ActionType.EXTRACT,
                "getdropdownoptions": ActionType.EXTRACT,  # GetDropDownOptionsAction
                "assert": ActionType.EXTRACT,  # AssertAction
                # Submit actions
                "submit": ActionType.SUBMIT,
                "form_submit": ActionType.SUBMIT,
                "send": ActionType.SUBMIT,
                "post": ActionType.SUBMIT,
                "selectdropdownoption": ActionType.SUBMIT,  # SelectDropDownOptionAction
                # Tab management
                "open_tab": ActionType.OPEN_TAB,
                "open_new_tab": ActionType.OPEN_TAB,
                "new_tab": ActionType.OPEN_TAB,
                "close_tab": ActionType.CLOSE_TAB,
                "close_current_tab": ActionType.CLOSE_TAB,
                "close": ActionType.CLOSE_TAB,
                # Wait actions
                "wait": ActionType.WAIT,
                "pause": ActionType.WAIT,
                "sleep": ActionType.WAIT,
                "delay": ActionType.WAIT,
                "idle": ActionType.WAIT,  # IdleAction
                # Scroll actions
                "scroll": ActionType.SCROLL,
                "scroll_up": ActionType.SCROLL,
                "scroll_down": ActionType.SCROLL,
                "scroll_to": ActionType.SCROLL,
                # Screenshot actions
                "screenshot": ActionType.SCREENSHOT,
                "capture": ActionType.SCREENSHOT,
                "snap": ActionType.SCREENSHOT,
                "photo": ActionType.SCREENSHOT,
                # Drag and drop actions (map to CLICK for now)
                "draganddrop": ActionType.CLICK,
                "leftclickdrag": ActionType.CLICK,
                # Undefined actions
                "undefined": ActionType.OTHER,
            }
            action_type = alias_map.get(normalized_type_key)
            if action_type is None:
                try:
                    action_type = ActionType(normalized_type_key)
                except ValueError:
                    action_type = ActionType.OTHER

            # Extract selector - check action object first, then attributes
            selector = None
            if hasattr(action, "selector"):
                selector_obj = getattr(action, "selector")
                if isinstance(selector_obj, dict):
                    # Selector is a dict with type and value (e.g., xpathSelector)
                    selector = selector_obj.get("value") or str(selector_obj)
                elif selector_obj is not None:
                    selector = str(selector_obj)
            elif "selector" in action_dict:
                selector_obj = action_dict["selector"]
                if isinstance(selector_obj, dict):
                    selector = selector_obj.get("value") or str(selector_obj)
                elif selector_obj is not None:
                    selector = str(selector_obj)

            if not selector:
                sel_attr = (
                    attributes.get("selector") if isinstance(attributes, dict) else None
                )
                if isinstance(sel_attr, dict):
                    selector = sel_attr.get("value") or str(sel_attr)
                else:
                    selector = sel_attr

            # Extract value - check action object first, then attributes
            value = (
                getattr(action, "url", None)
                or action_dict.get("url")
                or getattr(action, "value", None)
                or action_dict.get("value")
                or getattr(action, "text", None)
                or action_dict.get("text")
                or attributes.get("value")
                or attributes.get("url")
                or attributes.get("text")
                or attributes.get("label")
                or attributes.get("field")
                or attributes.get("for")
            )
            if value is not None:
                value = str(value)

            # Extract duration - check action object first, then attributes
            duration_candidate = (
                getattr(action, "duration", None)
                or action_dict.get("duration")
                or getattr(action, "time_seconds", None)
                or action_dict.get("time_seconds")
                or attributes.get("durationSeconds")
                or attributes.get("duration")
            )
            try:
                duration = (
                    float(duration_candidate) if duration_candidate is not None else 0.0
                )
            except (TypeError, ValueError):
                duration = 0.0

            # Extract success status
            status = attributes.get("status")
            if isinstance(status, str):
                success_flag = status.lower() not in {"failed", "error"}
            else:
                success_flag = bool(getattr(action, "success", True))

            error_message = (
                str(attributes.get("error"))
                if attributes.get("error") is not None
                else None
            )

            # Build metadata from the complete action object
            metadata = {
                "attributes": attributes,
                "raw_action": action_dict,  # Include full action data for debugging
            }

            actions.append(
                TaskAction(
                    id=str(getattr(action, "id", index)),
                    type=action_type,
                    selector=selector,
                    value=value,
                    timestamp=self._action_timestamp(context, index),
                    duration=duration,
                    success=success_flag,
                    error=error_message,
                    metadata=metadata,
                )
            )
        return actions

    def build_screenshots(self, context: TaskContext) -> List[TaskScreenshot]:
        screenshots: List[TaskScreenshot] = []
        base_ts = context.agent_run.started_at or context.round.started_at or 0.0
        timestamp = _parse_iso(base_ts)

        screenshot_url = _normalize_media_url(
            getattr(context.task, "screenshot", None), mime="image/png"
        )
        if screenshot_url:
            screenshots.append(
                TaskScreenshot(
                    id=f"{context.task.task_id}_screenshot",
                    url=screenshot_url,
                    timestamp=timestamp,
                    actionId=None,
                    description=context.task.screenshot_description,
                )
            )

        if context.evaluation:
            gif_url = _normalize_media_url(
                getattr(context.evaluation, "gif_recording", None), mime="image/gif"
            )
        else:
            gif_url = None

        if gif_url:
            screenshots.append(
                TaskScreenshot(
                    id=f"{context.task.task_id}_recording",
                    url=gif_url,
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
                        timestamp=datetime.fromtimestamp(
                            base_ts + index, tz=timezone.utc
                        ),
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
                        timestamp=datetime.fromtimestamp(
                            base_ts + index, tz=timezone.utc
                        ),
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

    def _resolve_agent_run_id(self, task_row: TaskORM) -> Optional[str]:
        # Try to get from data first (fast, no lazy loading)
        data = task_row.data or {}
        agent_run_id = data.get("agent_run_id")
        if agent_run_id:
            return agent_run_id
        
        # Try from relationships only if they're already loaded
        try:
            for evaluation_row in task_row.evaluation_results or []:
                if evaluation_row.agent_run_id:
                    return evaluation_row.agent_run_id
        except:
            pass
        
        try:
            for solution_row in task_row.task_solutions or []:
                if solution_row.agent_run_id:
                    return solution_row.agent_run_id
        except:
            pass
        
        return None
        return str(agent_run_id)

    async def _build_context(
        self,
        task_row: TaskORM,
        cache: Optional[Dict[str, AgentRunContext]] = None,
    ) -> TaskContext:
        if cache is None:
            cache = {}

        agent_run_id = self._resolve_agent_run_id(task_row)
        if not agent_run_id:
            raise ValueError(f"Task {task_row.task_id} missing agent run reference")

        context = cache.get(agent_run_id)
        if context is None:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
            cache[agent_run_id] = context

        task_model = next(
            (task for task in context.tasks if task.task_id == task_row.task_id),
            None,
        )
        if task_model is None:
            task_model = self._deserialize_task(task_row)

        solution_model = None
        if context.task_solutions:
            solution_model = next(
                (
                    solution
                    for solution in context.task_solutions
                    if solution.task_id == task_row.task_id
                ),
                None,
            )
        if solution_model is None and task_row.task_solutions:
            matching_solutions = [
                solution_row
                for solution_row in task_row.task_solutions
                if solution_row.agent_run_id == agent_run_id
            ]
            target_solution = (
                matching_solutions[0]
                if matching_solutions
                else task_row.task_solutions[0]
            )
            solution_model = self._deserialize_task_solution(target_solution)

        evaluation_model = None
        if context.evaluation_results:
            evaluation_model = next(
                (
                    evaluation
                    for evaluation in context.evaluation_results
                    if evaluation.task_id == task_row.task_id
                ),
                None,
            )
        if evaluation_model is None and task_row.evaluation_results:
            matching_evaluations = [
                evaluation_row
                for evaluation_row in task_row.evaluation_results
                if evaluation_row.agent_run_id == agent_run_id
            ]
            target_evaluation = (
                matching_evaluations[0]
                if matching_evaluations
                else task_row.evaluation_results[0]
            )
            evaluation_model = self._deserialize_evaluation(target_evaluation)

        return TaskContext(
            round=context.round,
            agent_run=context.run,
            task=task_model,
            solution=solution_model,
            evaluation=evaluation_model,
        )

    def _build_ui_task(self, context: TaskContext, include_details: bool = False) -> UITask:
        evaluation = context.evaluation
        score = evaluation.final_score if evaluation else 0.0
        status = TaskStatus.COMPLETED if score >= 0.5 else TaskStatus.FAILED
        success_rate = int(score * 100)

        actions = self.build_actions(context) if include_details else []

        start_time = context.agent_run.started_at or context.round.started_at
        end_time = context.agent_run.ended_at or start_time

        # Extract seed from URL if present
        seed_val: Optional[str] = None
        try:
            parsed = urlparse(context.task.url or "")
            if parsed and parsed.query:
                q = parse_qs(parsed.query)
                if isinstance(q.get("seed"), list):
                    seed_val = q.get("seed")[0]
                elif q.get("seed"):
                    seed_val = str(q.get("seed"))
        except Exception:
            seed_val = None

        # Get validator info
        validator_name = None
        validator_image = None
        if context.round.validators:
            validator_model = next(
                (
                    v
                    for v in context.round.validators
                    if v.uid == context.agent_run.validator_uid
                ),
                context.round.validators[0] if context.round.validators else None,
            )
            if validator_model:
                validator_name = validator_model.name
                validator_image = resolve_validator_image(
                    name=validator_model.name,
                    existing=getattr(validator_model, "image_url", None),
                )

        # Get miner info
        miner_name = None
        miner_image = None
        if context.agent_run.miner_info:
            miner_name = context.agent_run.miner_info.agent_name
            miner_image = resolve_agent_image(context.agent_run.miner_info)

        return UITask(
            taskId=context.task.task_id,
            agentRunId=context.agent_run.agent_run_id,
            website=context.task.url,
            seed=seed_val,
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
            validatorName=validator_name,
            validatorImage=validator_image,
            minerName=miner_name,
            minerImage=miner_image,
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
    def _deserialize_task(task_row: TaskORM) -> Task:
        data = dict(task_row.data or {})
        data.setdefault("task_id", task_row.task_id)
        data.setdefault("validator_round_id", task_row.validator_round_id)
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
    def _deserialize_evaluation(
        evaluation_row: EvaluationResultORM,
    ) -> EvaluationResult:
        data = dict(evaluation_row.data or {})
        data.setdefault("evaluation_id", evaluation_row.evaluation_id)
        data.setdefault("task_id", evaluation_row.task_id)
        data.setdefault("task_solution_id", evaluation_row.task_solution_id)
        data.setdefault("agent_run_id", evaluation_row.agent_run_id)
        data.setdefault("validator_round_id", evaluation_row.validator_round_id)
        data.setdefault("validator_uid", evaluation_row.validator_uid)
        data.setdefault("miner_uid", evaluation_row.miner_uid)
        result = EvaluationResult(**data)
        if evaluation_row.gif_recording:
            result.gif_recording = evaluation_row.gif_recording
        return result

    @staticmethod
    def _sort_tasks(tasks: List[UITask], sort_by: str, sort_order: str) -> List[UITask]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(
                tasks, key=lambda task: getattr(task, sort_by), reverse=reverse
            )
        except Exception:  # noqa: BLE001
            return tasks


def _parse_iso(value: Optional[float]) -> datetime:
    if value is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(0, tz=timezone.utc)

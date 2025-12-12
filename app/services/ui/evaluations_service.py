from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg

from sqlalchemy import select
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError, InterfaceError as SQLInterfaceError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)
from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    ValidatorRound,
    Task,
    TaskSolution,
)
from app.models.ui.agent_runs import Action
from app.models.ui.evaluations import (
    EvaluationDetail,
    EvaluationDetailResponse,
    EvaluationListItem,
    EvaluationStatus,
    EvaluationTaskInfo,
)
from app.services.ui.rounds_service import (
    RoundsService,
    _get_validator_uid_from_context,
)

logger = logging.getLogger(__name__)


def _safe_round(value: float, digits: int = 3) -> float:
    try:
        return round(float(value), digits)
    except Exception:  # noqa: BLE001
        return 0.0


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


def _parse_identifier(identifier: str) -> int:
    if "-" in identifier:
        identifier = identifier.split("-", 1)[1]
    if "_" in identifier:
        identifier = identifier.split("_", 1)[1]
    return int(identifier)


@dataclass
class EvaluationContext:
    round: ValidatorRound
    agent_run: AgentEvaluationRun
    task: Task
    task_solution: TaskSolution
    evaluation: Evaluation


class EvaluationsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def list_evaluations(
        self,
        page: int,
        limit: int,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        validator_id: Optional[str] = None,
        task_id: Optional[str] = None,
        round_id: Optional[int] = None,
    ) -> Dict[str, object]:
        skip = (page - 1) * limit

        stmt = (
            select(EvaluationORM)
            .options(
                selectinload(EvaluationORM.agent_run).selectinload(
                    AgentEvaluationRunORM.validator_round
                ),
                selectinload(EvaluationORM.agent_run)
                .selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.miner_snapshots),
                selectinload(EvaluationORM.agent_run)
                .selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.validator_snapshot),  # 1:1 relationship (singular)
                selectinload(EvaluationORM.task),
                selectinload(EvaluationORM.task_solution),
            )
            .order_by(EvaluationORM.id.desc())
        )

        if run_id:
            stmt = stmt.where(EvaluationORM.agent_run_id == run_id)
        if task_id:
            stmt = stmt.where(EvaluationORM.task_id == task_id)
        if round_id is not None:
            stmt = stmt.where(
                EvaluationORM.validator_round_id == f"round_{round_id:03d}"
            )

        result = await self.session.scalars(stmt)
        contexts: List[EvaluationContext] = []
        for evaluation_row in result:
            context = self._build_context(evaluation_row)
            if agent_id:
                miner_uid = _parse_identifier(agent_id)
                if context.agent_run.miner_uid != miner_uid:
                    continue
            if validator_id:
                validator_uid = _parse_identifier(validator_id)
                context_validator_uid = _get_validator_uid_from_context(context)
                if context_validator_uid != validator_uid:
                    continue

            contexts.append(context)

        total = len(contexts)
        page_contexts = contexts[skip : skip + limit]
        items = [self._build_list_item(context) for context in page_contexts]

        return {
            "evaluations": items,
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def get_evaluation(self, evaluation_id: str) -> EvaluationContext:
        stmt = (
            select(EvaluationORM)
            .options(
                selectinload(EvaluationORM.agent_run)
                .selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.miner_snapshots),
                selectinload(EvaluationORM.agent_run)
                .selectinload(AgentEvaluationRunORM.validator_round)
                .selectinload(RoundORM.validator_snapshot),  # 1:1 relationship (singular)
                selectinload(EvaluationORM.task),
                selectinload(EvaluationORM.task_solution),
            )
            .where(EvaluationORM.evaluation_id == evaluation_id)
        )
        evaluation_row = await self.session.scalar(stmt)
        if not evaluation_row:
            raise ValueError(f"Evaluation {evaluation_id} not found")
        return self._build_context(evaluation_row)

    async def update_gif_recording(self, evaluation_id: str, gif_url: str) -> None:
        """Update GIF recording URL for an evaluation, with retry on connection errors."""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                stmt = select(EvaluationORM).where(
                    EvaluationORM.evaluation_id == evaluation_id
                )
                result_rows = await self.session.scalars(stmt)
                rows = list(result_rows)
                if not rows:
                    raise ValueError(f"No evaluations found for {evaluation_id}")

                for row in rows:
                    row.gif_recording = gif_url

                await self.session.commit()
                return
            except (
                AsyncAdapt_asyncpg_dbapi.InterfaceError,
                asyncpg.exceptions.InternalClientError,
                asyncpg.exceptions.ConnectionDoesNotExistError,
                AsyncAdapt_asyncpg_dbapi.Error,  # Catches other asyncpg errors
                SQLInterfaceError,  # SQLAlchemy wraps asyncpg errors
                DBAPIError,  # Base class for all DBAPI errors
            ) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Connection error updating GIF for %s (attempt %d/%d): %s. Retrying...",
                        evaluation_id,
                        attempt + 1,
                        max_retries,
                        str(e),
                    )
                    await self.session.rollback()
                    # Give the connection pool a moment to recover
                    await asyncio.sleep(0.1 * (attempt + 1))
                else:
                    logger.error(
                        "Failed to update GIF for %s after %d attempts: %s",
                        evaluation_id,
                        max_retries,
                        str(e),
                    )
                    await self.session.rollback()
                    raise

    def _build_context(self, evaluation_row: EvaluationORM) -> EvaluationContext:
        agent_run_row = evaluation_row.agent_run
        if agent_run_row is None:
            raise ValueError(
                f"Evaluation {evaluation_row.evaluation_id} missing agent run relationship"
            )
        round_row = agent_run_row.validator_round
        if round_row is None:
            raise ValueError(
                f"Agent run {agent_run_row.agent_run_id} missing round relationship"
            )

        round_model = self.rounds_service._deserialize_round(round_row)
        agent_run_model = self.rounds_service._deserialize_agent_run(
            agent_run_row,
            include_details=False,
        )

        task_row = evaluation_row.task
        if task_row is None:
            raise ValueError(
                f"Evaluation {evaluation_row.evaluation_id} missing task relationship"
            )
        task_model = self._deserialize_task(task_row)

        solution_row = evaluation_row.task_solution
        if solution_row is None:
            raise ValueError(
                f"Evaluation {evaluation_row.evaluation_id} missing task solution relationship"
            )
        solution_model = self._deserialize_task_solution(solution_row)

        evaluation_model = self._deserialize_evaluation(evaluation_row)

        return EvaluationContext(
            round=round_model,
            agent_run=agent_run_model,
            task=task_model,
            task_solution=solution_model,
            evaluation=evaluation_model,
        )

    def _build_list_item(self, context: EvaluationContext) -> EvaluationListItem:
        eval_score = getattr(context.evaluation, "eval_score", getattr(context.evaluation, "final_score", 0.0))
        reward = getattr(context.evaluation, "reward", eval_score)
        status = self._status_from_score(eval_score)
        round_int = _round_id_to_int(context.round.validator_round_id)
        task_url = context.task.url

        created = context.agent_run.started_at or context.round.started_at
        updated = context.agent_run.ended_at or created

        validator_uid = _get_validator_uid_from_context(context)
        return EvaluationListItem(
            evaluationId=context.evaluation.evaluation_id,
            runId=context.agent_run.agent_run_id,
            agentId=_format_agent_id(context.agent_run.miner_uid),
            validatorId=_format_validator_id(validator_uid) if validator_uid else "unknown",
            roundId=round_int,
            taskId=context.task.task_id,
            taskUrl=task_url,
            status=status,
            score=_safe_round(eval_score),
            reward=_safe_round(reward),
            responseTime=_safe_round(
                getattr(context.evaluation, "evaluation_time", 0.0)
            ),
            createdAt=self._format_timestamp(created),
            updatedAt=self._format_timestamp(updated),
        )

    def build_detail(self, context: EvaluationContext) -> EvaluationDetail:
        item = self._build_list_item(context)
        task_info = EvaluationTaskInfo(
            id=context.task.task_id,
            url=context.task.url,
            prompt=context.task.prompt,
            scope=context.task.scope,
            useCase=self._extract_use_case(context.task),
            useCaseMetadata=(
                dict(context.task.use_case)
                if isinstance(context.task.use_case, dict)
                else {}
            ),
        )

        actions: List[Action] = []
        for index, action in enumerate(context.task_solution.actions):
            selector = None
            value = None
            attributes = getattr(action, "attributes", None)
            if isinstance(attributes, dict):
                selector = attributes.get("selector")
                value = attributes.get("value")
            actions.append(
                Action(
                    id=f"{context.task.task_id}_action_{index}",
                    type=getattr(action, "type", "action"),
                    selector=selector,
                    value=value,
                    timestamp=self._format_timestamp(context.agent_run.started_at),
                    duration=float(getattr(action, "duration", 0.0)),
                    success=bool(getattr(action, "success", True)),
                )
            )

        evaluation_dump = context.evaluation.model_dump(mode="json")
        solution_dump = context.task_solution.nested_model_dump(mode="json")

        return EvaluationDetail(
            **item.model_dump(),
            task=task_info,
            actions=actions,
            logs=[],
            screenshots=list(getattr(context.evaluation, "screenshots", []) or []),
            taskSolution=solution_dump,
            evaluationResult=evaluation_dump,
            extras=dict(getattr(context.evaluation, "extras", {}) or {}),
        )

    @staticmethod
    def _status_from_score(score: float) -> EvaluationStatus:
        if score >= 0.5:
            return EvaluationStatus.PASSED
        if score > 0:
            return EvaluationStatus.FAILED
        return EvaluationStatus.PENDING

    @staticmethod
    def _format_timestamp(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            return None

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
        # Include web_version from the database column if not already in data
        if "web_version" not in data or data.get("web_version") is None:
            data["web_version"] = task_row.web_version
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
        evaluation_row: EvaluationORM,
    ) -> Evaluation:
        data = dict(evaluation_row.data or {})
        data.setdefault("evaluation_id", evaluation_row.evaluation_id)
        data.setdefault("task_id", evaluation_row.task_id)
        data.setdefault("task_solution_id", evaluation_row.task_solution_id)
        data.setdefault("agent_run_id", evaluation_row.agent_run_id)
        data.setdefault("validator_round_id", evaluation_row.validator_round_id)
        data.setdefault("validator_uid", evaluation_row.validator_uid)
        data.setdefault("validator_hotkey", evaluation_row.validator_hotkey)
        data.setdefault("miner_uid", evaluation_row.miner_uid)
        data.setdefault("miner_hotkey", evaluation_row.miner_hotkey)
        data.setdefault("eval_score", getattr(evaluation_row, "eval_score", getattr(evaluation_row, "final_score", 0.0)))
        data.setdefault("reward", getattr(evaluation_row, "reward", getattr(evaluation_row, "eval_score", getattr(evaluation_row, "final_score", 0.0))))
        data.setdefault("evaluation_time", evaluation_row.evaluation_time)
        data.setdefault("execution_history", evaluation_row.execution_history)
        data.setdefault("feedback", evaluation_row.feedback)
        data.setdefault("web_agent_id", evaluation_row.web_agent_id)
        data.setdefault("stats", evaluation_row.stats)
        data.setdefault("gif_recording", evaluation_row.gif_recording)
        data.setdefault("metadata", evaluation_row.meta)
        result = Evaluation(**data)
        return result

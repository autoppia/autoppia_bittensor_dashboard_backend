from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import InterfaceError as SQLInterfaceError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
)
from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    MinerInfo,
    Task,
    TaskSolution,
    ValidatorRound,
)
from app.models.core import (
    ValidatorInfo as CoreValidatorInfo,
)
from app.models.ui.agent_runs import Action
from app.models.ui.evaluations import (
    EvaluationDetail,
    EvaluationListItem,
    EvaluationStatus,
    EvaluationTaskInfo,
)
from app.services.service_utils import llm_summary_from_usage
from app.services.ui.ui_shared_helpers import (
    format_agent_id as _format_agent_id,
)
from app.services.ui.ui_shared_helpers import (
    format_validator_id as _format_validator_id,
)
from app.services.ui.ui_shared_helpers import (
    parse_identifier as _parse_identifier,
)
from app.services.ui.ui_shared_helpers import (
    round_id_to_int as _round_id_to_int,
)
from app.services.ui.ui_shared_helpers import (
    safe_round as _safe_round,
)

logger = logging.getLogger(__name__)


def _get_validator_uid_from_context(context: "EvaluationContext") -> Optional[int]:
    if hasattr(context.round, "validator_info") and context.round.validator_info:
        return context.round.validator_info.uid
    if hasattr(context.round, "validators") and context.round.validators:
        return context.round.validators[0].uid if context.round.validators else None
    return None


@dataclass
class EvaluationContext:
    round: ValidatorRound
    agent_run: AgentEvaluationRun
    task: Task
    task_solution: TaskSolution
    evaluation: Evaluation


class EvaluationsDomainServiceMixin:
    def __init__(self, session: AsyncSession):
        self.session = session

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
        """
        Lista evaluaciones con paginación optimizada en SQL.

        OPTIMIZACIONES APLICADAS:
        1. Filtros aplicados en SQL (no en Python)
        2. Paginación en SQL (no en memoria)
        3. Count total eficiente
        4. No carga execution_history (relación pesada)
        5. Usa índices compuestos para mejorar rendimiento
        """
        from sqlalchemy import and_, func

        skip = (page - 1) * limit

        # Reused runs have no evaluations; resolve to the run they reference
        if run_id:
            run_row = await self.session.scalar(select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == run_id))
            if run_row and getattr(run_row, "is_reused", False) and getattr(run_row, "reused_from_agent_run_id", None):
                run_id = run_row.reused_from_agent_run_id

        # Construir query base con filtros en SQL
        stmt = select(EvaluationORM)

        # Aplicar filtros en SQL (no en Python)
        filters = []

        if run_id:
            filters.append(EvaluationORM.agent_run_id == run_id)

        if task_id:
            filters.append(EvaluationORM.task_id == task_id)

        if round_id is not None:
            filters.append(EvaluationORM.validator_round_id == f"round_{round_id:03d}")

        # Filtro por agent_id (miner) - requiere JOIN con miner_evaluation_runs
        if agent_id:
            miner_uid = _parse_identifier(agent_id)
            stmt = stmt.join(AgentEvaluationRunORM, EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id)
            filters.append(AgentEvaluationRunORM.miner_uid == miner_uid)

        # Filtro por validator_id
        if validator_id:
            validator_uid = _parse_identifier(validator_id)
            filters.append(EvaluationORM.validator_uid == validator_uid)

        # Aplicar todos los filtros
        if filters:
            stmt = stmt.where(and_(*filters))

        # Contar total ANTES de paginar (optimizado con índices)
        count_stmt = select(func.count()).select_from(stmt.with_only_columns(EvaluationORM.id).subquery())
        total = await self.session.scalar(count_stmt) or 0

        # Aplicar paginación en SQL
        stmt = (
            stmt.options(
                # Cargar relaciones necesarias
                selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round),
                selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round).selectinload(RoundORM.miner_snapshots),
                selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round).selectinload(RoundORM.validator_snapshot),
                selectinload(EvaluationORM.task),
                selectinload(EvaluationORM.task_solution),
                # NO cargar execution_history_record (muy pesado)
            )
            .order_by(EvaluationORM.id.desc())
            .offset(skip)
            .limit(limit)
        )

        # Ejecutar query paginado
        result = await self.session.scalars(stmt)
        evaluation_rows = result.all()

        # Construir items
        items = []
        for evaluation_row in evaluation_rows:
            try:
                context = self._build_context(evaluation_row)
                item = self._build_list_item(context)
                items.append(item)
            except Exception as e:
                logger.warning(f"Error building context for evaluation {evaluation_row.evaluation_id}: {e}")
                continue

        return {
            "evaluations": items,
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def export_evaluations_by_season(self, season: int) -> List[Dict[str, object]]:
        stmt = (
            select(
                EvaluationORM,
                ValidatorRoundORM.season_number,
                ValidatorRoundORM.round_number_in_season,
            )
            .join(
                ValidatorRoundORM,
                EvaluationORM.validator_round_id == ValidatorRoundORM.validator_round_id,
            )
            .join(
                ValidatorRoundMinerORM,
                (ValidatorRoundMinerORM.validator_round_id == EvaluationORM.validator_round_id) & (ValidatorRoundMinerORM.miner_uid == EvaluationORM.miner_uid),
            )
            .where(ValidatorRoundORM.season_number == season)
            .where(ValidatorRoundMinerORM.is_sota.is_(True))
            .order_by(EvaluationORM.id.asc())
        )

        rows = (await self.session.execute(stmt)).all()
        export_items: List[Dict[str, object]] = []
        for evaluation_row, season_number, round_number_in_season in rows:
            export_items.append(
                {
                    "evaluation_id": evaluation_row.evaluation_id,
                    "validator_round_id": evaluation_row.validator_round_id,
                    "agent_run_id": evaluation_row.agent_run_id,
                    "task_id": evaluation_row.task_id,
                    "task_solution_id": evaluation_row.task_solution_id,
                    "miner_uid": evaluation_row.miner_uid,
                    "validator_uid": evaluation_row.validator_uid,
                    "evaluation_score": evaluation_row.evaluation_score,
                    "reward": evaluation_row.reward,
                    "evaluation_time": evaluation_row.evaluation_time,
                    "zero_reason": getattr(evaluation_row, "zero_reason", None),
                    **llm_summary_from_usage(getattr(evaluation_row, "llm_usage", None) or []),
                    "season": season_number,
                    "round_in_season": round_number_in_season,
                    "created_at": evaluation_row.created_at.isoformat() if evaluation_row.created_at else None,
                }
            )

        return export_items

    async def get_evaluation(self, evaluation_id: str) -> EvaluationContext:
        stmt = (
            select(EvaluationORM)
            .options(
                selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round).selectinload(RoundORM.miner_snapshots),
                selectinload(EvaluationORM.agent_run).selectinload(AgentEvaluationRunORM.validator_round).selectinload(RoundORM.validator_snapshot),  # 1:1 relationship (singular)
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
                stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
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
            raise ValueError(f"Evaluation {evaluation_row.evaluation_id} missing agent run relationship")
        round_row = agent_run_row.validator_round
        if round_row is None:
            raise ValueError(f"Agent run {agent_run_row.agent_run_id} missing round relationship")

        round_model = self._deserialize_round(round_row)
        agent_run_model = self._deserialize_agent_run(agent_run_row, round_row)

        task_row = evaluation_row.task
        if task_row is None:
            raise ValueError(f"Evaluation {evaluation_row.evaluation_id} missing task relationship")
        task_model = self._deserialize_task(task_row)

        solution_row = evaluation_row.task_solution
        if solution_row is None:
            raise ValueError(f"Evaluation {evaluation_row.evaluation_id} missing task solution relationship")
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
        evaluation_score = getattr(context.evaluation, "evaluation_score", 0.0)
        reward = getattr(context.evaluation, "reward", evaluation_score)
        status = self._status_from_score(evaluation_score)
        round_int = _round_id_to_int(context.round.validator_round_id)
        task_url = context.task.url

        created = context.agent_run.started_at or context.round.started_at
        updated = context.agent_run.ended_at or created

        validator_uid = _get_validator_uid_from_context(context)

        # Get season from round model
        season = getattr(context.round, "season_number", None)
        if season is None and round_int >= 10000:
            # Fallback: extract from legacy round_number format
            season = round_int // 10000

        return EvaluationListItem(
            evaluationId=context.evaluation.evaluation_id,
            runId=context.agent_run.agent_run_id,
            agentId=_format_agent_id(context.agent_run.miner_uid),
            validatorId=_format_validator_id(validator_uid) if validator_uid else "unknown",
            roundId=round_int,
            season=season,  # Add season field
            taskId=context.task.task_id,
            taskUrl=task_url,
            status=status,
            score=_safe_round(evaluation_score),
            reward=_safe_round(reward),
            responseTime=_safe_round(getattr(context.evaluation, "evaluation_time", 0.0)),
            createdAt=self._format_timestamp(created),
            updatedAt=self._format_timestamp(updated),
            zeroReason=getattr(context.evaluation, "zero_reason", None),
        )

    def build_detail(self, context: EvaluationContext) -> EvaluationDetail:
        item = self._build_list_item(context)
        task_info = EvaluationTaskInfo(
            id=context.task.task_id,
            url=context.task.url,
            prompt=context.task.prompt,
            scope=context.task.scope,
            useCase=self._extract_use_case(context.task),
            useCaseMetadata=(dict(context.task.use_case) if isinstance(context.task.use_case, dict) else {}),
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
        data.setdefault("evaluation_score", getattr(evaluation_row, "evaluation_score", 0.0))
        data.setdefault("reward", getattr(evaluation_row, "reward", getattr(evaluation_row, "evaluation_score", 0.0)))
        data.setdefault("evaluation_time", evaluation_row.evaluation_time)
        data.setdefault("execution_history", evaluation_row.execution_history)
        data.setdefault("web_agent_id", evaluation_row.web_agent_id)
        data.setdefault("stats", evaluation_row.stats)
        data.setdefault("gif_recording", evaluation_row.gif_recording)
        data.setdefault("metadata", evaluation_row.extra_info)
        data.setdefault("zero_reason", getattr(evaluation_row, "zero_reason", None))
        try:
            from sqlalchemy import inspect

            if "llm_usage" not in inspect(evaluation_row).unloaded:
                usage_list = evaluation_row.llm_usage or []
                data.setdefault(
                    "llm_usage",
                    [
                        {
                            "provider": u.provider,
                            "model": u.model,
                            "tokens": u.tokens,
                            "cost": u.cost,
                        }
                        for u in usage_list
                    ],
                )
            else:
                pass
        except Exception:
            pass
        result = Evaluation(**data)
        return result

    @staticmethod
    def _deserialize_round(round_row: ValidatorRoundORM) -> ValidatorRound:
        snapshot = getattr(round_row, "validator_snapshot", None)
        validator_uid = int(getattr(snapshot, "validator_uid", 0) or 0)
        validator_hotkey = str(getattr(snapshot, "validator_hotkey", "") or "")
        validator_info = CoreValidatorInfo(
            uid=validator_uid,
            hotkey=validator_hotkey,
            coldkey=getattr(snapshot, "validator_coldkey", None),
            stake=float(getattr(snapshot, "stake", 0.0) or 0.0),
            vtrust=float(getattr(snapshot, "vtrust", 0.0) or 0.0),
            name=getattr(snapshot, "name", None),
            version=getattr(snapshot, "version", None),
            image_url=getattr(snapshot, "image_url", None),
        )
        return ValidatorRound(
            validator_round_id=round_row.validator_round_id,
            season_number=int(getattr(round_row, "season_number", 0) or 0),
            round_number_in_season=int(getattr(round_row, "round_number_in_season", 0) or 0),
            validator_uid=validator_uid,
            validator_hotkey=validator_hotkey,
            validator_coldkey=getattr(snapshot, "validator_coldkey", None),
            start_block=int(getattr(round_row, "start_block", 0) or 0),
            end_block=getattr(round_row, "end_block", None),
            start_epoch=int(getattr(round_row, "start_epoch", 0) or 0),
            end_epoch=getattr(round_row, "end_epoch", None),
            started_at=float(getattr(round_row, "started_at", 0.0) or 0.0),
            ended_at=getattr(round_row, "ended_at", None),
            n_tasks=int(getattr(round_row, "n_tasks", 0) or 0),
            status=str(getattr(round_row, "status", "finished") or "finished"),
            metadata=dict(getattr(round_row, "validator_summary", None) or {}),
            validators=[validator_info],
            validator_info=validator_info,
        )

    @staticmethod
    def _deserialize_agent_run(
        agent_run_row: AgentEvaluationRunORM,
        round_row: Optional[ValidatorRoundORM],
    ) -> AgentEvaluationRun:
        miner_snapshot = None
        if round_row is not None:
            for snapshot in getattr(round_row, "miner_snapshots", []) or []:
                if (agent_run_row.miner_uid is not None and snapshot.miner_uid == agent_run_row.miner_uid) or (agent_run_row.miner_hotkey and snapshot.miner_hotkey == agent_run_row.miner_hotkey):
                    miner_snapshot = snapshot
                    break
        miner_info = MinerInfo(
            uid=agent_run_row.miner_uid,
            hotkey=agent_run_row.miner_hotkey,
            coldkey=getattr(miner_snapshot, "miner_coldkey", None),
            agent_name=getattr(miner_snapshot, "name", None) or f"miner {agent_run_row.miner_uid}",
            agent_image=getattr(miner_snapshot, "image_url", None) or "",
            github=getattr(miner_snapshot, "github_url", None) or "",
            is_sota=bool(getattr(miner_snapshot, "is_sota", False)),
        )
        return AgentEvaluationRun(
            agent_run_id=agent_run_row.agent_run_id,
            validator_round_id=agent_run_row.validator_round_id,
            miner_uid=agent_run_row.miner_uid,
            miner_hotkey=agent_run_row.miner_hotkey,
            started_at=float(agent_run_row.started_at or 0.0),
            ended_at=agent_run_row.ended_at,
            elapsed_sec=agent_run_row.elapsed_sec,
            average_score=agent_run_row.average_score,
            average_execution_time=agent_run_row.average_execution_time,
            average_reward=agent_run_row.average_reward,
            total_tasks=int(agent_run_row.total_tasks or 0),
            success_tasks=int(agent_run_row.success_tasks or 0),
            failed_tasks=int(agent_run_row.failed_tasks or 0),
            metadata=dict(agent_run_row.meta or {}),
            is_reused=bool(getattr(agent_run_row, "is_reused", False)),
            reused_from_agent_run_id=getattr(agent_run_row, "reused_from_agent_run_id", None),
            zero_reason=getattr(agent_run_row, "zero_reason", None),
            miner_info=miner_info,
        )

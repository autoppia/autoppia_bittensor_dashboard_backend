from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

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
    AgentEvaluationRunWithDetails,
    EvaluationResult,
    Round,
    RoundWithDetails,
    Task,
    TaskSolution,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentRunContext:
    """In-memory representation of an agent evaluation run with its related data."""

    round: Round
    run: AgentEvaluationRun
    tasks: List[Task]
    task_solutions: List[TaskSolution]
    evaluation_results: List[EvaluationResult]


class RoundsService:
    """Read operations for rounds stored in the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_rounds(self, limit: int, skip: int) -> List[RoundWithDetails]:
        stmt = (
            select(RoundORM)
            .order_by(RoundORM.validator_round_id.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.scalars(stmt)
        rounds: List[RoundWithDetails] = []

        for round_row in result:
            try:
                round_model = self._deserialize_round(round_row)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize round %s from SQL: %s",
                    round_row.validator_round_id,
                    exc,
                )
                continue

            rounds.append(
                RoundWithDetails(
                    **round_model.model_dump(),
                    agent_evaluation_runs=[],
                )
            )

        return rounds

    async def get_round(self, validator_round_id: str) -> RoundWithDetails:
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.tasks),
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(RoundORM.validator_round_id == validator_round_id)
        )
        round_row = await self.session.scalar(stmt)
        if not round_row:
            raise ValueError(f"Round {validator_round_id} not found")

        round_base = self._deserialize_round(round_row)

        agent_runs = [
            self._convert_agent_run(run_row, parent_round_row=round_row)
            for run_row in round_row.agent_runs
        ]

        return RoundWithDetails(
            **round_base.model_dump(),
            agent_evaluation_runs=agent_runs,
        )

    async def list_agent_runs(
        self,
        validator_round_id: str,
        limit: int,
        skip: int,
        include_details: bool = True,
    ) -> List[AgentEvaluationRunWithDetails]:
        stmt = (
            select(AgentEvaluationRunORM)
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
            .order_by(AgentEvaluationRunORM.id.desc())
            .offset(skip)
            .limit(limit)
        )

        if include_details:
            stmt = stmt.options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )

        result = await self.session.scalars(stmt)
        return [
            self._convert_agent_run(run_row, include_details=include_details)
            for run_row in result
        ]

    async def get_agent_run(self, agent_run_id: str) -> AgentEvaluationRunWithDetails:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        )
        run_row = await self.session.scalar(stmt)
        if not run_row:
            raise ValueError(f"Agent run {agent_run_id} not found")
        return self._convert_agent_run(run_row, include_details=True)

    async def list_agent_run_contexts(
        self,
        validator_round_id: Optional[str] = None,
        limit: int = 100,
        skip: int = 0,
    ) -> List[AgentRunContext]:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .order_by(AgentEvaluationRunORM.id.desc())
            .offset(skip)
            .limit(limit)
        )

        if validator_round_id:
            stmt = stmt.where(
                AgentEvaluationRunORM.validator_round_id == validator_round_id
            )

        result = await self.session.scalars(stmt)
        return [self._build_agent_run_context(run_row) for run_row in result]

    async def get_agent_run_context(self, agent_run_id: str) -> AgentRunContext:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.tasks),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        )
        run_row = await self.session.scalar(stmt)
        if not run_row:
            raise ValueError(f"Agent run {agent_run_id} not found")
        return self._build_agent_run_context(run_row)

    def _convert_agent_run(
        self,
        run_row: AgentEvaluationRunORM,
        include_details: bool = True,
        parent_round_row: Optional[RoundORM] = None,
    ) -> AgentEvaluationRunWithDetails:
        context = self._build_agent_run_context(
            run_row, parent_round_row=parent_round_row
        )

        tasks = context.tasks if include_details else []
        task_solutions = context.task_solutions if include_details else []
        evaluation_results = context.evaluation_results if include_details else []

        return AgentEvaluationRunWithDetails(
            **context.run.model_dump(),
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results,
        )

    def _build_agent_run_context(
        self,
        run_row: AgentEvaluationRunORM,
        parent_round_row: Optional[RoundORM] = None,
    ) -> AgentRunContext:
        round_row = parent_round_row or run_row.round
        if round_row is None:
            raise ValueError(
                f"Agent run {run_row.agent_run_id} is missing round relationship"
            )

        round_model = self._deserialize_round(round_row)
        agent_run_model = self._deserialize_agent_run(run_row)
        tasks = self._convert_tasks(run_row.tasks)
        task_solutions = self._convert_task_solutions(run_row.task_solutions)
        evaluation_results = self._convert_evaluations(run_row.evaluation_results)

        return AgentRunContext(
            round=round_model,
            run=agent_run_model,
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results,
        )

    def _deserialize_round(self, round_row: RoundORM) -> Round:
        payload = dict(round_row.data or {})
        payload.setdefault("validator_round_id", round_row.validator_round_id)
        return Round(**payload)

    def _deserialize_agent_run(self, run_row: AgentEvaluationRunORM) -> AgentEvaluationRun:
        payload = dict(run_row.data or {})
        payload.setdefault("agent_run_id", run_row.agent_run_id)
        payload.setdefault("validator_round_id", run_row.validator_round_id)
        payload.setdefault("validator_uid", run_row.validator_uid)
        payload.setdefault("miner_uid", run_row.miner_uid)
        payload.setdefault("is_sota", run_row.is_sota)
        return AgentEvaluationRun(**payload)

    @staticmethod
    def _convert_tasks(task_rows: List[TaskORM]) -> List[Task]:
        tasks: List[Task] = []
        for task_row in task_rows:
            data = dict(task_row.data or {})
            data.setdefault("task_id", task_row.task_id)
            data.setdefault("validator_round_id", task_row.validator_round_id)
            data.setdefault("agent_run_id", task_row.agent_run_id)
            try:
                tasks.append(Task(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to deserialize task %s: %s", task_row.task_id, exc)
        return tasks

    @staticmethod
    def _convert_task_solutions(
        solution_rows: List[TaskSolutionORM],
    ) -> List[TaskSolution]:
        solutions: List[TaskSolution] = []
        for solution_row in solution_rows:
            data = dict(solution_row.data or {})
            data.setdefault("solution_id", solution_row.solution_id)
            data.setdefault("task_id", solution_row.task_id)
            data.setdefault("agent_run_id", solution_row.agent_run_id)
            data.setdefault("validator_round_id", solution_row.validator_round_id)
            data.setdefault("validator_uid", solution_row.validator_uid)
            data.setdefault("miner_uid", solution_row.miner_uid)
            try:
                solutions.append(TaskSolution(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize task solution %s: %s",
                    solution_row.solution_id,
                    exc,
                )
        return solutions

    @staticmethod
    def _convert_evaluations(
        evaluation_rows: List[EvaluationResultORM],
    ) -> List[EvaluationResult]:
        evaluations: List[EvaluationResult] = []
        for evaluation_row in evaluation_rows:
            data = dict(evaluation_row.data or {})
            data.setdefault("evaluation_id", evaluation_row.evaluation_id)
            data.setdefault("task_id", evaluation_row.task_id)
            data.setdefault("task_solution_id", evaluation_row.task_solution_id)
            data.setdefault("agent_run_id", evaluation_row.agent_run_id)
            data.setdefault("validator_round_id", evaluation_row.validator_round_id)
            data.setdefault("validator_uid", evaluation_row.validator_uid)
            data.setdefault("miner_uid", evaluation_row.miner_uid)
            try:
                evaluations.append(EvaluationResult(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize evaluation result %s: %s",
                    evaluation_row.evaluation_id,
                    exc,
                )
        return evaluations

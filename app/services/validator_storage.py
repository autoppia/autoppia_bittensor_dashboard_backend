from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    ValidatorRoundSubmissionRequest,
    Task,
    TaskSolution,
)


@dataclass
class PersistenceResult:
    validator_uid: int
    saved_entities: Dict[str, List[str] | str]


class RoundConflictError(ValueError):
    """Raised when a validator attempts to register the same round twice."""

    pass


class RoundPersistenceService:
    """Handle persisting validator submissions into the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_round(self, validator_round_id: str) -> Optional[RoundORM]:
        """Fetch a persisted round by its validator_round_id."""
        stmt = select(RoundORM).where(RoundORM.validator_round_id == validator_round_id)
        return await self.session.scalar(stmt)

    async def ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: int,
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """
        Guard against duplicate round numbers for the same validator.

        Raises:
            RoundConflictError: When another validator round with the same logical round number exists.
        """
        if round_number is None:
            return

        stmt = select(RoundORM).where(RoundORM.validator_uid == validator_uid)
        if exclude_round_id is not None:
            stmt = stmt.where(RoundORM.validator_round_id != exclude_round_id)

        existing_rounds = await self.session.scalars(stmt)
        for round_row in existing_rounds:
            existing_round_number = self._extract_round_number(round_row.data)
            if existing_round_number == round_number:
                raise RoundConflictError(
                    f"Validator {validator_uid} already has a round with number {round_number}"
                )

    async def ensure_round(
        self, round_model: ValidatorRound, agent_runs: Optional[List[AgentEvaluationRun]] = None
    ) -> tuple[RoundORM, int]:
        """Create or update a round and return its row and validator UID."""
        validator_uid = self._derive_validator_uid(round_model, agent_runs or [])
        round_row = await self._upsert_round(round_model, validator_uid)
        await self.session.flush()
        return round_row, validator_uid

    async def upsert_agent_run_entry(self, round_row: RoundORM, agent_run: AgentEvaluationRun) -> AgentEvaluationRunORM:
        """Persist a single agent run."""
        await self._upsert_agent_runs(round_row, [agent_run])
        await self.session.flush()
        stmt = select(AgentEvaluationRunORM).where(
            AgentEvaluationRunORM.agent_run_id == agent_run.agent_run_id
        )
        result = await self.session.scalar(stmt)
        assert result is not None  # Safety: should exist after upsert
        return result

    async def get_agent_run(self, agent_run_id: str) -> Optional[AgentEvaluationRunORM]:
        """Fetch an agent run by its identifier."""
        stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        return await self.session.scalar(stmt)

    async def upsert_task_entry(self, task: Task) -> TaskORM:
        """Persist a single task."""
        await self._upsert_tasks([task])
        await self.session.flush()
        stmt = select(TaskORM).where(TaskORM.task_id == task.task_id)
        result = await self.session.scalar(stmt)
        assert result is not None
        return result

    async def upsert_task_solution_entry(self, solution: TaskSolution) -> TaskSolutionORM:
        """Persist a single task solution."""
        await self._upsert_task_solutions([solution])
        await self.session.flush()
        stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution.solution_id)
        result = await self.session.scalar(stmt)
        assert result is not None
        return result

    async def upsert_evaluation_entry(self, evaluation: EvaluationResult) -> EvaluationResultORM:
        """Persist a single evaluation result."""
        await self._upsert_evaluation_results([evaluation])
        await self.session.flush()
        stmt = select(EvaluationResultORM).where(
            EvaluationResultORM.evaluation_id == evaluation.evaluation_id
        )
        result = await self.session.scalar(stmt)
        assert result is not None
        return result

    async def update_round_fields(self, validator_round_id: str, **fields: Any) -> RoundORM:
        """Patch JSON payload for an existing round."""
        round_row = await self.get_round(validator_round_id)
        if round_row is None:
            raise ValueError(f"Round {validator_round_id} not found")

        data = dict(round_row.data)
        data.update(fields)
        round_row.data = data
        return round_row

    async def upsert_round_submission(self, payload: ValidatorRoundSubmissionRequest) -> PersistenceResult:
        """Persist the entire round submission payload."""
        round_model = payload.round
        agent_runs = payload.agent_evaluation_runs

        validator_uid = self._derive_validator_uid(round_model, agent_runs)

        round_row = await self._upsert_round(round_model, validator_uid)
        await self.session.flush()

        await self._upsert_agent_runs(round_row, agent_runs)
        await self.session.flush()

        await self._upsert_tasks(payload.tasks)
        await self.session.flush()

        await self._upsert_task_solutions(payload.task_solutions)
        await self.session.flush()

        await self._upsert_evaluation_results(payload.evaluation_results)

        saved_entities: Dict[str, List[str] | str] = {
            "round": round_row.validator_round_id,
            "agent_evaluation_runs": [run.agent_run_id for run in agent_runs],
            "tasks": [task.task_id for task in payload.tasks],
            "task_solutions": [solution.solution_id for solution in payload.task_solutions],
            "evaluation_results": [result.evaluation_id for result in payload.evaluation_results],
        }

        return PersistenceResult(validator_uid=validator_uid, saved_entities=saved_entities)

    async def _upsert_round(self, model: ValidatorRound, validator_uid: Optional[int]) -> RoundORM:
        stmt = select(RoundORM).where(RoundORM.validator_round_id == model.validator_round_id)
        existing = await self.session.scalar(stmt)

        data = model.model_dump(mode="json", exclude_none=True)

        if existing:
            target_validator_uid = validator_uid if validator_uid is not None else existing.validator_uid
            if model.round_number is not None and target_validator_uid is not None:
                await self.ensure_unique_round_number(
                    target_validator_uid,
                    model.round_number,
                    exclude_round_id=existing.validator_round_id,
                )
            existing.validator_uid = validator_uid
            existing.data = data
            return existing

        if validator_uid is not None and model.round_number is not None:
            await self.ensure_unique_round_number(validator_uid, model.round_number)

        round_row = RoundORM(
            validator_round_id=model.validator_round_id,
            validator_uid=validator_uid,
            data=data,
        )
        self.session.add(round_row)
        return round_row

    async def _upsert_agent_runs(self, round_row: RoundORM, runs: List[AgentEvaluationRun]) -> None:
        for agent_run in runs:
            stmt = select(AgentEvaluationRunORM).where(
                AgentEvaluationRunORM.agent_run_id == agent_run.agent_run_id
            )
            existing = await self.session.scalar(stmt)

            data = agent_run.model_dump(mode="json", exclude_none=True)

            if existing:
                existing.round_id = round_row.id
                existing.validator_round_id = agent_run.validator_round_id
                existing.validator_uid = agent_run.validator_uid
                existing.miner_uid = agent_run.miner_uid
                existing.is_sota = agent_run.is_sota
                existing.data = data
            else:
                self.session.add(
                    AgentEvaluationRunORM(
                        agent_run_id=agent_run.agent_run_id,
                        round_id=round_row.id,
                        validator_round_id=agent_run.validator_round_id,
                        validator_uid=agent_run.validator_uid,
                        miner_uid=agent_run.miner_uid,
                        is_sota=agent_run.is_sota,
                        data=data,
                    )
                )

    async def _upsert_tasks(self, tasks: List[Task]) -> None:
        for task in tasks:
            stmt = select(TaskORM).where(TaskORM.task_id == task.task_id)
            existing = await self.session.scalar(stmt)

            data = task.model_dump(mode="json", exclude_none=True)

            if existing:
                existing.validator_round_id = task.validator_round_id
                existing.data = data
            else:
                self.session.add(
                    TaskORM(
                        task_id=task.task_id,
                        validator_round_id=task.validator_round_id,
                        data=data,
                    )
                )

    async def _upsert_task_solutions(self, solutions: List[TaskSolution]) -> None:
        for solution in solutions:
            stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution.solution_id)
            existing = await self.session.scalar(stmt)

            data = solution.nested_model_dump(mode="json", exclude_none=True)

            if existing:
                existing.task_id = solution.task_id
                existing.agent_run_id = solution.agent_run_id
                existing.validator_round_id = solution.validator_round_id
                existing.validator_uid = solution.validator_uid
                existing.miner_uid = solution.miner_uid
                existing.data = data
            else:
                self.session.add(
                    TaskSolutionORM(
                        solution_id=solution.solution_id,
                        task_id=solution.task_id,
                        agent_run_id=solution.agent_run_id,
                        validator_round_id=solution.validator_round_id,
                        validator_uid=solution.validator_uid,
                        miner_uid=solution.miner_uid,
                        data=data,
                    )
                )

    async def _upsert_evaluation_results(self, results: List[EvaluationResult]) -> None:
        for evaluation in results:
            stmt = select(EvaluationResultORM).where(
                EvaluationResultORM.evaluation_id == evaluation.evaluation_id
            )
            existing = await self.session.scalar(stmt)

            data = evaluation.model_dump(mode="json", exclude_none=True)

            if existing:
                existing.task_id = evaluation.task_id
                existing.task_solution_id = evaluation.task_solution_id
                existing.agent_run_id = evaluation.agent_run_id
                existing.validator_round_id = evaluation.validator_round_id
                existing.validator_uid = evaluation.validator_uid
                existing.miner_uid = evaluation.miner_uid
                existing.data = data
            else:
                self.session.add(
                    EvaluationResultORM(
                        evaluation_id=evaluation.evaluation_id,
                        task_id=evaluation.task_id,
                        task_solution_id=evaluation.task_solution_id,
                        agent_run_id=evaluation.agent_run_id,
                        validator_round_id=evaluation.validator_round_id,
                        validator_uid=evaluation.validator_uid,
                        miner_uid=evaluation.miner_uid,
                        data=data,
                    )
                )

    @staticmethod
    def _derive_validator_uid(round_model: ValidatorRound, agent_runs: List[AgentEvaluationRun]) -> int:
        if round_model.validator_info:
            return round_model.validator_info.uid

        for run in agent_runs:
            if run.validator_uid is not None:
                return run.validator_uid

        raise ValueError("Unable to determine validator UID from submission payload")

    @staticmethod
    def _extract_round_number(data: Dict[str, Any] | None) -> Optional[int]:
        if not data:
            return None
        for key in ("round_number", "roundNumber", "round"):
            value = data.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

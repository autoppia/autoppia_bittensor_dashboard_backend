from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.services.round_calc import compute_boundaries_for_round

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    EvaluationResultORM,
    MinerORM,
    TaskORM,
    TaskSolutionORM,
    ValidatorORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundValidatorORM,
)
from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    EvaluationResult,
    Miner,
    ValidatorRoundMiner,
    Task,
    TaskSolution,
    Validator,
    ValidatorRound,
    ValidatorRoundSubmissionRequest,
    ValidatorRoundValidator,
)


@dataclass
class PersistenceResult:
    validator_uid: int
    saved_entities: Dict[str, Any]


class RoundConflictError(ValueError):
    """Raised when a validator attempts to register the same round twice."""


class DuplicateIdentifierError(ValueError):
    """Raised when an identifier that must be unique already exists."""


def _non_empty_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return value or {}


def _non_empty_list(value: Optional[List[Any]]) -> List[Any]:
    return value or []


def _action_dump(actions: Iterable[Any]) -> List[Dict[str, Any]]:
    dumped: List[Dict[str, Any]] = []
    for action in actions:
        if hasattr(action, "model_dump"):
            dumped.append(action.model_dump(mode="json", exclude_none=True))
        else:
            dumped.append(dict(action))
    return dumped


def _test_results_dump(matrix: Iterable[Iterable[Any]]) -> List[List[Dict[str, Any]]]:
    serialised: List[List[Dict[str, Any]]] = []
    for row in matrix:
        row_dump: List[Dict[str, Any]] = []
        for item in row:
            if hasattr(item, "model_dump"):
                row_dump.append(item.model_dump(mode="json", exclude_none=True))
            else:
                row_dump.append(dict(item))
        serialised.append(row_dump)
    return serialised


def _optional_dump(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


class ValidatorRoundPersistenceService:
    """Handle persisting validator round submissions into the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Public API used by endpoints
    # ------------------------------------------------------------------

    async def start_round(
        self,
        *,
        validator_identity: Validator,
        validator_round: ValidatorRound,
        validator_snapshot: ValidatorRoundValidator,
    ) -> ValidatorRoundORM:
        """Create a new validator round and store the initial snapshot."""
        await self._ensure_unique_round_number(
            validator_round.validator_uid,
            validator_round.round_number,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        if existing_round is not None:
            raise DuplicateIdentifierError(
                f"validator_round_id {validator_round.validator_round_id} is already registered"
            )

        validator_row = await self._upsert_validator_identity(validator_identity)

        round_row = ValidatorRoundORM(
            **self._validator_round_kwargs(validator_round, validator_row.id)
        )
        self.session.add(round_row)
        await self.session.flush()

        snapshot_row = await self._upsert_validator_snapshot(
            round_row, validator_snapshot, validator_row.id
        )

        return round_row

    async def add_tasks(
        self,
        validator_round_id: str,
        tasks: List[Task],
        *,
        allow_existing: bool = False,
    ) -> int:
        """Persist or update tasks associated with a validator round."""
        round_row = await self._ensure_round_exists(validator_round_id)
        count = 0
        for task in tasks:
            stmt = select(TaskORM).where(TaskORM.task_id == task.task_id)
            existing = await self.session.scalar(stmt)

            kwargs = self._task_kwargs(task)
            kwargs["validator_round_id"] = round_row.validator_round_id

            if existing:
                if existing.validator_round_id != validator_round_id:
                    raise DuplicateIdentifierError(
                        f"task_id {task.task_id} already belongs to validator_round {existing.validator_round_id}"
                    )
                if allow_existing:
                    continue
                raise DuplicateIdentifierError(
                    f"task_id {task.task_id} already exists for validator_round {validator_round_id}"
                )

            self.session.add(TaskORM(**kwargs))
            count += 1
        return count

    async def start_agent_run(
        self,
        *,
        validator_round_id: str,
        agent_run: AgentEvaluationRun,
        miner_identity: Miner,
        miner_snapshot: ValidatorRoundMiner,
    ) -> AgentEvaluationRunORM:
        """Persist the beginning of an agent evaluation run."""
        round_row = await self._ensure_round_exists(validator_round_id)

        existing_run = await self._get_agent_run_row(agent_run.agent_run_id)
        if existing_run:
            raise DuplicateIdentifierError(
                f"agent_run_id {agent_run.agent_run_id} is already registered"
            )

        miner_row = await self._upsert_miner_identity(miner_identity)
        await self._upsert_miner_snapshot(round_row, miner_snapshot, miner_row.id)

        kwargs = self._agent_run_kwargs(
            agent_run, validator_id=round_row.validator_id, miner_id=miner_row.id
        )

        row = AgentEvaluationRunORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_round_by_validator_and_number(
        self,
        *,
        validator_uid: int,
        round_number: int,
    ) -> Optional[ValidatorRoundORM]:
        """Fetch an existing round row by (validator_uid, round_number) without modifying DB state."""
        stmt = select(ValidatorRoundORM).where(
            ValidatorRoundORM.validator_uid == validator_uid,
            ValidatorRoundORM.round_number == round_number,
        )
        return await self.session.scalar(stmt)

    async def add_evaluation(
        self,
        *,
        validator_round_id: str,
        agent_run_id: str,
        task: Task,
        task_solution: TaskSolution,
        evaluation: Evaluation,
        evaluation_result: EvaluationResult,
    ) -> None:
        """Persist evaluation data (task, solution, evaluation record, and artefact)."""
        round_row = await self._ensure_round_exists(validator_round_id)
        agent_run_row = await self._get_agent_run_row(agent_run_id)
        if not agent_run_row:
            raise ValueError(f"Agent run {agent_run_id} has not been registered yet")
        if agent_run_row.validator_round_id != validator_round_id:
            raise ValueError(
                f"Agent run {agent_run_id} is not associated with validator_round {validator_round_id}"
            )

        if task.validator_round_id != validator_round_id:
            raise ValueError(
                f"Task {task.task_id} does not belong to validator_round {validator_round_id}"
            )

        existing_task = await self._get_task_row(task.task_id)
        if existing_task is None:
            raise ValueError(
                f"Task {task.task_id} has not been registered for validator_round {validator_round_id}"
            )
        if existing_task.validator_round_id != validator_round_id:
            raise ValueError(
                f"Task {task.task_id} is registered under validator_round {existing_task.validator_round_id}, "
                f"not {validator_round_id}"
            )

        miner_id = await self._resolve_miner_identity_id(
            task_solution.miner_uid,
            task_solution.miner_hotkey,
        )

        # Task solution
        solution_kwargs = self._task_solution_kwargs(task_solution, miner_id=miner_id)
        stmt_solution = select(TaskSolutionORM).where(
            TaskSolutionORM.solution_id == task_solution.solution_id
        )
        existing_solution = await self.session.scalar(stmt_solution)
        if existing_solution:
            raise DuplicateIdentifierError(
                f"task_solution_id {task_solution.solution_id} is already registered"
            )
        solution_row = TaskSolutionORM(**solution_kwargs)
        self.session.add(solution_row)

        await self.session.flush()

        evaluation_kwargs = self._evaluation_kwargs(evaluation, miner_id=miner_id)
        stmt_evaluation = select(EvaluationORM).where(
            EvaluationORM.evaluation_id == evaluation.evaluation_id
        )
        existing_evaluation = await self.session.scalar(stmt_evaluation)
        if existing_evaluation:
            raise DuplicateIdentifierError(
                f"evaluation_id {evaluation.evaluation_id} is already registered"
            )
        evaluation_row = EvaluationORM(**evaluation_kwargs)
        self.session.add(evaluation_row)

        result_kwargs = self._evaluation_result_kwargs(
            evaluation_row,
            evaluation_result,
            miner_id=miner_id,
        )
        stmt_result = select(EvaluationResultORM).where(
            EvaluationResultORM.result_id == evaluation_result.result_id
        )
        existing_result = await self.session.scalar(stmt_result)
        if existing_result:
            raise DuplicateIdentifierError(
                f"evaluation_result_id {evaluation_result.result_id} is already registered"
            )
        self.session.add(EvaluationResultORM(**result_kwargs))
        # Reaching here means fresh insert path; idempotency is handled at endpoint by
        # detecting duplicates before writes. This method intentionally raises
        # DuplicateIdentifierError when any of the IDs already exist.

    async def upsert_evaluation_bundle(
        self,
        *,
        validator_round_id: str,
        agent_run_id: str,
        task: Task,
        task_solution: TaskSolution,
        evaluation: Evaluation,
        evaluation_result: EvaluationResult,
    ) -> None:
        """
        Insert any missing records for the (solution, evaluation, result) bundle and
        validate consistency for any existing ones. This is an idempotent helper that
        only writes what is missing and raises when existing rows conflict
        (e.g., belong to a different round/run).
        """
        # Ensure round and agent_run belong together
        round_row = await self._ensure_round_exists(validator_round_id)
        agent_run_row = await self._get_agent_run_row(agent_run_id)
        if not agent_run_row:
            raise ValueError(f"Agent run {agent_run_id} has not been registered yet")
        if agent_run_row.validator_round_id != validator_round_id:
            raise ValueError(
                f"Agent run {agent_run_id} is not associated with validator_round {validator_round_id}"
            )

        # Ensure task exists and belongs to round
        if task.validator_round_id != validator_round_id:
            raise ValueError(
                f"Task {task.task_id} does not belong to validator_round {validator_round_id}"
            )
        existing_task = await self._get_task_row(task.task_id)
        if existing_task is None:
            raise ValueError(
                f"Task {task.task_id} has not been registered for validator_round {validator_round_id}"
            )
        if existing_task.validator_round_id != validator_round_id:
            raise ValueError(
                f"Task {task.task_id} is registered under validator_round {existing_task.validator_round_id}, not {validator_round_id}"
            )

        miner_id = await self._resolve_miner_identity_id(
            task_solution.miner_uid,
            task_solution.miner_hotkey,
        )

        # Task solution upsert/validate
        existing_solution = await self.get_task_solution_row(task_solution.solution_id)
        if existing_solution is not None:
            if (
                existing_solution.validator_round_id != validator_round_id
                or existing_solution.agent_run_id != agent_run_id
                or existing_solution.task_id != task.task_id
            ):
                raise DuplicateIdentifierError(
                    f"task_solution_id {task_solution.solution_id} already belongs to a different context"
                )
            solution_row = existing_solution
        else:
            solution_kwargs = self._task_solution_kwargs(
                task_solution, miner_id=miner_id
            )
            solution_row = TaskSolutionORM(**solution_kwargs)
            self.session.add(solution_row)
            
            # Handle race condition: another request may have inserted between SELECT and INSERT
            try:
                await self.session.flush()
            except Exception as e:
                from sqlalchemy.exc import IntegrityError
                if isinstance(e, IntegrityError) and "uq_solution_id" in str(e):
                    # Race condition: solution was inserted by concurrent request
                    await self.session.rollback()
                    # Reload the existing solution
                    existing_solution = await self.get_task_solution_row(task_solution.solution_id)
                    if existing_solution:
                        solution_row = existing_solution
                    else:
                        raise  # Unexpected error
                else:
                    raise

        # Evaluation upsert/validate
        existing_eval = await self.get_evaluation_row(evaluation.evaluation_id)
        if existing_eval is not None:
            if (
                existing_eval.validator_round_id != validator_round_id
                or existing_eval.agent_run_id != agent_run_id
                or existing_eval.task_id != task.task_id
                or existing_eval.task_solution_id != task_solution.solution_id
            ):
                raise DuplicateIdentifierError(
                    f"evaluation_id {evaluation.evaluation_id} already belongs to a different context"
                )
            evaluation_row = existing_eval
        else:
            evaluation_kwargs = self._evaluation_kwargs(evaluation, miner_id=miner_id)
            evaluation_row = EvaluationORM(**evaluation_kwargs)
            self.session.add(evaluation_row)
            
            # Handle race condition for evaluation
            try:
                await self.session.flush()
            except Exception as e:
                from sqlalchemy.exc import IntegrityError
                if isinstance(e, IntegrityError) and "uq_evaluation_id" in str(e):
                    await self.session.rollback()
                    existing_eval = await self.get_evaluation_row(evaluation.evaluation_id)
                    if existing_eval:
                        evaluation_row = existing_eval
                    else:
                        raise
                else:
                    raise

        # Evaluation result upsert/validate
        existing_result = await self.get_evaluation_result_row(
            evaluation_result.result_id
        )
        if existing_result is not None:
            if (
                existing_result.validator_round_id != validator_round_id
                or existing_result.agent_run_id != agent_run_id
                or existing_result.task_id != task.task_id
                or existing_result.task_solution_id != task_solution.solution_id
                or existing_result.evaluation_id != evaluation_row.evaluation_id
            ):
                raise DuplicateIdentifierError(
                    f"evaluation_result_id {evaluation_result.result_id} already belongs to a different context"
                )
        else:
            # We may need flush to ensure evaluation_row has PK for FK relations
            await self.session.flush()
            result_kwargs = self._evaluation_result_kwargs(
                evaluation_row,
                evaluation_result,
                miner_id=miner_id,
            )
            result_orm = EvaluationResultORM(**result_kwargs)
            self.session.add(result_orm)
            
            # Handle race condition for evaluation_result
            try:
                await self.session.flush()
            except Exception as e:
                from sqlalchemy.exc import IntegrityError
                if isinstance(e, IntegrityError) and "uq_result_id" in str(e):
                    # Race condition handled - result already exists
                    await self.session.rollback()
                    # Continue without error (result is already saved)
                else:
                    raise

    # ──────────────────────────────────────────────────────────────────────
    # Read-only helpers for idempotency checks
    # ──────────────────────────────────────────────────────────────────────

    async def get_task_solution_row(
        self, solution_id: str
    ) -> Optional[TaskSolutionORM]:
        stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution_id)
        return await self.session.scalar(stmt)

    async def get_evaluation_row(self, evaluation_id: str) -> Optional[EvaluationORM]:
        stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
        return await self.session.scalar(stmt)

    async def get_evaluation_result_row(
        self, result_id: str
    ) -> Optional[EvaluationResultORM]:
        stmt = select(EvaluationResultORM).where(
            EvaluationResultORM.result_id == result_id
        )
        return await self.session.scalar(stmt)

    async def finish_round(
        self,
        *,
        validator_round_id: str,
        status: str,
        winners: List[Dict[str, Any]],
        winner_scores: List[float],
        weights: Dict[str, float],
        ended_at: float,
        summary: Optional[Dict[str, int]],
        agent_runs: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Mark a validator round as completed."""
        round_row = await self._ensure_round_exists(validator_round_id)
        # Ensure start/end epoch are populated even when testing overrides bypassed chain-boundary fill
        try:
            round_number = int(getattr(round_row, "round_number", 0) or 0)
            if (
                getattr(round_row, "start_epoch", None) is None
                or getattr(round_row, "end_epoch", None) is None
            ):
                bounds = compute_boundaries_for_round(round_number)
                if getattr(round_row, "start_epoch", None) is None:
                    round_row.start_epoch = int(bounds.start_epoch)
                if getattr(round_row, "end_epoch", None) is None:
                    round_row.end_epoch = int(bounds.end_epoch)
        except Exception:
            # If boundary computation fails, proceed without blocking finish
            pass
        
        # Normalize status to match ValidatorRound literal type
        normalized_status = status.lower()
        if normalized_status in {"completed", "complete"}:
            normalized_status = "finished"
        elif normalized_status not in {"active", "finished", "pending", "evaluating_finished"}:
            normalized_status = "finished"
        
        round_row.status = normalized_status
        round_row.meta = {
            **round_row.meta,
            "winners": winners,
            "winner_scores": winner_scores,
            "weights": weights,
        }
        round_row.n_winners = len(winners)
        round_row.ended_at = ended_at
        if summary is not None:
            round_row.summary = summary

        rank_map: Dict[str, Optional[int]] = {}
        weight_map: Dict[str, Optional[float]] = {}
        if agent_runs:
            for agent_run_data in agent_runs:
                agent_run_id = agent_run_data.get("agent_run_id")
                if not agent_run_id:
                    continue
                rank_map[agent_run_id] = agent_run_data.get("rank")
                weight_map[agent_run_id] = agent_run_data.get("weight")

        stmt_runs = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
        )
        run_rows_result = await self.session.scalars(stmt_runs)
        run_rows = list(run_rows_result)

        for run_row in run_rows:
            if ended_at is not None:
                run_row.ended_at = ended_at
                if run_row.started_at is not None:
                    elapsed = max(0.0, float(ended_at) - float(run_row.started_at))
                    run_row.elapsed_sec = elapsed

            metrics = self._compute_agent_run_stats(run_row)
            run_row.total_tasks = metrics["total_tasks"]
            run_row.completed_tasks = metrics["completed_tasks"]
            run_row.failed_tasks = metrics["failed_tasks"]
            run_row.average_score = metrics["average_score"]
            run_row.average_execution_time = metrics["average_execution_time"]
            run_row.total_reward = metrics["total_reward"]
            run_row.average_reward = metrics["average_reward"]

            agent_run_id = run_row.agent_run_id
            rank_value = rank_map.get(agent_run_id)
            if rank_value is not None:
                try:
                    run_row.rank = int(rank_value)
                except (TypeError, ValueError):
                    run_row.rank = run_row.rank
            weight_value = weight_map.get(agent_run_id)
            if weight_value is not None:
                try:
                    run_row.weight = float(weight_value)
                except (TypeError, ValueError):
                    run_row.weight = run_row.weight

    async def submit_round(
        self, payload: ValidatorRoundSubmissionRequest
    ) -> PersistenceResult:
        """Persist the entire round submission payload."""
        self._assert_unique_payload(payload)
        # Identities
        for identity in payload.validator_identities:
            await self._upsert_validator_identity(identity)
        for identity in payload.miner_identities:
            await self._upsert_miner_identity(identity)

        validator_round = payload.validator_round
        await self._ensure_unique_round_number(
            validator_round.validator_uid,
            validator_round.round_number,
            exclude_round_id=None,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        validator_id = self._find_validator_id(
            validator_round.validator_uid, validator_round.validator_hotkey
        )
        round_kwargs = self._validator_round_kwargs(
            validator_round, validator_id=validator_id
        )

        if existing_round:
            for key, value in round_kwargs.items():
                setattr(existing_round, key, value)
            round_row = existing_round
        else:
            round_row = ValidatorRoundORM(**round_kwargs)
            self.session.add(round_row)
        await self.session.flush()

        # Snapshots
        validator_snapshot_ids: List[int] = []
        for snapshot in payload.validator_snapshots:
            row = await self._upsert_validator_snapshot(
                round_row,
                snapshot,
                self._find_validator_id(
                    snapshot.validator_uid, snapshot.validator_hotkey
                ),
            )
            validator_snapshot_ids.append(row.id)

        miner_snapshot_ids: List[int] = []
        for snapshot in payload.miner_snapshots:
            miner_id = await self._resolve_miner_identity_id(
                snapshot.miner_uid,
                snapshot.miner_hotkey,
            )
            row = await self._upsert_miner_snapshot(round_row, snapshot, miner_id)
            miner_snapshot_ids.append(row.id)

        # Agent runs
        agent_run_ids: List[str] = []
        for agent_run in payload.agent_evaluation_runs:
            miner_id = await self._resolve_miner_identity_id(
                agent_run.miner_uid,
                agent_run.miner_hotkey,
            )
            kwargs = self._agent_run_kwargs(
                agent_run,
                validator_id=round_row.validator_id,
                miner_id=miner_id,
            )
            stmt = select(AgentEvaluationRunORM).where(
                AgentEvaluationRunORM.agent_run_id == agent_run.agent_run_id
            )
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(
                    f"agent_run_id {agent_run.agent_run_id} is provided multiple times"
                )
            self.session.add(AgentEvaluationRunORM(**kwargs))
            agent_run_ids.append(agent_run.agent_run_id)

        # Tasks
        await self.add_tasks(round_row.validator_round_id, payload.tasks)
        task_ids = [task.task_id for task in payload.tasks]

        # Task solutions
        task_solution_ids: List[str] = []
        for solution in payload.task_solutions:
            miner_id = await self._resolve_miner_identity_id(
                solution.miner_uid,
                solution.miner_hotkey,
            )
            kwargs = self._task_solution_kwargs(solution, miner_id=miner_id)
            stmt = select(TaskSolutionORM).where(
                TaskSolutionORM.solution_id == solution.solution_id
            )
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(
                    f"task_solution_id {solution.solution_id} is provided multiple times"
                )
            self.session.add(TaskSolutionORM(**kwargs))
            task_solution_ids.append(solution.solution_id)

        # Evaluations
        evaluation_ids: List[str] = []
        evaluation_rows: Dict[str, EvaluationORM] = {}
        for evaluation in payload.evaluations:
            miner_id = await self._resolve_miner_identity_id(
                evaluation.miner_uid,
                evaluation.miner_hotkey,
            )
            kwargs = self._evaluation_kwargs(evaluation, miner_id=miner_id)
            stmt = select(EvaluationORM).where(
                EvaluationORM.evaluation_id == evaluation.evaluation_id
            )
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(
                    f"evaluation_id {evaluation.evaluation_id} is provided multiple times"
                )
            evaluation_row = EvaluationORM(**kwargs)
            self.session.add(evaluation_row)
            evaluation_ids.append(evaluation.evaluation_id)
            evaluation_rows[evaluation.evaluation_id] = evaluation_row

        await self.session.flush()

        # Evaluation results
        evaluation_result_ids: List[str] = []
        for result in payload.evaluation_results:
            miner_id = await self._resolve_miner_identity_id(
                result.miner_uid,
                None,
            )
            evaluation_row = evaluation_rows.get(result.evaluation_id)
            if evaluation_row is None:
                evaluation_row = await self.session.scalar(
                    select(EvaluationORM).where(
                        EvaluationORM.evaluation_id == result.evaluation_id
                    )
                )
                if evaluation_row is None:
                    raise ValueError(
                        f"Evaluation result {result.result_id} references unknown evaluation {result.evaluation_id}"
                    )
            kwargs = self._evaluation_result_kwargs(
                evaluation_row,
                result,
                miner_id=miner_id,
            )
            stmt = select(EvaluationResultORM).where(
                EvaluationResultORM.result_id == result.result_id
            )
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(
                    f"evaluation_result_id {result.result_id} is provided multiple times"
                )
            self.session.add(EvaluationResultORM(**kwargs))
            evaluation_result_ids.append(result.result_id)

        saved = {
            "validator_round": round_row.validator_round_id,
            "validator_snapshots": validator_snapshot_ids,
            "miner_snapshots": miner_snapshot_ids,
            "agent_evaluation_runs": agent_run_ids,
            "tasks": task_ids,
            "task_solutions": task_solution_ids,
            "evaluations": evaluation_ids,
            "evaluation_results": evaluation_result_ids,
        }
        return PersistenceResult(
            validator_uid=payload.validator_round.validator_uid,
            saved_entities=saved,
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _compute_agent_run_stats(
        self, run_row: AgentEvaluationRunORM
    ) -> Dict[str, Any]:
        task_solutions = list(getattr(run_row, "task_solutions", []) or [])
        evaluation_results = list(getattr(run_row, "evaluation_results", []) or [])

        task_ids = {solution.task_id for solution in task_solutions if solution.task_id}
        if not task_ids:
            task_ids = {
                result.task_id for result in evaluation_results if result.task_id
            }

        total_tasks = len(task_ids)
        if total_tasks == 0:
            total_tasks = run_row.total_tasks or len(evaluation_results)
        total_tasks = int(total_tasks or 0)

        scores: List[float] = []
        for result in evaluation_results:
            value = self._to_float(getattr(result, "final_score", None))
            if value is not None:
                scores.append(value)
        average_score = sum(scores) / len(scores) if scores else None

        completed_tasks = sum(1 for score in scores if score >= 0.5)
        if completed_tasks > total_tasks:
            completed_tasks = total_tasks
        failed_tasks = max(total_tasks - completed_tasks, 0)

        evaluation_times: List[float] = []
        for result in evaluation_results:
            value = self._to_float(getattr(result, "evaluation_time", None))
            if value is not None and value >= 0.0:
                evaluation_times.append(value)
        average_execution_time = (
            sum(evaluation_times) / len(evaluation_times) if evaluation_times else None
        )

        reward_values: List[float] = []
        reward_keys = (
            "reward",
            "total_reward",
            "final_reward",
            "wta_reward",
            "reward_value",
            "score_reward",
        )
        for result in evaluation_results:
            meta = getattr(result, "meta", {}) or {}
            reward_candidate: Any = None
            if isinstance(meta, dict):
                for key in reward_keys:
                    if key in meta:
                        reward_candidate = meta[key]
                        break
            if reward_candidate is None:
                reward_candidate = getattr(result, "raw_score", None)
            value = self._to_float(reward_candidate)
            if value is not None:
                reward_values.append(value)

        total_reward = sum(reward_values) if reward_values else None
        average_reward = (
            (total_reward / len(reward_values))
            if reward_values and total_reward is not None
            else None
        )

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "average_score": average_score,
            "average_execution_time": average_execution_time,
            "total_reward": total_reward,
            "average_reward": average_reward,
        }

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    async def _get_round_row(
        self, validator_round_id: str
    ) -> Optional[ValidatorRoundORM]:
        stmt = select(ValidatorRoundORM).where(
            ValidatorRoundORM.validator_round_id == validator_round_id
        )
        return await self.session.scalar(stmt)

    async def _get_agent_run_row(
        self, agent_run_id: str
    ) -> Optional[AgentEvaluationRunORM]:
        stmt = select(AgentEvaluationRunORM).where(
            AgentEvaluationRunORM.agent_run_id == agent_run_id
        )
        return await self.session.scalar(stmt)

    async def _get_task_row(self, task_id: str) -> Optional[TaskORM]:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        return await self.session.scalar(stmt)

    async def _ensure_round_exists(self, validator_round_id: str) -> ValidatorRoundORM:
        round_row = await self._get_round_row(validator_round_id)
        if not round_row:
            raise ValueError(f"Validator round {validator_round_id} not found")
        return round_row

    async def _ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        if round_number is None:
            return

        stmt = select(ValidatorRoundORM).where(
            ValidatorRoundORM.validator_uid == validator_uid,
            ValidatorRoundORM.round_number == round_number,
        )
        if exclude_round_id is not None:
            stmt = stmt.where(ValidatorRoundORM.validator_round_id != exclude_round_id)
        existing = await self.session.scalar(stmt)
        if existing:
            raise RoundConflictError(
                f"Validator {validator_uid} already has a round with number {round_number}"
            )

    async def ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """Public wrapper to guard against duplicate round numbers."""
        await self._ensure_unique_round_number(
            validator_uid, round_number, exclude_round_id=exclude_round_id
        )

    async def _upsert_validator_identity(self, identity: Validator) -> ValidatorORM:
        stmt = select(ValidatorORM).where(
            ValidatorORM.uid == identity.uid,
            ValidatorORM.hotkey == identity.hotkey,
        )
        existing = await self.session.scalar(stmt)
        if existing:
            existing.coldkey = identity.coldkey
            return existing

        row = ValidatorORM(
            uid=identity.uid,
            hotkey=identity.hotkey,
            coldkey=identity.coldkey,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _upsert_miner_identity(self, identity: Miner) -> MinerORM:
        stmt = select(MinerORM)
        if identity.uid is not None and identity.hotkey:
            stmt = stmt.where(
                MinerORM.uid == identity.uid, MinerORM.hotkey == identity.hotkey
            )
        else:
            raise ValueError("Miner identity must include uid and hotkey")

        existing = await self.session.scalar(stmt)
        if existing:
            existing.coldkey = identity.coldkey
            return existing

        row = MinerORM(
            uid=identity.uid,
            hotkey=identity.hotkey,
            coldkey=identity.coldkey,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _upsert_validator_snapshot(
        self,
        round_row: ValidatorRoundORM,
        snapshot: ValidatorRoundValidator,
        validator_id: Optional[int],
    ) -> ValidatorRoundValidatorORM:
        stmt = select(ValidatorRoundValidatorORM).where(
            ValidatorRoundValidatorORM.validator_round_id
            == round_row.validator_round_id,
            ValidatorRoundValidatorORM.validator_uid == snapshot.validator_uid,
            ValidatorRoundValidatorORM.validator_hotkey == snapshot.validator_hotkey,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "validator_id": validator_id,
            "validator_uid": snapshot.validator_uid,
            "validator_hotkey": snapshot.validator_hotkey,
            "name": snapshot.name,
            "stake": snapshot.stake,
            "vtrust": snapshot.vtrust,
            "image_url": snapshot.image_url,
            "version": snapshot.version,
        }
        if existing:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing
        row = ValidatorRoundValidatorORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def _upsert_miner_snapshot(
        self,
        round_row: ValidatorRoundORM,
        snapshot: ValidatorRoundMiner,
        miner_id: Optional[int],
    ) -> ValidatorRoundMinerORM:
        stmt = select(ValidatorRoundMinerORM).where(
            ValidatorRoundMinerORM.validator_round_id == round_row.validator_round_id,
            ValidatorRoundMinerORM.miner_uid == snapshot.miner_uid,
            ValidatorRoundMinerORM.miner_hotkey == snapshot.miner_hotkey,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "miner_id": miner_id,
            "miner_uid": snapshot.miner_uid,
            "miner_hotkey": snapshot.miner_hotkey,
            "miner_coldkey": snapshot.miner_coldkey,
            "agent_name": snapshot.agent_name,
            "image_url": snapshot.image_url,
            "github_url": snapshot.github_url,
            "description": snapshot.description,
            "is_sota": snapshot.is_sota,
            "first_seen_at": snapshot.first_seen_at,
            "last_seen_at": snapshot.last_seen_at,
        }
        if existing:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing
        row = ValidatorRoundMinerORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    def _validator_round_kwargs(
        self, model: ValidatorRound, validator_id: Optional[int]
    ) -> Dict[str, Any]:
        return {
            "validator_round_id": model.validator_round_id,
            "validator_id": validator_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "validator_coldkey": model.validator_coldkey,
            "round_number": model.round_number,
            "start_block": model.start_block,
            "end_block": model.end_block,
            "start_epoch": model.start_epoch,
            "end_epoch": model.end_epoch,
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "elapsed_sec": model.elapsed_sec,
            "max_epochs": model.max_epochs,
            "max_blocks": model.max_blocks,
            "n_tasks": model.n_tasks,
            "n_miners": model.n_miners,
            "n_winners": model.n_winners,
            "status": model.status,
            "average_score": model.average_score,
            "top_score": model.top_score,
            "summary": _non_empty_dict(model.summary),
            "meta": _non_empty_dict(model.metadata),
        }

    def _agent_run_kwargs(
        self,
        model: AgentEvaluationRun,
        *,
        validator_id: Optional[int],
        miner_id: Optional[int],
    ) -> Dict[str, Any]:
        return {
            "agent_run_id": model.agent_run_id,
            "validator_round_id": model.validator_round_id,
            "validator_id": validator_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "miner_id": miner_id,
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            "is_sota": model.is_sota,
            "version": model.version,
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "elapsed_sec": model.elapsed_sec,
            "average_score": model.average_score,
            "average_execution_time": model.average_execution_time,
            "average_reward": model.average_reward,
            "total_reward": model.total_reward,
            "total_tasks": model.total_tasks,
            "completed_tasks": model.completed_tasks,
            "failed_tasks": model.failed_tasks,
            "rank": model.rank,
            "weight": model.weight,
            "meta": _non_empty_dict(model.metadata),
        }

    def _task_kwargs(self, model: Task) -> Dict[str, Any]:
        return {
            "task_id": model.task_id,
            "validator_round_id": model.validator_round_id,
            "is_web_real": model.is_web_real,
            "web_project_id": model.web_project_id,
            "url": model.url,
            "prompt": model.prompt,
            "specifications": _non_empty_dict(model.specifications),
            "tests": [
                test.model_dump(mode="json", exclude_none=True) for test in model.tests
            ],
            "relevant_data": _non_empty_dict(model.relevant_data),
            "use_case": (
                model.use_case
                if isinstance(model.use_case, dict)
                else _optional_dump(model.use_case)
            ),
        }

    def _task_solution_kwargs(
        self,
        model: TaskSolution,
        *,
        miner_id: Optional[int],
    ) -> Dict[str, Any]:
        return {
            "solution_id": model.solution_id,
            "task_id": model.task_id,
            "agent_run_id": model.agent_run_id,
            "validator_round_id": model.validator_round_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            "miner_id": miner_id,
            "actions": _action_dump(model.actions),
            "web_agent_id": model.web_agent_id,
        }

    def _evaluation_kwargs(
        self,
        model: Evaluation,
        *,
        miner_id: Optional[int],
    ) -> Dict[str, Any]:
        summary = _non_empty_dict(model.summary)
        if "test_results" in summary:
            try:
                summary["test_results"] = _test_results_dump(summary["test_results"])
            except Exception:
                pass

        return {
            "evaluation_id": model.evaluation_id,
            "validator_round_id": model.validator_round_id,
            "task_id": model.task_id,
            "task_solution_id": model.task_solution_id,
            "agent_run_id": model.agent_run_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            "miner_id": miner_id,
            "final_score": model.final_score,
            "raw_score": model.raw_score,
            "evaluation_time": model.evaluation_time,
            "summary": summary,
        }

    def _evaluation_result_kwargs(
        self,
        evaluation_row: EvaluationORM,
        model: EvaluationResult,
        *,
        miner_id: Optional[int],
    ) -> Dict[str, Any]:
        return {
            "result_id": model.result_id,
            "evaluation_id": evaluation_row.evaluation_id,
            "validator_round_id": model.validator_round_id,
            "agent_run_id": model.agent_run_id,
            "task_id": model.task_id,
            "task_solution_id": model.task_solution_id,
            "validator_uid": model.validator_uid,
            "miner_uid": model.miner_uid,
            "miner_id": miner_id,
            "final_score": model.final_score,
            "test_results_matrix": _test_results_dump(model.test_results_matrix),
            "execution_history": list(model.execution_history),
            "feedback": _optional_dump(model.feedback),
            "web_agent_id": model.web_agent_id,
            "raw_score": model.raw_score,
            "evaluation_time": model.evaluation_time,
            "stats": _optional_dump(model.stats),
            "gif_recording": model.gif_recording,
            "meta": _non_empty_dict(model.metadata),
        }

    def _find_validator_id(self, uid: int, hotkey: str) -> Optional[int]:
        for instance in self.session.identity_map.values():
            if isinstance(instance, ValidatorORM):
                if instance.uid == uid and instance.hotkey == hotkey:
                    return instance.id
        return None

    async def _resolve_miner_identity_id(
        self,
        uid: Optional[int],
        hotkey: Optional[str],
    ) -> Optional[int]:
        if uid is None:
            return None

        stmt = select(MinerORM)
        if uid is not None:
            stmt = stmt.where(MinerORM.uid == uid)
            if hotkey:
                stmt = stmt.where(MinerORM.hotkey == hotkey)

        row = await self.session.scalar(stmt)
        if row:
            return row.id
        return None

    @staticmethod
    def _assert_unique(sequence: Iterable[str], name: str) -> None:
        seen: set[str] = set()
        for value in sequence:
            if value in seen:
                raise DuplicateIdentifierError(f"Duplicate {name}: {value}")
            seen.add(value)

    def _assert_unique_payload(self, payload: ValidatorRoundSubmissionRequest) -> None:
        self._assert_unique(
            [payload.validator_round.validator_round_id],
            "validator_round_id",
        )
        self._assert_unique(
            [run.agent_run_id for run in payload.agent_evaluation_runs],
            "agent_run_id",
        )
        self._assert_unique(
            [task.task_id for task in payload.tasks],
            "task_id",
        )
        self._assert_unique(
            [solution.solution_id for solution in payload.task_solutions],
            "task_solution_id",
        )
        self._assert_unique(
            [evaluation.evaluation_id for evaluation in payload.evaluations],
            "evaluation_id",
        )
        self._assert_unique(
            [result.result_id for result in payload.evaluation_results],
            "evaluation_result_id",
        )

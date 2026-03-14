from __future__ import annotations

import logging
import time
from typing import List, Optional

from sqlalchemy import select

from app.config import settings
from app.db.models import (
    EvaluationORM,
    TaskORM,
    TaskSolutionORM,
)
from app.models.core import Evaluation, Task, TaskSolution
from app.services.validator.validator_storage_common import DuplicateIdentifierError

logger = logging.getLogger(__name__)


class ValidatorStorageEvaluationsMixin:
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
                    raise DuplicateIdentifierError(f"task_id {task.task_id} already belongs to validator_round {existing.validator_round_id}")
                if allow_existing:
                    logger.debug(f"Task {task.task_id} already exists for validator_round {validator_round_id}, skipping (idempotent)")
                    continue
                raise DuplicateIdentifierError(f"task_id {task.task_id} already exists for validator_round {validator_round_id}")

            logger.debug(f"Adding new task {task.task_id} for validator_round {validator_round_id}")
            self.session.add(TaskORM(**kwargs))
            count += 1

        logger.info(f"add_tasks: {count} new tasks added, {len(tasks) - count} already existed for validator_round {validator_round_id}")
        return count

    async def add_evaluation(
        self,
        *,
        validator_round_id: str,
        agent_run_id: str,
        task: Task,
        task_solution: TaskSolution,
        evaluation: Evaluation,
    ) -> None:
        """Persist evaluation data (task, solution, and evaluation with artefacts)."""
        round_row = await self._ensure_round_exists(validator_round_id)
        agent_run_row = await self._get_agent_run_row(agent_run_id)
        if not agent_run_row:
            raise ValueError(f"Agent run {agent_run_id} has not been registered yet")
        if agent_run_row.validator_round_id != validator_round_id:
            raise ValueError(f"Agent run {agent_run_id} is not associated with validator_round {validator_round_id}")

        if task.validator_round_id != validator_round_id:
            raise ValueError(f"Task {task.task_id} does not belong to validator_round {validator_round_id}")

        existing_task = await self._get_task_row(task.task_id)
        if existing_task is None:
            # In TESTING mode, auto-register the task if it doesn't exist
            # This handles cases where set_tasks failed but we still want to accept evaluations
            if settings.TESTING:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"TESTING mode: Task {task.task_id} not found, auto-registering for validator_round {validator_round_id}")
                try:
                    await self.add_tasks(validator_round_id, [task], allow_existing=True)
                    existing_task = await self._get_task_row(task.task_id)
                    if existing_task is None:
                        raise ValueError(f"Failed to auto-register task {task.task_id} for validator_round {validator_round_id}")
                except Exception as exc:
                    logger.error(
                        f"Failed to auto-register task {task.task_id}: {exc}",
                        exc_info=True,
                    )
                    raise ValueError(f"Task {task.task_id} has not been registered for validator_round {validator_round_id} and auto-registration failed: {exc}") from exc
            else:
                raise ValueError(f"Task {task.task_id} has not been registered for validator_round {validator_round_id}")
        if existing_task.validator_round_id != validator_round_id:
            raise ValueError(f"Task {task.task_id} is registered under validator_round {existing_task.validator_round_id}, not {validator_round_id}")

        # Task solution
        solution_kwargs = self._task_solution_kwargs(task_solution)
        stmt_solution = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == task_solution.solution_id)
        existing_solution = await self.session.scalar(stmt_solution)
        if existing_solution:
            raise DuplicateIdentifierError(f"task_solution_id {task_solution.solution_id} is already registered")
        solution_row = TaskSolutionORM(**solution_kwargs)
        self.session.add(solution_row)

        await self.session.flush()

        # Evaluation (consolidated - contains all data including artefacts)
        # Ensure validator_hotkey is set from round if not in evaluation model
        evaluation_kwargs = self._evaluation_kwargs(evaluation)
        if not evaluation_kwargs.get("validator_hotkey") and round_row.validator_snapshot:
            evaluation_kwargs["validator_hotkey"] = round_row.validator_snapshot.validator_hotkey

        # Ensure miner_uid and miner_hotkey are set from agent_run if not in evaluation model
        if not evaluation_kwargs.get("miner_uid") and agent_run_row.miner_uid is not None:
            evaluation_kwargs["miner_uid"] = agent_run_row.miner_uid
        if not evaluation_kwargs.get("miner_hotkey") and agent_run_row.miner_hotkey:
            evaluation_kwargs["miner_hotkey"] = agent_run_row.miner_hotkey

        # Separate execution_history to store in related table
        execution_history_data = evaluation_kwargs.pop("execution_history", [])

        stmt_evaluation = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation.evaluation_id)
        existing_evaluation = await self.session.scalar(stmt_evaluation)
        if existing_evaluation:
            raise DuplicateIdentifierError(f"evaluation_id {evaluation.evaluation_id} is already registered")
        evaluation_row = EvaluationORM(**evaluation_kwargs)
        self.session.add(evaluation_row)

        # Create execution_history record if there's data
        if execution_history_data:
            from app.db.models import EvaluationExecutionHistoryORM

            await self.session.flush()  # Get evaluation.id
            execution_history_row = EvaluationExecutionHistoryORM(
                evaluation_id=evaluation_row.evaluation_id,
                execution_history=execution_history_data,
            )
            self.session.add(execution_history_row)

        # Persist per-model/provider LLM usage (from llm_usage or from scalar llm_* for subnet compat)
        await self._sync_llm_usage(evaluation_row, self._llm_usage_from_evaluation(evaluation))

        # 🔍 CRITICAL: Update agent_run stats immediately after adding evaluation
        # This ensures average_score is NEVER NULL if there are evaluations
        # Previously, average_score was only updated in finish_round, which could
        # be called before all evaluations were created, leaving average_score as NULL
        await self.session.flush()  # Ensure evaluation is persisted before recalculating

        # Reload agent_run with evaluations and task_solutions to recalc stats without lazy loads
        await self.session.refresh(agent_run_row, ["evaluations", "task_solutions"])
        total_tasks_override = await self._resolve_expected_total_tasks_for_round(agent_run_row.validator_round_id)
        metrics = self._compute_agent_run_stats(agent_run_row, total_tasks_override=total_tasks_override)
        agent_run_row.total_tasks = metrics["total_tasks"]
        agent_run_row.success_tasks = metrics["success_tasks"]
        agent_run_row.failed_tasks = metrics["failed_tasks"]
        agent_run_row.average_score = metrics["average_score"]
        agent_run_row.average_execution_time = metrics["average_execution_time"]
        agent_run_row.average_reward = metrics["average_reward"]
        if getattr(agent_run_row, "zero_reason", None) is None and self._run_has_zero_score(agent_run_row):
            agent_run_row.zero_reason = self._derive_run_zero_reason_from_evaluations(agent_run_row)

        # Close this agent run now: ended_at = when we received this evaluation (run is per-miner, not per-round)
        now = time.time()
        agent_run_row.ended_at = now
        if agent_run_row.started_at is not None:
            agent_run_row.elapsed_sec = max(0.0, float(now) - float(agent_run_row.started_at))

    async def upsert_evaluation_bundle(
        self,
        *,
        validator_round_id: str,
        agent_run_id: str,
        task: Task,
        task_solution: TaskSolution,
        evaluation: Evaluation,
    ) -> None:
        """
        Insert any missing records for the (solution, evaluation) bundle and
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
            raise ValueError(f"Agent run {agent_run_id} is not associated with validator_round {validator_round_id}")

        # Ensure task exists and belongs to round
        if task.validator_round_id != validator_round_id:
            raise ValueError(f"Task {task.task_id} does not belong to validator_round {validator_round_id}")
        existing_task = await self._get_task_row(task.task_id)
        if existing_task is None:
            raise ValueError(f"Task {task.task_id} has not been registered for validator_round {validator_round_id}")
        if existing_task.validator_round_id != validator_round_id:
            raise ValueError(f"Task {task.task_id} is registered under validator_round {existing_task.validator_round_id}, not {validator_round_id}")

        # Task solution upsert/validate
        existing_solution = await self.get_task_solution_row(task_solution.solution_id)
        if existing_solution is not None:
            if existing_solution.validator_round_id != validator_round_id or existing_solution.agent_run_id != agent_run_id or existing_solution.task_id != task.task_id:
                raise DuplicateIdentifierError(f"task_solution_id {task_solution.solution_id} already belongs to a different context")
            solution_row = existing_solution
        else:
            solution_kwargs = self._task_solution_kwargs(task_solution)
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
                raise DuplicateIdentifierError(f"evaluation_id {evaluation.evaluation_id} already belongs to a different context")
            evaluation_row = existing_eval
            await self._sync_llm_usage(evaluation_row, self._llm_usage_from_evaluation(evaluation))
        else:
            evaluation_kwargs = self._evaluation_kwargs(evaluation)
            # Ensure validator_hotkey is set from round if not in evaluation model
            if not evaluation_kwargs.get("validator_hotkey") and round_row.validator_snapshot:
                evaluation_kwargs["validator_hotkey"] = round_row.validator_snapshot.validator_hotkey

            # Ensure miner_uid and miner_hotkey are set from agent_run if not in evaluation model
            if not evaluation_kwargs.get("miner_uid") and agent_run_row.miner_uid is not None:
                evaluation_kwargs["miner_uid"] = agent_run_row.miner_uid
            if not evaluation_kwargs.get("miner_hotkey") and agent_run_row.miner_hotkey:
                evaluation_kwargs["miner_hotkey"] = agent_run_row.miner_hotkey

            # Separate execution_history to store in related table
            execution_history_data = evaluation_kwargs.pop("execution_history", [])

            evaluation_row = EvaluationORM(**evaluation_kwargs)
            self.session.add(evaluation_row)

            # Create execution_history record if there's data
            if execution_history_data:
                from app.db.models import EvaluationExecutionHistoryORM

                await self.session.flush()  # Get evaluation.id
                execution_history_row = EvaluationExecutionHistoryORM(
                    evaluation_id=evaluation_row.evaluation_id,
                    execution_history=execution_history_data,
                )
                self.session.add(execution_history_row)

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

            # Persist per-model/provider LLM usage (from llm_usage or scalar llm_* for subnet compat)
            await self._sync_llm_usage(evaluation_row, self._llm_usage_from_evaluation(evaluation))

            # 🔍 CRITICAL: Update agent_run stats immediately after adding new evaluation
            # This ensures average_score is NEVER NULL if there are evaluations
            # Only update if this was a new evaluation (not existing)
            await self.session.refresh(agent_run_row, ["evaluations", "task_solutions"])
            total_tasks_override = await self._resolve_expected_total_tasks_for_round(agent_run_row.validator_round_id)
            metrics = self._compute_agent_run_stats(agent_run_row, total_tasks_override=total_tasks_override)
            agent_run_row.total_tasks = metrics["total_tasks"]
            agent_run_row.success_tasks = metrics["success_tasks"]
            agent_run_row.failed_tasks = metrics["failed_tasks"]
            agent_run_row.average_score = metrics["average_score"]
            agent_run_row.average_execution_time = metrics["average_execution_time"]
            agent_run_row.average_reward = metrics["average_reward"]
            if getattr(agent_run_row, "zero_reason", None) is None and self._run_has_zero_score(agent_run_row):
                agent_run_row.zero_reason = self._derive_run_zero_reason_from_evaluations(agent_run_row)

            # Close this agent run now (run is per-miner; we don't wait for round end)
            now = time.time()
            agent_run_row.ended_at = now
            if agent_run_row.started_at is not None:
                agent_run_row.elapsed_sec = max(0.0, float(now) - float(agent_run_row.started_at))

    async def get_task_solution_row(self, solution_id: str) -> Optional[TaskSolutionORM]:
        stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution_id)
        return await self.session.scalar(stmt)

    async def get_evaluation_row(self, evaluation_id: str) -> Optional[EvaluationORM]:
        stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
        return await self.session.scalar(stmt)

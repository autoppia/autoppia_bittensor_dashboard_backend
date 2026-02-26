from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, defer
from app.config import settings

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    EvaluationLLMUsageORM,
    TaskORM,
    TaskSolutionORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundValidatorORM,
)
import logging

from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    Miner,
    ValidatorRoundMiner,
    Task,
    TaskSolution,
    Validator,
    ValidatorRound,
    ValidatorRoundSubmissionRequest,
    ValidatorRoundValidator,
)

logger = logging.getLogger(__name__)


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


# Keys in agent_run metadata that are redundant with DB columns (is_reused, reused_from_agent_run_id)
_AGENT_RUN_META_REDUNDANT_KEYS = frozenset({"handshake_note", "reused_from_round"})


def _agent_run_meta_for_storage(model: "AgentEvaluationRun") -> Dict[str, Any]:
    """Store only useful agent_run metadata; omit handshake_note/reused_from_round (already in is_reused/reused_from_agent_run_id)."""
    meta = getattr(model, "metadata", None) or {}
    if not meta:
        return {}
    cleaned = {k: v for k, v in meta.items() if k not in _AGENT_RUN_META_REDUNDANT_KEYS}
    return _non_empty_dict(cleaned)


def _clean_meta_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Clean metadata dict: remove empty/useless fields and heavy LLM payloads.
    llm_calls / llm_usage detail is not stored here (lives in evaluation_llm_usage and AWS logs).
    timeout (and similar) are not stored here: use zero_reason column instead.
    """
    if not value:
        return {}

    # Don't store heavy LLM payloads in meta (duplicated in evaluation_llm_usage + AWS)
    # Don't store timeout/timeout_reason in extra_info; use zero_reason column (e.g. task_timeout)
    skip_keys = {"llm_calls", "llm_usage", "timeout", "timeout_reason"}
    useless_fields = {
        "notes": "",
        "error_message": "",
        "version_ok": True,
        "evaluation_score": 0.0,
        "reward": 0.0,
    }

    cleaned = {}
    for key, val in value.items():
        if key in skip_keys:
            continue
        if key in useless_fields and val == useless_fields[key]:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        cleaned[key] = val

    return cleaned


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


def _strip_round_prefix(task_id: str, round_id: str) -> Optional[str]:
    prefix = f"{round_id}_"
    if task_id.startswith(prefix):
        return task_id[len(prefix) :]
    return None


def _make_solution_id(miner_uid: Optional[int]) -> str:
    suffix = uuid.uuid4().hex
    if miner_uid is None:
        return f"task_solution_{suffix}"
    return f"task_solution_{miner_uid}_{suffix}"


def _make_evaluation_id(miner_uid: Optional[int]) -> str:
    suffix = uuid.uuid4().hex
    if miner_uid is None:
        return f"evaluation_{suffix}"
    return f"evaluation_{miner_uid}_{suffix}"


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
        # Check for existing round with same season and round_in_season for this validator
        await self._purge_round_for_validator_season_and_round(validator_round.validator_uid, validator_round.season_number, validator_round.round_number_in_season)
        await self._ensure_unique_season_round(
            validator_round.validator_uid,
            validator_round.season_number,
            validator_round.round_number_in_season,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        if existing_round is not None:
            raise DuplicateIdentifierError(f"validator_round_id {validator_round.validator_round_id} is already registered")

        round_kwargs = self._validator_round_kwargs(validator_round)

        round_row = ValidatorRoundORM(**round_kwargs)
        self.session.add(round_row)
        await self.session.flush()

        await self._upsert_validator_snapshot(round_row, validator_snapshot)

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

        # Check if agent_run_id already exists (idempotency by ID)
        existing_run = await self._get_agent_run_row(agent_run.agent_run_id)
        if existing_run:
            if existing_run.validator_round_id == validator_round_id:
                # Same agent_run_id for same round - idempotent, return existing
                return existing_run
            else:
                raise DuplicateIdentifierError(f"agent_run_id {agent_run.agent_run_id} is already registered for a different round")

        # CRITICAL: Check if there's already an agent_run for this miner in this round
        # An agent run should be unique per (validator_round_id, miner_uid)
        if agent_run.miner_uid is not None:
            from app.db.models import AgentEvaluationRunORM

            stmt_existing = (
                select(AgentEvaluationRunORM)
                .where(
                    AgentEvaluationRunORM.validator_round_id == validator_round_id,
                    AgentEvaluationRunORM.miner_uid == agent_run.miner_uid,
                )
                .limit(1)
            )
            result_existing = await self.session.execute(stmt_existing)
            existing_for_miner = result_existing.scalar_one_or_none()

            if existing_for_miner:
                # There's already an agent_run for this miner in this round
                # Return the existing one instead of creating a duplicate
                logger.warning(
                    f"Agent run already exists for miner_uid={agent_run.miner_uid} in validator_round_id={validator_round_id}. "
                    f"Existing agent_run_id={existing_for_miner.agent_run_id}, requested agent_run_id={agent_run.agent_run_id}. "
                    f"Returning existing agent run (idempotent)."
                )
                return existing_for_miner

        await self._upsert_miner_snapshot(round_row, miner_snapshot)

        kwargs = self._agent_run_kwargs(agent_run)

        row = AgentEvaluationRunORM(**kwargs)
        self.session.add(row)
        await self.session.flush()

        # Reused runs: copy result metrics from source at creation time (instant, no evaluation).
        # Do NOT copy ended_at/elapsed_sec: reused = 0s "evaluation" time (we didn't run anything).
        is_reused = getattr(agent_run, "is_reused", False)
        reused_from_id = getattr(agent_run, "reused_from_agent_run_id", None)
        if is_reused and reused_from_id:
            source = await self._resolve_reused_source_run(reused_from_id)
            if source:
                # Always anchor reused runs to the original source run.
                row.reused_from_agent_run_id = source.agent_run_id
                for attr in (
                    "average_score",
                    "average_execution_time",
                    "average_reward",
                    "total_tasks",
                    "success_tasks",
                    "failed_tasks",
                    "zero_reason",
                ):
                    val = getattr(source, attr, None)
                    if val is not None:
                        setattr(row, attr, val)
                if getattr(source, "meta", None) is not None and isinstance(source.meta, dict):
                    row.meta = dict(source.meta)
                # Reused run is "closed" immediately: no time spent evaluating
                row.ended_at = row.started_at
                row.elapsed_sec = 0.0
                await self.session.flush()
                logger.debug(
                    "start_agent_run: copied metrics from source run %s to reused run %s (elapsed_sec=0)",
                    reused_from_id,
                    row.agent_run_id,
                )

        return row

    async def get_round_by_validator_and_number(
        self,
        *,
        validator_uid: int,
        round_number: int,
    ) -> Optional[ValidatorRoundORM]:
        """DEPRECATED: Fetch an existing round row by (validator_uid, round_number).

        This method is deprecated. Use season_number and round_number_in_season instead.
        """
        # This method is kept for backward compatibility but should not be used
        # It will always return None since round_number column no longer exists
        return None

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
                evaluations_id=evaluation_row.id,
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
        metrics = self._compute_agent_run_stats(agent_run_row)
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

        # Propagate to any runs that reused from this one (they may have been created when this run was still empty)
        await self._propagate_source_metrics_to_reused_runs(agent_run_row)

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
                    evaluations_id=evaluation_row.id,
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
            metrics = self._compute_agent_run_stats(agent_run_row)
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

            await self._propagate_source_metrics_to_reused_runs(agent_run_row)

    # ──────────────────────────────────────────────────────────────────────
    # Read-only helpers for idempotency checks
    # ──────────────────────────────────────────────────────────────────────

    async def get_task_solution_row(self, solution_id: str) -> Optional[TaskSolutionORM]:
        stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution_id)
        return await self.session.scalar(stmt)

    async def get_evaluation_row(self, evaluation_id: str) -> Optional[EvaluationORM]:
        stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id)
        return await self.session.scalar(stmt)

    async def finish_round(
        self,
        *,
        validator_round_id: str,
        status: str,
        ended_at: float,
        agent_runs: Optional[List[Dict[str, Any]]] = None,
        round_metadata: Optional[Dict[str, Any]] = None,
        validator_summary: Optional[Dict[str, Any]] = None,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        ipfs_uploaded: Optional[Dict[str, Any]] = None,
        ipfs_downloaded: Optional[Dict[str, Any]] = None,
        s3_logs: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a validator round as completed."""
        round_row = await self._ensure_round_exists(validator_round_id)

        # If round metadata was provided by the validator, persist boundary fields.
        # This is the only place end_block/end_epoch are communicated back to the backend.
        if round_metadata and isinstance(round_metadata, dict):
            # Keep summary round_number coherent with persisted round id fields.
            # Some validators can compute round_number from a later block during finish.
            try:
                persisted_round_in_season = int(getattr(round_row, "round_number_in_season", 0) or 0)
                if persisted_round_in_season > 0:
                    round_metadata["round_number"] = persisted_round_in_season
            except Exception:
                pass
            try:
                rb = round_metadata.get("start_block")
                if rb is not None and int(rb) > 0 and (getattr(round_row, "start_block", None) in (None, 0)):
                    round_row.start_block = int(rb)
            except Exception:
                pass
            try:
                rb = round_metadata.get("end_block")
                if rb is not None and int(rb) > 0 and (getattr(round_row, "end_block", None) in (None, 0)):
                    round_row.end_block = int(rb)
            except Exception:
                pass
            try:
                re = round_metadata.get("start_epoch")
                if re is not None and int(re) > 0 and (getattr(round_row, "start_epoch", None) in (None, 0)):
                    round_row.start_epoch = int(re)
            except Exception:
                pass
            try:
                re = round_metadata.get("end_epoch")
                if re is not None and int(re) > 0 and (getattr(round_row, "end_epoch", None) in (None, 0)):
                    round_row.end_epoch = int(re)
            except Exception:
                pass
            try:
                rs = round_metadata.get("started_at")
                if rs is not None and (getattr(round_row, "started_at", None) in (None, 0)):
                    round_row.started_at = float(rs)
            except Exception:
                pass
            try:
                ra = round_metadata.get("ended_at")
                if ra is not None and (getattr(round_row, "ended_at", None) in (None, 0)):
                    round_row.ended_at = float(ra)
            except Exception:
                pass
        # Ensure start/end epoch are populated even when testing overrides bypassed chain-boundary fill
        try:
            if getattr(round_row, "start_epoch", None) is None or getattr(round_row, "end_epoch", None) is None:
                # Calculate epochs from start_block
                from app.services.round_calc import block_to_epoch

                if getattr(round_row, "start_epoch", None) is None:
                    round_row.start_epoch = int(block_to_epoch(round_row.start_block))
                if getattr(round_row, "end_epoch", None) is None:
                    round_row.end_epoch = int(block_to_epoch(round_row.end_block or round_row.start_block))
        except Exception:
            # If boundary computation fails, proceed without blocking finish
            pass

        # Normalize status to match ValidatorRound literal type
        normalized_status = status.lower()
        if normalized_status in {"completed", "complete"}:
            normalized_status = "finished"
        elif normalized_status not in {
            "active",
            "finished",
            "pending",
            "evaluating_finished",
        }:
            normalized_status = "finished"

        round_row.status = normalized_status

        # validator_summary: solo round, s3_logs, ipfs_uploaded, ipfs_downloaded, evaluation_pre_consensus, evaluation_post_consensus
        from app.services.subnet_utils import get_price
        from app.config import settings

        emission_info = None
        try:
            alpha_price = get_price(netuid=settings.VALIDATOR_NETUID)
            if alpha_price <= 0:
                alpha_price = float(settings.SUBNET_PRICE_FALLBACK)
        except Exception:
            alpha_price = float(settings.SUBNET_PRICE_FALLBACK)

        if round_metadata and isinstance(round_metadata, dict):
            emission_info = round_metadata.get("emission", {})
        if not emission_info and post_consensus_evaluation:
            emission_info = (post_consensus_evaluation or {}).get("emission", {})
        if emission_info:
            emission_info = dict(emission_info)
            emission_info["alpha_price"] = float(alpha_price)

        round_with_emission = None
        if round_metadata:
            round_with_emission = dict(round_metadata)
            if emission_info:
                round_with_emission["emission"] = emission_info
        elif emission_info:
            round_with_emission = {"emission": emission_info}

        vs = validator_summary or {}
        merged = {
            "round": round_with_emission or vs.get("round"),
            "s3_logs": s3_logs if s3_logs is not None else vs.get("s3_logs"),
            "ipfs_uploaded": ipfs_uploaded or vs.get("ipfs_uploaded"),
            "ipfs_downloaded": ipfs_downloaded or vs.get("ipfs_downloaded"),
            "evaluation_pre_consensus": vs.get("evaluation_pre_consensus") or (local_evaluation.get("summary") if isinstance(local_evaluation, dict) else None),
            "evaluation_post_consensus": vs.get("evaluation_post_consensus") or (post_consensus_evaluation.get("summary") if isinstance(post_consensus_evaluation, dict) else None),
        }

        # Normalize consensus summaries for stable UI/API shape
        pre_summary = merged.get("evaluation_pre_consensus")
        if isinstance(pre_summary, dict):
            # Remove noisy internal key from pre-consensus summary.
            pre_summary.pop("schema_version", None)
            rs = pre_summary.get("round_summary") if isinstance(pre_summary.get("round_summary"), dict) else {}
            winner_obj = rs.get("winner") if isinstance(rs.get("winner"), dict) else {}
            miner_uid = winner_obj.get("miner_uid")
            if miner_uid is None:
                # Try to infer deterministically from round summary fields
                decision = rs.get("decision") if isinstance(rs.get("decision"), dict) else {}
                inferred_uid = winner_obj.get("uid") or decision.get("top_candidate_uid") or pre_summary.get("season_summary", {}).get("current_winner_uid")
                if inferred_uid is None and isinstance(rs.get("miner_scores"), dict) and rs.get("miner_scores"):
                    try:
                        inferred_uid = max(
                            rs.get("miner_scores").items(),
                            key=lambda kv: float(kv[1] or 0.0),
                        )[0]
                    except Exception:
                        inferred_uid = None
                if inferred_uid is not None:
                    try:
                        winner_obj["miner_uid"] = int(inferred_uid)
                    except Exception:
                        pass
                    rs["winner"] = winner_obj
                    pre_summary["round_summary"] = rs
            # Ensure season_summary carries explicit current winner uid
            season_summary = pre_summary.get("season_summary") if isinstance(pre_summary.get("season_summary"), dict) else {}
            season_current_uid = season_summary.get("current_winner_uid")
            if season_current_uid is None:
                inferred_season_uid = winner_obj.get("miner_uid") or winner_obj.get("uid")
                if inferred_season_uid is not None:
                    try:
                        season_summary["current_winner_uid"] = int(inferred_season_uid)
                    except Exception:
                        pass
            pre_summary["season_summary"] = season_summary
            merged["evaluation_pre_consensus"] = pre_summary

        post_summary = merged.get("evaluation_post_consensus")
        if isinstance(post_summary, dict):
            # Remove noisy internal key from post-consensus summary shown in UI payloads.
            post_summary.pop("schema_version", None)
            merged["evaluation_post_consensus"] = post_summary

        round_row.validator_summary = merged
        if s3_logs is not None:
            round_row.s3_logs = s3_logs

        round_row.ended_at = ended_at

        rank_map: Dict[str, Optional[int]] = {}
        weight_map: Dict[str, Optional[float]] = {}
        zero_reason_map: Dict[str, Optional[str]] = {}
        is_reused_map: Dict[str, bool] = {}
        reused_from_map: Dict[str, Optional[str]] = {}
        agent_runs_by_id: Dict[str, Dict[str, Any]] = {}
        if agent_runs:
            for agent_run_data in agent_runs:
                agent_run_id = agent_run_data.get("agent_run_id")
                if not agent_run_id:
                    continue
                rank_map[agent_run_id] = agent_run_data.get("rank")
                weight_map[agent_run_id] = agent_run_data.get("weight")
                zero_reason_map[agent_run_id] = agent_run_data.get("zero_reason")
                is_reused_map[agent_run_id] = bool(agent_run_data.get("is_reused", False))
                reused_from_map[agent_run_id] = agent_run_data.get("reused_from_agent_run_id")
                agent_runs_by_id[agent_run_id] = agent_run_data

        stmt_runs = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluations)
                .options(
                    defer(EvaluationORM.gif_recording),
                    defer(EvaluationORM.extra_info),
                )
                .selectinload(EvaluationORM.execution_history_record),
            )
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
        )
        run_rows_result = await self.session.scalars(stmt_runs)
        run_rows = list(run_rows_result)

        for run_row in run_rows:
            is_reused = is_reused_map.get(run_row.agent_run_id, getattr(run_row, "is_reused", False))
            # Do NOT set ended_at/elapsed_sec here: agent runs are per-miner and already closed
            # - Reused: closed at start_agent_run (ended_at=started_at, elapsed_sec=0)
            # - Evaluated: closed in add_evaluation when we received the last evaluation

            if not is_reused:
                metrics = self._compute_agent_run_stats(run_row)
                run_row.total_tasks = metrics["total_tasks"]
                run_row.success_tasks = metrics["success_tasks"]
                run_row.failed_tasks = metrics["failed_tasks"]
                run_row.average_score = metrics["average_score"]
                run_row.average_execution_time = metrics["average_execution_time"]
                run_row.average_reward = metrics["average_reward"]
            else:
                # Reused runs: source run is truth. Never overwrite with payload 0/0/0 — rounds are sequential, source always has metrics.
                payload_data = agent_runs_by_id.get(run_row.agent_run_id) or {}
                source_id = reused_from_map.get(run_row.agent_run_id) or getattr(run_row, "reused_from_agent_run_id", None)
                source_run = await self._resolve_reused_source_run(source_id) if source_id else None
                if source_run is not None:
                    run_row.reused_from_agent_run_id = source_run.agent_run_id

                payload_attempted = payload_data.get("tasks_attempted")
                source_total = (getattr(source_run, "total_tasks", None) or 0) if source_run else 0
                # Prefer source when payload would zero out (validator sometimes sends 0 for reused runs)
                use_source_metrics = source_run is not None and source_total > 0 and (payload_attempted is None or int(payload_attempted or 0) == 0)

                if use_source_metrics:
                    run_row.total_tasks = int(source_run.total_tasks or 0)
                    run_row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                    run_row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                    run_row.average_score = source_run.average_score
                    run_row.average_execution_time = getattr(source_run, "average_execution_time", None)
                    run_row.average_reward = getattr(source_run, "average_reward", None)
                    if getattr(source_run, "zero_reason", None):
                        run_row.zero_reason = source_run.zero_reason
                else:
                    if payload_attempted is not None:
                        run_row.total_tasks = int(payload_attempted)
                    elif source_run is not None:
                        run_row.total_tasks = int(source_run.total_tasks or 0)
                    if payload_data.get("tasks_completed") is not None:
                        run_row.success_tasks = int(payload_data["tasks_completed"])
                    elif source_run is not None:
                        run_row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                    if payload_data.get("tasks_failed") is not None:
                        run_row.failed_tasks = int(payload_data["tasks_failed"])
                    elif source_run is not None:
                        run_row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                    if payload_data.get("avg_reward") is not None:
                        run_row.average_reward = float(payload_data["avg_reward"])
                    elif source_run is not None and getattr(source_run, "average_reward", None) is not None:
                        run_row.average_reward = float(source_run.average_reward)
                    if payload_data.get("avg_evaluation_time") is not None:
                        payload_avg_eval_time = float(payload_data["avg_evaluation_time"])
                        if payload_avg_eval_time > 0.0:
                            run_row.average_execution_time = payload_avg_eval_time
                        elif source_run is not None and getattr(source_run, "average_execution_time", None) is not None:
                            run_row.average_execution_time = float(source_run.average_execution_time)
                        else:
                            run_row.average_execution_time = payload_avg_eval_time
                    elif source_run is not None and getattr(source_run, "average_execution_time", None) is not None:
                        run_row.average_execution_time = float(source_run.average_execution_time)
                    total = getattr(run_row, "total_tasks", 0) or 0
                    success = getattr(run_row, "success_tasks", 0) or 0
                    run_row.average_score = (success / total) if total else (float(source_run.average_score) if source_run and getattr(source_run, "average_score", None) is not None else 0.0)

            if run_row.agent_run_id in zero_reason_map:
                run_row.zero_reason = zero_reason_map[run_row.agent_run_id]
            if run_row.agent_run_id in is_reused_map:
                run_row.is_reused = is_reused_map[run_row.agent_run_id]
            if run_row.agent_run_id in reused_from_map:
                # Do not downgrade an already-resolved root source to an intermediate reused run.
                if not getattr(run_row, "reused_from_agent_run_id", None):
                    run_row.reused_from_agent_run_id = reused_from_map[run_row.agent_run_id]

            # If run has effective score 0 and no zero_reason: for reused runs use source run's zero_reason, else derive from evaluations
            if run_row.zero_reason is None and self._run_has_zero_score(run_row):
                source_id = getattr(run_row, "reused_from_agent_run_id", None)
                if source_id:
                    source_run = await self._get_agent_run_row(source_id)
                    if source_run and getattr(source_run, "zero_reason", None):
                        run_row.zero_reason = source_run.zero_reason
                if run_row.zero_reason is None:
                    run_row.zero_reason = self._derive_run_zero_reason_from_evaluations(run_row)

            # rank and weight removed from agent_evaluation_runs
            # They are now stored in validator_round_summary_miners and updated there

        # Cascade: runs that reuse a run we just updated may have been processed in a
        # finish_round that ran before this round (e.g. round 5 before round 4). Now that
        # this run has total_tasks/failed_tasks/average_execution_time, copy them to all
        # runs that have reused_from_agent_run_id = this run.
        for run_row in run_rows:
            source_id = getattr(run_row, "agent_run_id", None)
            if not source_id:
                continue
            has_stats = (getattr(run_row, "total_tasks", None) or 0) > 0 or getattr(run_row, "average_execution_time", None) is not None
            if not has_stats:
                continue
            stmt_reused = select(AgentEvaluationRunORM).where(
                AgentEvaluationRunORM.reused_from_agent_run_id == source_id,
            )
            reused_rows_result = await self.session.scalars(stmt_reused)
            for reused_row in reused_rows_result:
                if (getattr(reused_row, "total_tasks", None) or 0) == 0 and getattr(reused_row, "average_execution_time", None) is None:
                    reused_row.total_tasks = run_row.total_tasks or 0
                    reused_row.success_tasks = run_row.success_tasks or 0
                    reused_row.failed_tasks = run_row.failed_tasks or 0
                    reused_row.average_score = run_row.average_score
                    reused_row.average_execution_time = run_row.average_execution_time
                    reused_row.average_reward = run_row.average_reward
                    if getattr(run_row, "zero_reason", None) and getattr(reused_row, "zero_reason", None) is None:
                        reused_row.zero_reason = run_row.zero_reason
                    logger.debug(
                        "finish_round: cascaded stats from source run %s to reused run %s",
                        source_id,
                        reused_row.agent_run_id,
                    )

        # Populate validator_round_summary_miners table
        await self._populate_round_summary(
            validator_round_id=validator_round_id,
            local_evaluation=local_evaluation,
            post_consensus_evaluation=post_consensus_evaluation,
            subnet_price=alpha_price,
        )
        await self._enrich_validator_summary_post_consensus_from_db(round_row)

    async def submit_round(self, payload: ValidatorRoundSubmissionRequest) -> PersistenceResult:
        """Persist the entire round submission payload."""
        self._assert_unique_payload(payload)

        validator_round = payload.validator_round
        await self._ensure_unique_round_number(
            validator_round.validator_uid,
            validator_round.round_number,
            exclude_round_id=None,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        round_kwargs = self._validator_round_kwargs(validator_round)

        if existing_round:
            for key, value in round_kwargs.items():
                setattr(existing_round, key, value)
            round_row = existing_round
        else:
            round_row = ValidatorRoundORM(**round_kwargs)
            self.session.add(round_row)
        await self.session.flush()

        # Snapshots (1:1 relationship - only one snapshot per round)
        validator_snapshot_ids: List[int] = []
        if payload.validator_snapshots:
            # Take the first snapshot (should only be one)
            snapshot = payload.validator_snapshots[0]
            row = await self._upsert_validator_snapshot(
                round_row,
                snapshot,
            )
            validator_snapshot_ids.append(row.id)

        miner_snapshot_ids: List[int] = []
        for snapshot in payload.miner_snapshots:
            row = await self._upsert_miner_snapshot(round_row, snapshot)
            miner_snapshot_ids.append(row.id)

        # Agent runs
        agent_run_ids: List[str] = []
        for agent_run in payload.agent_evaluation_runs:
            kwargs = self._agent_run_kwargs(agent_run)
            stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run.agent_run_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"agent_run_id {agent_run.agent_run_id} is provided multiple times")
            self.session.add(AgentEvaluationRunORM(**kwargs))
            agent_run_ids.append(agent_run.agent_run_id)

        # Tasks
        await self.add_tasks(round_row.validator_round_id, payload.tasks)
        task_ids = [task.task_id for task in payload.tasks]

        # Task solutions
        task_solution_ids: List[str] = []
        for solution in payload.task_solutions:
            kwargs = self._task_solution_kwargs(solution)
            stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution.solution_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"task_solution_id {solution.solution_id} is provided multiple times")
            self.session.add(TaskSolutionORM(**kwargs))
            task_solution_ids.append(solution.solution_id)

        # Evaluations
        evaluation_ids: List[str] = []
        evaluation_rows: Dict[str, EvaluationORM] = {}
        execution_histories: List[tuple[EvaluationORM, list]] = []  # Store for later creation

        for evaluation in payload.evaluations:
            kwargs = self._evaluation_kwargs(evaluation)

            # Ensure miner_uid and miner_hotkey are set from agent_run if not in evaluation model
            if not kwargs.get("miner_uid") or not kwargs.get("miner_hotkey"):
                # Find the agent_run to get miner info
                agent_run_stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == evaluation.agent_run_id)
                agent_run_row = await self.session.scalar(agent_run_stmt)
                if agent_run_row:
                    if not kwargs.get("miner_uid") and agent_run_row.miner_uid is not None:
                        kwargs["miner_uid"] = agent_run_row.miner_uid
                    if not kwargs.get("miner_hotkey") and agent_run_row.miner_hotkey:
                        kwargs["miner_hotkey"] = agent_run_row.miner_hotkey

            # Separate execution_history to store in related table
            execution_history_data = kwargs.pop("execution_history", [])

            stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation.evaluation_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"evaluation_id {evaluation.evaluation_id} is provided multiple times")
            evaluation_row = EvaluationORM(**kwargs)
            self.session.add(evaluation_row)
            evaluation_ids.append(evaluation.evaluation_id)
            evaluation_rows[evaluation.evaluation_id] = evaluation_row

            if execution_history_data:
                execution_histories.append((evaluation_row, execution_history_data))

        await self.session.flush()

        # Persist per-model/provider LLM usage (after flush so eval rows exist; subnet can send llm_* scalars)
        for evaluation in payload.evaluations:
            eval_row = evaluation_rows.get(evaluation.evaluation_id)
            if eval_row is not None:
                await self._sync_llm_usage(eval_row, self._llm_usage_from_evaluation(evaluation))

        # Create execution_history records after flush (so we have evaluation.id)
        if execution_histories:
            from app.db.models import EvaluationExecutionHistoryORM

            for evaluation_row, execution_history_data in execution_histories:
                execution_history_row = EvaluationExecutionHistoryORM(
                    evaluations_id=evaluation_row.id,
                    execution_history=execution_history_data,
                )
                self.session.add(execution_history_row)

        # 🔍 CRITICAL: Update agent_run stats after adding all evaluations in batch
        # This ensures average_score is NEVER NULL if there are evaluations
        # This is especially important for submit_round which adds multiple evaluations at once
        if agent_run_ids:
            from sqlalchemy.orm import selectinload

            stmt_runs = select(AgentEvaluationRunORM).options(selectinload(AgentEvaluationRunORM.evaluations)).where(AgentEvaluationRunORM.agent_run_id.in_(agent_run_ids))
            run_rows_result = await self.session.scalars(stmt_runs)
            run_rows = list(run_rows_result)

            for run_row in run_rows:
                metrics = self._compute_agent_run_stats(run_row)
                run_row.total_tasks = metrics["total_tasks"]
                run_row.success_tasks = metrics["success_tasks"]
                run_row.failed_tasks = metrics["failed_tasks"]
                run_row.average_score = metrics["average_score"]
                run_row.average_execution_time = metrics["average_execution_time"]
                run_row.average_reward = metrics["average_reward"]

        saved = {
            "validator_round": round_row.validator_round_id,
            "validator_snapshots": validator_snapshot_ids,
            "miner_snapshots": miner_snapshot_ids,
            "agent_evaluation_runs": agent_run_ids,
            "tasks": task_ids,
            "task_solutions": task_solution_ids,
            "evaluations": evaluation_ids,
        }
        # Get validator_uid from snapshot (1:1 relationship)
        validator_uid = payload.validator_snapshots[0].validator_uid if payload.validator_snapshots else None
        if validator_uid is None:
            raise ValueError("No validator snapshot provided")
        return PersistenceResult(
            validator_uid=validator_uid,
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

    @staticmethod
    def _run_has_zero_score(run_row: AgentEvaluationRunORM) -> bool:
        """True if the run has effective score 0 (so it must have a zero_reason)."""
        avg = getattr(run_row, "average_score", None)
        if avg is not None and avg <= 0.0:
            return True
        total = getattr(run_row, "total_tasks", None) or 0
        success = getattr(run_row, "success_tasks", None) or 0
        return total > 0 and success == 0

    @staticmethod
    def _derive_run_zero_reason_from_evaluations(run_row: AgentEvaluationRunORM) -> str:
        """Derive zero_reason for a run with score 0 from its evaluations (never leave 0 without reason)."""
        evaluations = list(getattr(run_row, "evaluations", []) or [])
        zero_reasons: List[str] = []
        for ev in evaluations:
            score = getattr(ev, "evaluation_score", None)
            if score is not None and float(score) <= 0.0:
                reason = getattr(ev, "zero_reason", None)
                if reason:
                    zero_reasons.append(reason)
        if not zero_reasons:
            return "task_failed"
        # Use single most common reason (e.g. all task_timeout -> "task_timeout")
        (reason, _) = Counter(zero_reasons).most_common(1)[0]
        return reason

    def _compute_agent_run_stats(self, run_row: AgentEvaluationRunORM) -> Dict[str, Any]:
        task_solutions = list(getattr(run_row, "task_solutions", []) or [])
        evaluations = list(getattr(run_row, "evaluations", []) or [])

        # CRITICAL: total_tasks should be the number of evaluations, because each task has one evaluation
        # (even if the miner didn't respond, we create an evaluation with score 0.0)
        # So total_tasks = len(evaluations) is correct
        total_tasks = len(evaluations)

        # If no evaluations yet, fall back to counting unique task_ids from solutions
        if total_tasks == 0:
            task_ids = {solution.task_id for solution in task_solutions if solution.task_id}
            total_tasks = len(task_ids) if task_ids else (run_row.total_tasks or 0)

        total_tasks = int(total_tasks or 0)

        scores: List[float] = []
        for eval_obj in evaluations:
            value = self._to_float(getattr(eval_obj, "evaluation_score", None)) or self._to_float(getattr(eval_obj, "eval_score", None))
            # 🔍 CRITICAL: If no evaluation_score found, default to 0.0 (task failed)
            # This ensures average_score is NEVER None if there are evaluations
            # Each task should have an evaluation with evaluation_score (0.0 or 1.0)
            if value is not None:
                scores.append(value)
            else:
                # Evaluation exists but no score - treat as failed (0.0)
                # This should never happen if task_flow.py is working correctly,
                # but we handle it defensively
                scores.append(0.0)

        # 🔍 CRITICAL: average_score should NEVER be None if there are evaluations
        # If there are evaluations, we should always have scores (even if all are 0.0)
        # If there are no evaluations, average_score can be None (round not finished yet)
        if total_tasks > 0:
            # We have tasks (evaluations), so we must have scores
            average_score = sum(scores) / len(scores) if scores else 0.0
        else:
            # No tasks yet - average_score is None (round not finished)
            average_score = None

        # Binary scoring: evaluation_score is 1.0 if at least one test passed, 0.0 otherwise
        # success_tasks counts evaluations with evaluation_score > 0.0 (i.e., == 1.0)
        success_tasks = sum(1 for score in scores if score > 0.0)
        if success_tasks > total_tasks:
            success_tasks = total_tasks
        failed_tasks = max(total_tasks - success_tasks, 0)

        evaluation_times: List[float] = []
        for eval_obj in evaluations:
            value = self._to_float(getattr(eval_obj, "evaluation_time", None))
            if value is not None and value >= 0.0:
                evaluation_times.append(value)
        average_execution_time = sum(evaluation_times) / len(evaluation_times) if evaluation_times else None

        reward_values: List[float] = []
        for eval_obj in evaluations:
            reward_candidate: Any = getattr(eval_obj, "reward", None)
            if reward_candidate is None:
                meta = getattr(eval_obj, "meta", {}) or {}
                if isinstance(meta, dict):
                    reward_candidate = meta.get("reward") or meta.get("total_reward") or meta.get("final_reward")
            if reward_candidate is None:
                reward_candidate = getattr(eval_obj, "evaluation_score", None) or getattr(eval_obj, "eval_score", None)
            value = self._to_float(reward_candidate)
            if value is not None:
                reward_values.append(value)

        average_reward = (sum(reward_values) / len(reward_values)) if reward_values and len(reward_values) > 0 else None

        return {
            "total_tasks": total_tasks,
            "success_tasks": success_tasks,
            "failed_tasks": failed_tasks,
            "average_score": average_score,
            "average_execution_time": average_execution_time,
            "average_reward": average_reward,
        }

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    async def _get_round_row(self, validator_round_id: str) -> Optional[ValidatorRoundORM]:
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        return await self.session.scalar(stmt)

    async def _purge_round_for_validator_and_number(self, validator_uid: int, round_number: Optional[int]) -> None:
        """DEPRECATED: Delete any existing round for this validator and round_number (and cascade children)."""
        if round_number is None:
            return
        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.round_number == round_number,
            )
        )
        rows = list(await self.session.scalars(stmt))
        if not rows:
            return
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

    async def _purge_round_for_validator_season_and_round(self, validator_uid: int, season_number: int, round_number_in_season: int) -> None:
        """Delete any existing round for this validator, season and round_in_season (and cascade children)."""
        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.season_number == season_number,
                ValidatorRoundORM.round_number_in_season == round_number_in_season,
            )
        )
        rows = list(await self.session.scalars(stmt))
        if not rows:
            return
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

    async def _get_agent_run_row(self, agent_run_id: str) -> Optional[AgentEvaluationRunORM]:
        stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        return await self.session.scalar(stmt)

    async def _resolve_reused_source_run(self, source_run_id: Optional[str]) -> Optional[AgentEvaluationRunORM]:
        """Follow reused_from chain and return the root source run."""
        if not source_run_id:
            return None
        visited: set[str] = set()
        current_id: Optional[str] = source_run_id
        root: Optional[AgentEvaluationRunORM] = None
        while current_id and current_id not in visited:
            visited.add(current_id)
            row = await self._get_agent_run_row(current_id)
            if row is None:
                break
            root = row
            current_id = getattr(row, "reused_from_agent_run_id", None)
        return root

    async def _propagate_source_metrics_to_reused_runs(self, source_run: AgentEvaluationRunORM) -> None:
        """When a source run gets its metrics (e.g. after evaluations), copy them to any runs that reuse from it.
        This fixes runs created before the source had data (e.g. round N+1 reused run created before round N evaluations arrived).
        """
        total = getattr(source_run, "total_tasks", None) or 0
        if total <= 0:
            return
        stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.reused_from_agent_run_id == source_run.agent_run_id)
        result = await self.session.scalars(stmt)
        updated = False
        for row in result:
            if (getattr(row, "total_tasks", None) or 0) <= 0:
                row.total_tasks = int(total)
                row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                if getattr(source_run, "average_score", None) is not None:
                    row.average_score = source_run.average_score
                if getattr(source_run, "average_execution_time", None) is not None:
                    row.average_execution_time = source_run.average_execution_time
                if getattr(source_run, "average_reward", None) is not None:
                    row.average_reward = source_run.average_reward
                if getattr(source_run, "zero_reason", None):
                    row.zero_reason = source_run.zero_reason
                updated = True
        if updated:
            await self.session.flush()

    async def _get_task_row(self, task_id: str) -> Optional[TaskORM]:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        return await self.session.scalar(stmt)

    async def _ensure_round_exists(self, validator_round_id: str) -> ValidatorRoundORM:
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        if not round_row:
            raise ValueError(f"Validator round {validator_round_id} not found")
        return round_row

    async def ensure_round_exists_or_create_minimal_for_round_log(
        self,
        validator_round_id: str,
        season: Optional[int],
        round_in_season: Optional[int],
        validator_uid: Optional[int],
        validator_hotkey: Optional[str],
        *,
        owner_hotkey_from_request: Optional[str] = None,
    ) -> ValidatorRoundORM:
        """
        Return the round row, creating a minimal round + validator snapshot if the round
        does not exist (e.g. after IWAP reset). Allows round-log upload to succeed and
        finish_round to update the row later.
        """
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        if round_row is not None:
            return round_row
        uid = int(validator_uid) if validator_uid is not None else 0
        hotkey = (validator_hotkey or owner_hotkey_from_request or "").strip()
        if not hotkey:
            raise ValueError("Cannot create minimal round: validator_hotkey or owner_hotkey_from_request required")
        round_row = ValidatorRoundORM(
            validator_round_id=validator_round_id,
            season_number=season,
            round_number_in_season=round_in_season,
            start_block=0,
            end_block=None,
            start_epoch=0,
            end_epoch=None,
            started_at=0.0,
            ended_at=None,
            n_tasks=0,
            status="active",
        )
        self.session.add(round_row)
        await self.session.flush()
        snapshot = ValidatorRoundValidatorORM(
            validator_round_id=validator_round_id,
            validator_uid=uid,
            validator_hotkey=hotkey,
            validator_coldkey=None,
            name=None,
            stake=None,
            vtrust=None,
            image_url=None,
            version=None,
            config=None,
        )
        self.session.add(snapshot)
        await self.session.flush()
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        assert round_row is not None
        logger.info("Created minimal validator round %s for round-log upload (e.g. after IWAP reset)", validator_round_id)
        return round_row

    async def _ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """DEPRECATED: Use _ensure_unique_season_round instead."""
        if round_number is None:
            return

        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.round_number == round_number,
            )
        )
        if exclude_round_id is not None:
            stmt = stmt.where(ValidatorRoundORM.validator_round_id != exclude_round_id)
        existing = await self.session.scalar(stmt)
        if existing:
            raise RoundConflictError(f"Validator {validator_uid} already has a round with number {round_number}")

    async def _ensure_unique_season_round(
        self,
        validator_uid: int,
        season_number: int,
        round_number_in_season: int,
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """Ensure no existing round for this validator with same season and round_in_season."""
        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.season_number == season_number,
                ValidatorRoundORM.round_number_in_season == round_number_in_season,
            )
        )
        if exclude_round_id is not None:
            stmt = stmt.where(ValidatorRoundORM.validator_round_id != exclude_round_id)
        existing = await self.session.scalar(stmt)
        if existing:
            raise RoundConflictError(f"Validator {validator_uid} already has a round for season {season_number}, round {round_number_in_season}")

    async def ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """DEPRECATED: Public wrapper to guard against duplicate round numbers."""
        await self._ensure_unique_round_number(validator_uid, round_number, exclude_round_id=exclude_round_id)

    async def _upsert_validator_snapshot(
        self,
        round_row: ValidatorRoundORM,
        snapshot: ValidatorRoundValidator,
    ) -> ValidatorRoundValidatorORM:
        # 1:1 relationship - only one snapshot per round
        stmt = select(ValidatorRoundValidatorORM).where(
            ValidatorRoundValidatorORM.validator_round_id == round_row.validator_round_id,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "validator_uid": snapshot.validator_uid,
            "validator_hotkey": snapshot.validator_hotkey,
            "validator_coldkey": snapshot.validator_coldkey,
            "name": snapshot.name,
            "stake": snapshot.stake,
            "vtrust": snapshot.vtrust,
            "image_url": snapshot.image_url,
            "version": snapshot.version,
            "config": snapshot.config,  # Include validator configuration
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
    ) -> ValidatorRoundMinerORM:
        stmt = select(ValidatorRoundMinerORM).where(
            ValidatorRoundMinerORM.validator_round_id == round_row.validator_round_id,
            ValidatorRoundMinerORM.miner_uid == snapshot.miner_uid,
            ValidatorRoundMinerORM.miner_hotkey == snapshot.miner_hotkey,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "miner_uid": snapshot.miner_uid,
            "miner_hotkey": snapshot.miner_hotkey,
            "miner_coldkey": snapshot.miner_coldkey,
            "name": snapshot.agent_name,
            "image_url": snapshot.image_url,
            "github_url": snapshot.github_url,
            "is_sota": snapshot.is_sota,
            "version": snapshot.version if hasattr(snapshot, "version") else None,
        }
        if existing:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing
        row = ValidatorRoundMinerORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    def _validator_round_kwargs(self, model: ValidatorRound) -> Dict[str, Any]:
        # validator_uid, validator_hotkey, validator_coldkey moved to ValidatorRoundValidatorORM
        # metadata/round summary is stored in validator_summary at finish_round, not at start
        return {
            "validator_round_id": model.validator_round_id,
            "season_number": model.season_number,
            "round_number_in_season": model.round_number_in_season,
            "start_block": model.start_block,
            "end_block": model.end_block,
            "start_epoch": model.start_epoch,
            "end_epoch": model.end_epoch,
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "n_tasks": model.n_tasks,
            "status": model.status,
        }

    def _agent_run_kwargs(
        self,
        model: AgentEvaluationRun,
    ) -> Dict[str, Any]:
        return {
            "agent_run_id": model.agent_run_id,
            "validator_round_id": model.validator_round_id,
            # validator_uid and validator_hotkey removed - obtain via validator_round.validator_snapshot
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            # is_sota and version removed - obtain via validator_round.miner_snapshots
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "elapsed_sec": model.elapsed_sec,
            "average_score": model.average_score,
            "average_execution_time": model.average_execution_time,
            "average_reward": model.average_reward,
            # total_reward removed - no longer stored in agent_evaluation_runs
            "total_tasks": model.total_tasks,
            "success_tasks": getattr(model, "success_tasks", getattr(model, "completed_tasks", 0)),
            "failed_tasks": model.failed_tasks,
            # rank and weight removed - obtain via validator_round_summary_miners
            "meta": _agent_run_meta_for_storage(model),
            "is_reused": getattr(model, "is_reused", False),
            "reused_from_agent_run_id": getattr(model, "reused_from_agent_run_id", None),
            "zero_reason": getattr(model, "zero_reason", None),
        }

    def _task_kwargs(self, model: Task) -> Dict[str, Any]:
        return {
            "task_id": model.task_id,
            "validator_round_id": model.validator_round_id,
            "is_web_real": model.is_web_real,
            "web_project_id": model.web_project_id,
            "web_version": model.web_version,
            "url": model.url,
            "prompt": model.prompt,
            "specifications": _non_empty_dict(model.specifications),
            "tests": [test.model_dump(mode="json", exclude_none=True) for test in model.tests],
            "use_case": (model.use_case if isinstance(model.use_case, dict) else _optional_dump(model.use_case)),
        }

    def _task_solution_kwargs(
        self,
        model: TaskSolution,
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
            "actions": _action_dump(model.actions),
        }

    def _evaluation_kwargs(
        self,
        model: Evaluation,
    ) -> Dict[str, Any]:
        # Normalize evaluation_score and reward (accept eval_score from legacy payloads)
        eval_score_val = getattr(model, "evaluation_score", None) or getattr(model, "eval_score", None)
        if eval_score_val is None:
            eval_score_val = 0.0
        try:
            eval_score_val = float(eval_score_val)
        except Exception:
            eval_score_val = 0.0

        reward_val = getattr(model, "reward", 0.0)
        try:
            reward_val = float(reward_val)
        except Exception:
            reward_val = 0.0

        # Enforce minimum reward when evaluation_score > 0: at least EVAL_SCORE_WEIGHT
        # If evaluation_score == 0, reward must be 0
        if eval_score_val > 0.0:
            eval_score_weight = float(settings.EVAL_SCORE_WEIGHT)

            reward_val = max(reward_val, eval_score_weight)
        else:
            reward_val = 0.0

        zero_reason = getattr(model, "zero_reason", None)
        if zero_reason is None and eval_score_val <= 0.0:
            meta = getattr(model, "metadata", None) or {}
            if meta.get("timeout") is True:
                zero_reason = "task_timeout"

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
            "evaluation_score": eval_score_val,
            "reward": reward_val,
            "evaluation_time": model.evaluation_time,
            "execution_history": list(model.execution_history),
            "gif_recording": model.gif_recording,
            "extra_info": _clean_meta_dict(model.metadata),
            "zero_reason": zero_reason,
        }

    def _llm_usage_from_evaluation(self, evaluation: Evaluation) -> Any:
        """Return llm_usage list from evaluation (single source of truth)."""
        usage = getattr(evaluation, "llm_usage", None)
        if usage and isinstance(usage, list) and len(usage) > 0:
            return usage
        return None

    def _normalize_llm_usage(self, usage: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not usage:
            return rows
        for item in usage:
            if hasattr(item, "model_dump"):
                raw = item.model_dump()
            elif isinstance(item, dict):
                raw = item
            else:
                continue
            provider = raw.get("provider")
            model = raw.get("model")
            tokens = raw.get("tokens")
            cost = raw.get("cost")
            if provider is None and model is None and tokens is None and cost is None:
                continue
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "tokens": tokens,
                    "cost": cost,
                }
            )
        return rows

    async def _sync_llm_usage(
        self,
        evaluation_row: EvaluationORM,
        usage: Any,
    ) -> None:
        usage_rows = self._normalize_llm_usage(usage)
        if not usage_rows:
            return
        await self.session.execute(delete(EvaluationLLMUsageORM).where(EvaluationLLMUsageORM.evaluation_id == evaluation_row.evaluation_id))
        for row in usage_rows:
            self.session.add(
                EvaluationLLMUsageORM(
                    evaluation_id=evaluation_row.evaluation_id,
                    provider=row.get("provider"),
                    model=row.get("model"),
                    tokens=row.get("tokens"),
                    cost=row.get("cost"),
                )
            )

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

    async def _enrich_validator_summary_post_consensus_from_db(self, round_row: ValidatorRoundORM) -> None:
        """Enrich validator_summary.evaluation_post_consensus with DB-derived metrics.

        Source of truth is validator_round_summary_miners. This makes the summary explicit,
        debuggable, and consistent with APIs that read from round summary rows.
        """
        stmt_rows = select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == round_row.validator_round_id).order_by(ValidatorRoundSummaryORM.miner_uid.asc())
        summary_rows = list(await self.session.scalars(stmt_rows))
        if not summary_rows:
            return

        validators_count = int(
            await self.session.scalar(select(func.count(func.distinct(ValidatorRoundValidatorORM.validator_uid))).where(ValidatorRoundValidatorORM.validator_round_id == round_row.validator_round_id))
            or 0
        )

        miners_payload: List[Dict[str, Any]] = []
        tasks_evaluated_total = 0
        tasks_success_total = 0
        rewards: List[float] = []
        eval_scores: List[float] = []
        eval_times: List[float] = []

        for row in summary_rows:
            post_tasks_received = int(row.post_consensus_tasks_received or 0)
            post_tasks_success = int(row.post_consensus_tasks_success or 0)
            tasks_evaluated_total += post_tasks_received
            tasks_success_total += post_tasks_success
            if row.post_consensus_avg_reward is not None:
                rewards.append(float(row.post_consensus_avg_reward))
            if row.post_consensus_avg_eval_score is not None:
                eval_scores.append(float(row.post_consensus_avg_eval_score))
            if row.post_consensus_avg_eval_time is not None:
                eval_times.append(float(row.post_consensus_avg_eval_time))

            miners_payload.append(
                {
                    "miner_uid": int(row.miner_uid),
                    "miner_hotkey": row.miner_hotkey,
                    "post_consensus_rank": row.post_consensus_rank,
                    "post_consensus_avg_reward": row.post_consensus_avg_reward,
                    "post_consensus_avg_eval_score": row.post_consensus_avg_eval_score,
                    "post_consensus_avg_eval_time": row.post_consensus_avg_eval_time,
                    "post_consensus_tasks_received": row.post_consensus_tasks_received,
                    "post_consensus_tasks_success": row.post_consensus_tasks_success,
                    "weight": row.weight,
                }
            )

        winner = next((m for m in miners_payload if m.get("post_consensus_rank") == 1), None)
        if winner is None:
            winner = max(
                miners_payload,
                key=lambda m: (
                    float(m.get("post_consensus_avg_reward") or 0.0),
                    float(m.get("post_consensus_avg_eval_score") or 0.0),
                    -int(m.get("miner_uid") or 0),
                ),
                default=None,
            )

        db_rollup = {
            "validator_round_id": round_row.validator_round_id,
            "season_number": round_row.season_number,
            "round_number_in_season": round_row.round_number_in_season,
            "validators_count": validators_count,
            "miners_evaluated": len(miners_payload),
            "tasks_evaluated": tasks_evaluated_total,
            "tasks_success": tasks_success_total,
            "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
            "avg_eval_score": (sum(eval_scores) / len(eval_scores)) if eval_scores else 0.0,
            "avg_eval_time": (sum(eval_times) / len(eval_times)) if eval_times else 0.0,
            "single_validator_mode": validators_count == 1,
            "winner": {
                "miner_uid": winner.get("miner_uid"),
                "post_consensus_rank": winner.get("post_consensus_rank"),
                "post_consensus_avg_reward": winner.get("post_consensus_avg_reward"),
            }
            if winner
            else None,
        }

        validator_summary = dict(round_row.validator_summary or {})
        current_post = validator_summary.get("evaluation_post_consensus")
        if isinstance(current_post, dict):
            enriched_post = dict(current_post)
        elif current_post is None:
            enriched_post = {}
        else:
            enriched_post = {"raw": current_post}

        enriched_post["miners"] = miners_payload
        # Avoid noisy internal fields in post-consensus payload.
        enriched_post.pop("schema_version", None)
        winner_uid = db_rollup.get("winner", {}).get("miner_uid") if isinstance(db_rollup.get("winner"), dict) else None
        round_summary = enriched_post.get("round_summary") if isinstance(enriched_post.get("round_summary"), dict) else {}
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        if winner_uid is not None:
            winner_obj["miner_uid"] = int(winner_uid)
        round_summary["winner"] = winner_obj
        enriched_post["round_summary"] = round_summary

        decision_obj = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
        season_summary = enriched_post.get("season_summary") if isinstance(enriched_post.get("season_summary"), dict) else {}
        if winner_uid is not None:
            season_summary["current_winner_uid"] = int(winner_uid)
        if "dethroned" not in season_summary:
            season_summary["dethroned"] = bool(decision_obj.get("dethroned", False))
        enriched_post["season_summary"] = season_summary

        def _to_int(value: Any) -> Optional[int]:
            try:
                if value is None:
                    return None
                return int(value)
            except Exception:
                return None

        def _to_float(value: Any) -> Optional[float]:
            try:
                if value is None:
                    return None
                return float(value)
            except Exception:
                return None

        # Keep round-level consensus decision as first-class columns in validator_rounds.
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        round_row.winner_uid = _to_int(winner_obj.get("miner_uid") or winner_obj.get("uid"))
        round_row.winner_score = _to_float(winner_obj.get("score"))
        round_row.reigning_uid_before_round = _to_int(decision_obj.get("reigning_uid_before_round"))
        round_row.reigning_score_before_round = _to_float(decision_obj.get("reigning_score_before_round"))
        round_row.top_candidate_uid = _to_int(decision_obj.get("top_candidate_uid"))
        round_row.top_candidate_score = _to_float(decision_obj.get("top_candidate_score"))
        round_row.required_improvement_pct = _to_float(season_summary.get("required_improvement_pct", decision_obj.get("required_improvement_pct")))
        dethroned_value = season_summary.get("dethroned", decision_obj.get("dethroned"))
        round_row.dethroned = bool(dethroned_value) if dethroned_value is not None else None

        # Convenience top-level aliases to avoid ambiguity in consumers.
        enriched_post.setdefault("validators_count", db_rollup["validators_count"])
        enriched_post.setdefault("miners_evaluated", db_rollup["miners_evaluated"])
        enriched_post.setdefault("tasks_evaluated", db_rollup["tasks_evaluated"])
        enriched_post.setdefault("tasks_success", db_rollup["tasks_success"])
        # winner is already represented in round_summary; avoid duplicated winner blocks here.
        enriched_post.pop("winner", None)

        validator_summary["evaluation_post_consensus"] = enriched_post
        round_row.validator_summary = validator_summary

    async def _populate_round_summary(
        self,
        *,
        validator_round_id: str,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        subnet_price: Optional[float] = None,
    ) -> None:
        """Populate validator_round_summary_miners table from local_evaluation and post_consensus_evaluation.

        If no evaluation data is provided, creates basic summary records from agent_runs.
        """
        # Build a map of miner_uid -> summary data
        summary_map: Dict[int, Dict[str, Any]] = {}
        run_metrics_map: Dict[int, Dict[str, Any]] = {}

        # Always read local metrics from persisted agent runs as the source of truth.
        stmt_runs = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.validator_round_id == validator_round_id).where(AgentEvaluationRunORM.miner_uid.isnot(None))
        run_rows = await self.session.scalars(stmt_runs)
        for run_row in run_rows:
            if run_row.miner_uid is None:
                continue
            miner_uid = int(run_row.miner_uid)
            run_metrics_map[miner_uid] = {
                "miner_uid": miner_uid,
                "miner_hotkey": run_row.miner_hotkey,
                "local_avg_reward": run_row.average_reward,
                "local_avg_eval_score": run_row.average_score,
                "local_avg_eval_time": run_row.average_execution_time,
                "local_tasks_received": run_row.total_tasks,
                "local_tasks_success": run_row.success_tasks,
            }
            summary_map.setdefault(miner_uid, {}).update(run_metrics_map[miner_uid])

        # If no evaluation data provided, create basic summaries from agent_runs
        if not local_evaluation and not post_consensus_evaluation:
            # When only runs are available, also derive deterministic local ranking.
            ranked_miners = sorted(
                run_metrics_map.items(),
                key=lambda kv: (
                    -float(kv[1].get("local_avg_reward") or 0.0),
                    -float(kv[1].get("local_avg_eval_score") or 0.0),
                    int(kv[0]),
                ),
            )
            for index, (miner_uid, _) in enumerate(ranked_miners, start=1):
                summary_map.setdefault(miner_uid, {})["local_rank"] = index

        # Process local_evaluation
        if local_evaluation and isinstance(local_evaluation, dict):
            local_miners = local_evaluation.get("miners", [])
            for miner_data in local_miners:
                if not isinstance(miner_data, dict):
                    continue
                miner_uid = miner_data.get("miner_uid")
                if miner_uid is None:
                    continue

                summary_map.setdefault(miner_uid, {})["miner_uid"] = int(miner_uid)
                run_metrics = run_metrics_map.get(int(miner_uid), {})
                miner_hotkey = miner_data.get("miner_hotkey") or run_metrics.get("miner_hotkey")
                if miner_hotkey is not None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_hotkey

                local_rank = miner_data.get("rank")
                if local_rank is not None:
                    summary_map[miner_uid]["local_rank"] = local_rank

                # Local metrics must match persisted local execution (agent runs).
                # Only fall back to payload when run metrics are not available.
                summary_map[miner_uid]["local_avg_reward"] = run_metrics.get("local_avg_reward") if run_metrics.get("local_avg_reward") is not None else miner_data.get("avg_reward")
                summary_map[miner_uid]["local_avg_eval_score"] = run_metrics.get("local_avg_eval_score") if run_metrics.get("local_avg_eval_score") is not None else miner_data.get("avg_eval_score")
                summary_map[miner_uid]["local_avg_eval_time"] = run_metrics.get("local_avg_eval_time") if run_metrics.get("local_avg_eval_time") is not None else miner_data.get("avg_evaluation_time")
                summary_map[miner_uid]["local_tasks_received"] = run_metrics.get("local_tasks_received") if run_metrics.get("local_tasks_received") is not None else miner_data.get("tasks_attempted")
                summary_map[miner_uid]["local_tasks_success"] = run_metrics.get("local_tasks_success") if run_metrics.get("local_tasks_success") is not None else miner_data.get("tasks_completed")

        # If some local ranks are still missing but local metrics exist, derive from local_avg_reward.
        missing_rank_uids = [uid for uid, data in summary_map.items() if data.get("local_rank") is None and data.get("local_avg_reward") is not None]
        if missing_rank_uids:
            ranked_miners = sorted(
                missing_rank_uids,
                key=lambda uid: (
                    -float(summary_map[uid].get("local_avg_reward") or 0.0),
                    -float(summary_map[uid].get("local_avg_eval_score") or 0.0),
                    int(uid),
                ),
            )
            used_ranks = {int(data["local_rank"]) for data in summary_map.values() if data.get("local_rank") is not None}
            next_rank = 1
            for uid in ranked_miners:
                while next_rank in used_ranks:
                    next_rank += 1
                summary_map[uid]["local_rank"] = next_rank
                used_ranks.add(next_rank)
                next_rank += 1

        # Process post_consensus_evaluation
        if post_consensus_evaluation and isinstance(post_consensus_evaluation, dict):
            post_consensus_miners = post_consensus_evaluation.get("miners", [])
            for miner_data in post_consensus_miners:
                if not isinstance(miner_data, dict):
                    continue
                miner_uid = miner_data.get("miner_uid")
                if miner_uid is None:
                    continue

                summary_map.setdefault(miner_uid, {})["miner_uid"] = int(miner_uid)
                # Update miner_hotkey if not already set or if post_consensus has it
                if "miner_hotkey" not in summary_map[miner_uid] or summary_map[miner_uid]["miner_hotkey"] is None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_data.get("miner_hotkey")

                summary_map[miner_uid]["post_consensus_rank"] = miner_data.get("rank")
                summary_map[miner_uid]["post_consensus_avg_reward"] = miner_data.get("consensus_reward")
                summary_map[miner_uid]["post_consensus_avg_eval_score"] = miner_data.get("avg_eval_score")
                summary_map[miner_uid]["post_consensus_avg_eval_time"] = miner_data.get("avg_eval_time")
                summary_map[miner_uid]["post_consensus_tasks_received"] = miner_data.get("tasks_sent")
                summary_map[miner_uid]["post_consensus_tasks_success"] = miner_data.get("tasks_success")
                summary_map[miner_uid]["weight"] = miner_data.get("weight")
                # Add subnet_price to all miners in this round
                if subnet_price is not None:
                    summary_map[miner_uid]["subnet_price"] = float(subnet_price)

        # Consistency rule:
        # If this round only has one validator, post-consensus metrics should not
        # contradict local metrics. In reused/timeout flows, post-consensus payloads
        # can contain zeros (tasks_sent=0, avg_eval_time=0) even though local metrics
        # are valid. Normalize post-consensus from local in that case.
        validators_count_in_round = await self.session.scalar(
            select(func.count(func.distinct(ValidatorRoundValidatorORM.validator_uid))).where(ValidatorRoundValidatorORM.validator_round_id == validator_round_id)
        )
        if int(validators_count_in_round or 0) == 1:
            for _miner_uid, data in summary_map.items():
                local_tasks = int(data.get("local_tasks_received") or 0)
                post_tasks = int(data.get("post_consensus_tasks_received") or 0)
                if local_tasks <= 0:
                    continue
                if post_tasks <= 0:
                    data["post_consensus_tasks_received"] = local_tasks
                    data["post_consensus_tasks_success"] = int(data.get("local_tasks_success") or 0)
                if float(data.get("post_consensus_avg_eval_time") or 0.0) <= 0.0:
                    data["post_consensus_avg_eval_time"] = data.get("local_avg_eval_time")
                if float(data.get("post_consensus_avg_eval_score") or 0.0) <= 0.0:
                    data["post_consensus_avg_eval_score"] = data.get("local_avg_eval_score")
                if float(data.get("post_consensus_avg_reward") or 0.0) <= 0.0:
                    data["post_consensus_avg_reward"] = data.get("local_avg_reward")

        # Upsert summary records
        for miner_uid, summary_data in summary_map.items():
            stmt = select(ValidatorRoundSummaryORM).where(
                ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                ValidatorRoundSummaryORM.miner_uid == miner_uid,
            )
            existing = await self.session.scalar(stmt)

            # Ensure subnet_price is set for all records (use provided value or keep existing)
            if subnet_price is not None and "subnet_price" not in summary_data:
                summary_data["subnet_price"] = float(subnet_price)

            if existing:
                # Update existing record
                for key, value in summary_data.items():
                    if key != "miner_uid":  # Don't update the primary key
                        setattr(existing, key, value)
                # Update subnet_price if not in summary_data but provided
                if subnet_price is not None and existing.subnet_price is None:
                    existing.subnet_price = float(subnet_price)
            else:
                # Create new record
                new_summary = ValidatorRoundSummaryORM(validator_round_id=validator_round_id, **summary_data)
                self.session.add(new_summary)

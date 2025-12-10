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
    TaskORM,
    TaskSolutionORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundValidatorORM,
)
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


def _clean_meta_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Clean metadata dict, removing empty or useless fields."""
    if not value:
        return {}
    
    # Fields that are considered useless if empty or have default values
    useless_fields = {
        "notes": "",
        "error_message": "",
        "version_ok": True,  # Default value
        "eval_score": 0.0,  # Already stored in eval_score column
        "reward": 0.0,  # Already stored in reward column
        "final_score": 0.0,  # Legacy field name (replaced by eval_score)
    }
    
    cleaned = {}
    for key, val in value.items():
        # Skip if it's a useless field with default/empty value
        if key in useless_fields and val == useless_fields[key]:
            continue
        # Skip empty strings
        if isinstance(val, str) and not val.strip():
            continue
        # Include the field
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
        await self._purge_round_for_validator_and_number(
            validator_round.validator_uid, validator_round.round_number
        )
        await self._ensure_unique_round_number(
            validator_round.validator_uid,
            validator_round.round_number,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        if existing_round is not None:
            raise DuplicateIdentifierError(
                f"validator_round_id {validator_round.validator_round_id} is already registered"
            )

        round_row = ValidatorRoundORM(
            **self._validator_round_kwargs(validator_round)
        )
        self.session.add(round_row)
        await self.session.flush()

        snapshot_row = await self._upsert_validator_snapshot(
            round_row, validator_snapshot
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

        await self._upsert_miner_snapshot(round_row, miner_snapshot)

        kwargs = self._agent_run_kwargs(agent_run)

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
        return await self.session.scalar(stmt)

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

        # Task solution
        solution_kwargs = self._task_solution_kwargs(task_solution)
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

        # Evaluation (consolidated - contains all data including artefacts)
        # Ensure validator_hotkey is set from round if not in evaluation model
        evaluation_kwargs = self._evaluation_kwargs(evaluation)
        if not evaluation_kwargs.get("validator_hotkey") and round_row.validator_snapshot:
            evaluation_kwargs["validator_hotkey"] = round_row.validator_snapshot.validator_hotkey
        
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
                    existing_solution = await self.get_task_solution_row(
                        task_solution.solution_id
                    )
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
            evaluation_kwargs = self._evaluation_kwargs(evaluation)
            # Ensure validator_hotkey is set from round if not in evaluation model
            if not evaluation_kwargs.get("validator_hotkey") and round_row.validator_snapshot:
                evaluation_kwargs["validator_hotkey"] = round_row.validator_snapshot.validator_hotkey
            evaluation_row = EvaluationORM(**evaluation_kwargs)
            self.session.add(evaluation_row)

            # Handle race condition for evaluation
            try:
                await self.session.flush()
            except Exception as e:
                from sqlalchemy.exc import IntegrityError

                if isinstance(e, IntegrityError) and "uq_evaluation_id" in str(e):
                    await self.session.rollback()
                    existing_eval = await self.get_evaluation_row(
                        evaluation.evaluation_id
                    )
                    if existing_eval:
                        evaluation_row = existing_eval
                    else:
                        raise
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


    async def finish_round(
        self,
        *,
        validator_round_id: str,
        status: str,
        ended_at: float,
        agent_runs: Optional[List[Dict[str, Any]]] = None,
        round_metadata: Optional[Dict[str, Any]] = None,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        ipfs_uploaded: Optional[Dict[str, Any]] = None,
        ipfs_downloaded: Optional[Dict[str, Any]] = None,
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
        elif normalized_status not in {
            "active",
            "finished",
            "pending",
            "evaluating_finished",
        }:
            normalized_status = "finished"

        round_row.status = normalized_status

        # Build meta with all data
        meta_data = {
            **round_row.meta,
        }

        # Process emission info - check round_metadata first, then post_consensus_evaluation (for backward compatibility)
        emission_info = None
        from app.services.subnet_utils import get_price
        from app.config import settings

        # Calculate alpha_price from cached metagraph price
        try:
            alpha_price = get_price(netuid=settings.VALIDATOR_NETUID)
            if alpha_price <= 0:
                # Fallback to env if cached price is invalid
                alpha_price = float(settings.SUBNET_PRICE_FALLBACK)
        except Exception:
            # Safe fallback to env config
            alpha_price = float(settings.SUBNET_PRICE_FALLBACK)

        # Check if emission is already in round_metadata (preferred)
        if round_metadata and isinstance(round_metadata, dict):
            emission_info = round_metadata.get("emission", {})
        
        # Fallback: extract from post_consensus_evaluation (backward compatibility)
        if not emission_info and post_consensus_evaluation:
            import copy
            post_consensus_copy = copy.deepcopy(post_consensus_evaluation)
            emission_info = post_consensus_copy.get("emission", {})
            # Remove emission from post_consensus_evaluation if it was there
            if "emission" in post_consensus_copy:
                del post_consensus_copy["emission"]
            meta_data["post_consensus_evaluation"] = post_consensus_copy
        elif post_consensus_evaluation:
            # post_consensus_evaluation exists but no emission - use as is
            meta_data["post_consensus_evaluation"] = post_consensus_evaluation

        # Add alpha_price to emission info if we have it
        if emission_info:
            emission_info = dict(emission_info)  # Make a copy
            emission_info["alpha_price"] = float(alpha_price)

        # Add round_metadata with emission info
        if round_metadata:
            round_metadata_copy = dict(round_metadata)
            # Add/update emission info with alpha_price
            if emission_info:
                round_metadata_copy["emission"] = emission_info
            meta_data["round"] = round_metadata_copy
        elif emission_info:
            # If no round_metadata but we have emission, create minimal round metadata with emission
            meta_data["round"] = {"emission": emission_info}

        # Add other fields
        if local_evaluation:
            meta_data["local_evaluation"] = local_evaluation
        if ipfs_uploaded:
            meta_data["ipfs_uploaded"] = ipfs_uploaded
        if ipfs_downloaded:
            meta_data["ipfs_downloaded"] = ipfs_downloaded

        round_row.meta = meta_data

        # Calculate n_winners from post_consensus_evaluation
        if post_consensus_evaluation:
            miners = post_consensus_evaluation.get("miners", [])
            n_winners = len([m for m in miners if m.get("weight", 0) > 0])
            round_row.n_winners = n_winners
        else:
            round_row.n_winners = 0

        round_row.ended_at = ended_at

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
                selectinload(AgentEvaluationRunORM.evaluations),
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
            run_row.success_tasks = metrics["success_tasks"]
            run_row.failed_tasks = metrics["failed_tasks"]
            run_row.average_score = metrics["average_score"]
            run_row.average_execution_time = metrics["average_execution_time"]
            run_row.average_reward = metrics["average_reward"]

            # rank and weight removed from agent_evaluation_runs
            # They are now stored in validator_round_summary_miners and updated there

        # Populate validator_round_summary_miners table
        await self._populate_round_summary(
            validator_round_id=validator_round_id,
            local_evaluation=local_evaluation,
            post_consensus_evaluation=post_consensus_evaluation,
            subnet_price=alpha_price,
        )

    async def submit_round(
        self, payload: ValidatorRoundSubmissionRequest
    ) -> PersistenceResult:
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
            kwargs = self._task_solution_kwargs(solution)
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
            kwargs = self._evaluation_kwargs(evaluation)
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

    def _compute_agent_run_stats(
        self, run_row: AgentEvaluationRunORM
    ) -> Dict[str, Any]:
        task_solutions = list(getattr(run_row, "task_solutions", []) or [])
        evaluations = list(getattr(run_row, "evaluations", []) or [])

        task_ids = {solution.task_id for solution in task_solutions if solution.task_id}
        if not task_ids:
            task_ids = {
                eval_obj.task_id for eval_obj in evaluations if eval_obj.task_id
            }

        total_tasks = len(task_ids)
        if total_tasks == 0:
            total_tasks = run_row.total_tasks or len(evaluations)
        total_tasks = int(total_tasks or 0)

        scores: List[float] = []
        for eval_obj in evaluations:
            # Try eval_score first, fallback to final_score for legacy compatibility
            value = self._to_float(getattr(eval_obj, "eval_score", None)) or self._to_float(getattr(eval_obj, "final_score", None))
            if value is not None:
                scores.append(value)
        average_score = sum(scores) / len(scores) if scores else None

        success_tasks = sum(1 for score in scores if score >= 0.5)
        if success_tasks > total_tasks:
            success_tasks = total_tasks
        failed_tasks = max(total_tasks - success_tasks, 0)

        evaluation_times: List[float] = []
        for eval_obj in evaluations:
            value = self._to_float(getattr(eval_obj, "evaluation_time", None))
            if value is not None and value >= 0.0:
                evaluation_times.append(value)
        average_execution_time = (
            sum(evaluation_times) / len(evaluation_times) if evaluation_times else None
        )

        reward_values: List[float] = []
        for eval_obj in evaluations:
            # Try reward field first (new), then fallback to meta or final_score for legacy compatibility
            reward_candidate: Any = getattr(eval_obj, "reward", None)
            if reward_candidate is None:
                meta = getattr(eval_obj, "meta", {}) or {}
                if isinstance(meta, dict):
                    reward_candidate = meta.get("reward") or meta.get("total_reward") or meta.get("final_reward")
            if reward_candidate is None:
                # Legacy fallback: use eval_score or final_score
                reward_candidate = getattr(eval_obj, "eval_score", None) or getattr(eval_obj, "final_score", None)
            value = self._to_float(reward_candidate)
            if value is not None:
                reward_values.append(value)

        average_reward = (
            (sum(reward_values) / len(reward_values))
            if reward_values and len(reward_values) > 0
            else None
        )

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

    async def _get_round_row(
        self, validator_round_id: str
    ) -> Optional[ValidatorRoundORM]:
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = (
            select(ValidatorRoundORM)
            .options(selectinload(ValidatorRoundORM.validator_snapshot))
            .where(ValidatorRoundORM.validator_round_id == validator_round_id)
        )
        return await self.session.scalar(stmt)

    async def _purge_round_for_validator_and_number(
        self, validator_uid: int, round_number: Optional[int]
    ) -> None:
        """Delete any existing round for this validator and round_number (and cascade children)."""
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
            await self.session.delete(row)  # Fixed: added await
        await self.session.flush()

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
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = (
            select(ValidatorRoundORM)
            .options(selectinload(ValidatorRoundORM.validator_snapshot))
            .where(ValidatorRoundORM.validator_round_id == validator_round_id)
        )
        round_row = await self.session.scalar(stmt)
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
            "round_number": round_row.round_number,  # Copy from round_row
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
            "description": snapshot.description,
            "is_sota": snapshot.is_sota,
            "version": snapshot.version if hasattr(snapshot, "version") else None,
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
        self, model: ValidatorRound
    ) -> Dict[str, Any]:
        # validator_uid, validator_hotkey, validator_coldkey moved to ValidatorRoundValidatorORM
        return {
            "validator_round_id": model.validator_round_id,
            "round_number": model.round_number,
            "start_block": model.start_block,
            "end_block": model.end_block,
            "start_epoch": model.start_epoch,
            "end_epoch": model.end_epoch,
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "n_tasks": model.n_tasks,
            "n_miners": model.n_miners,
            "n_winners": model.n_winners,
            "status": model.status,
            "meta": _non_empty_dict(model.metadata),
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
            "success_tasks": model.success_tasks,
            "failed_tasks": model.failed_tasks,
            # rank and weight removed - obtain via validator_round_summary_miners
            "meta": _non_empty_dict(model.metadata),
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
            "eval_score": getattr(model, "eval_score", getattr(model, "final_score", 0.0)),  # Support both new and legacy field names
            "reward": getattr(model, "reward", 0.0),
            "evaluation_time": model.evaluation_time,
            "execution_history": list(model.execution_history),
            "feedback": _optional_dump(model.feedback),
            "gif_recording": model.gif_recording,
            "meta": _clean_meta_dict(model.metadata),
        }

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

    async def _populate_round_summary(
        self,
        *,
        validator_round_id: str,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        subnet_price: Optional[float] = None,
    ) -> None:
        """Populate validator_round_summary_miners table from local_evaluation and post_consensus_evaluation."""
        # Build a map of miner_uid -> summary data
        summary_map: Dict[int, Dict[str, Any]] = {}

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
                summary_map[miner_uid]["miner_hotkey"] = miner_data.get("miner_hotkey")
                summary_map[miner_uid]["local_rank"] = miner_data.get("rank")
                summary_map[miner_uid]["local_avg_reward"] = miner_data.get("avg_reward")
                summary_map[miner_uid]["local_avg_eval_score"] = miner_data.get("avg_eval_score")
                summary_map[miner_uid]["local_avg_eval_time"] = miner_data.get("avg_evaluation_time")
                summary_map[miner_uid]["local_tasks_received"] = miner_data.get("tasks_attempted")
                summary_map[miner_uid]["local_tasks_success"] = miner_data.get("tasks_completed")

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
                new_summary = ValidatorRoundSummaryORM(
                    validator_round_id=validator_round_id,
                    **summary_data
                )
                self.session.add(new_summary)

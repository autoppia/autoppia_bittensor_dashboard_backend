"""
Bulk submission endpoint for validator rounds using the normalized models.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
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
    ValidatorRoundSubmissionResponse,
    ValidatorRoundValidator,
)
from app.services.validator_storage import (
    RoundConflictError,
    DuplicateIdentifierError,
    ValidatorRoundPersistenceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds-post"])


class LegacyRoundSubmissionPayload(BaseModel):
    round: Dict[str, Any]
    agent_evaluation_runs: List[Dict[str, Any]] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    task_solutions: List[Dict[str, Any]] = Field(default_factory=list)
    evaluation_results: List[Dict[str, Any]] = Field(default_factory=list)


def _require_round_match(value: str, expected: str, label: str) -> None:
    if value != expected:
        raise HTTPException(
            status_code=400,
            detail=f"{label} mismatch: expected {expected}, got {value}",
        )


def _index_agent_runs(agent_runs: list[AgentEvaluationRun]) -> Dict[str, AgentEvaluationRun]:
    return {run.agent_run_id: run for run in agent_runs}


def _index_tasks(tasks: list[Task]) -> Dict[str, Task]:
    return {task.task_id: task for task in tasks}


def _index_task_solutions(task_solutions: list[TaskSolution]) -> Dict[str, TaskSolution]:
    return {solution.solution_id: solution for solution in task_solutions}


def _index_evaluations(evaluations: list[Evaluation]) -> Dict[str, Evaluation]:
    return {evaluation.evaluation_id: evaluation for evaluation in evaluations}


def _legacy_to_submission_request(
    payload: LegacyRoundSubmissionPayload,
) -> ValidatorRoundSubmissionRequest:
    round_data = dict(payload.round)
    round_id = round_data.get("validator_round_id")
    if not round_id:
        raise HTTPException(
            status_code=400,
            detail="validator_round_id is required in legacy round payloads",
        )

    validators_raw = list(round_data.get("validators", []) or [])
    if not validators_raw:
        raise HTTPException(
            status_code=400,
            detail="validators list is required in legacy round payloads",
        )

    if "round_number" not in round_data and "round" in round_data:
        round_data["round_number"] = round_data.pop("round")

    status_value = round_data.get("status")
    if status_value is not None:
        normalized = str(status_value).lower()
        if normalized in {"in_progress", "in-progress"}:
            round_data["status"] = "active"
        elif normalized in {"finished", "complete", "completed"}:
            round_data["status"] = "completed"
        elif normalized == "pending":
            round_data["status"] = "pending"

    primary_validator = validators_raw[0]
    round_data.setdefault("validator_uid", primary_validator.get("uid"))
    round_data.setdefault("validator_hotkey", primary_validator.get("hotkey"))
    round_data.setdefault("validator_coldkey", primary_validator.get("coldkey"))

    validator_identities: List[Validator] = []
    validator_snapshots: List[ValidatorRoundValidator] = []
    validator_hotkey_map: Dict[int, str] = {}

    for raw in validators_raw:
        uid = raw.get("uid")
        hotkey = raw.get("hotkey")
        if uid is None or hotkey is None:
            raise HTTPException(
                status_code=400,
                detail="validator entries must include uid and hotkey",
            )
        validator_identities.append(
            Validator(
                uid=uid,
                hotkey=hotkey,
                coldkey=raw.get("coldkey"),
            )
        )
        validator_snapshots.append(
            ValidatorRoundValidator(
                validator_round_id=round_id,
                validator_uid=uid,
                validator_hotkey=hotkey,
                name=raw.get("name"),
                stake=raw.get("stake"),
                vtrust=raw.get("vtrust"),
                image_url=raw.get("image") or raw.get("image_url"),
                version=raw.get("version"),
            )
        )
        validator_hotkey_map[int(uid)] = hotkey

    round_data.pop("validators", None)
    round_data.pop("validator_info", None)
    round_data.pop("miners", None)
    round_data.pop("sota_agents", None)

    validator_round = ValidatorRound(**round_data)

    miner_identity_map: Dict[tuple[Optional[int], Optional[str], Optional[str]], Miner] = {}

    def _register_miner(info: Dict[str, Any]) -> None:
        key = (info.get("uid"), info.get("hotkey"), info.get("agent_key"))
        if key in miner_identity_map:
            return
        miner_identity_map[key] = Miner(
            uid=info.get("uid"),
            hotkey=info.get("hotkey"),
            coldkey=info.get("coldkey"),
            agent_key=info.get("agent_key"),
        )

    for miner_raw in payload.round.get("miners", []) or []:
        _register_miner(miner_raw)
    for run_raw in payload.agent_evaluation_runs:
        miner_info = run_raw.get("miner_info") or {}
        _register_miner(miner_info)

    miner_identities = list(miner_identity_map.values())
    miner_hotkey_map: Dict[int, str] = {
        identity.uid: identity.hotkey  # type: ignore[index]
        for identity in miner_identities
        if identity.uid is not None and identity.hotkey is not None
    }

    miner_snapshots: List[ValidatorRoundMiner] = []
    for miner_raw in payload.round.get("miners", []) or []:
        uid = miner_raw.get("uid")
        hotkey = miner_raw.get("hotkey")
        if uid is None and not miner_raw.get("agent_key"):
            continue
        if uid is not None and hotkey is None:
            raise HTTPException(
                status_code=400,
                detail="miner entries must include hotkey when uid is provided",
            )
        miner_snapshots.append(
            ValidatorRoundMiner(
                validator_round_id=round_id,
                miner_uid=uid,
                miner_hotkey=hotkey,
                miner_coldkey=miner_raw.get("coldkey"),
                agent_key=miner_raw.get("agent_key"),
                agent_name=miner_raw.get("agent_name") or miner_raw.get("name") or "",
                image_url=miner_raw.get("agent_image") or miner_raw.get("image"),
                github_url=miner_raw.get("github"),
                provider=miner_raw.get("provider"),
                description=miner_raw.get("description"),
                is_sota=bool(miner_raw.get("is_sota")),
                first_seen_at=miner_raw.get("first_seen_at"),
                last_seen_at=miner_raw.get("last_seen_at"),
                metadata=miner_raw.get("metadata") or {},
            )
        )

    agent_runs_raw = []
    for run_raw in payload.agent_evaluation_runs:
        run_copy = dict(run_raw)
        run_copy.setdefault("validator_hotkey", validator_round.validator_hotkey)
        agent_runs_raw.append(run_copy)
    agent_evaluation_runs = [AgentEvaluationRun(**run_raw) for run_raw in agent_runs_raw]
    agent_run_map = {run.agent_run_id: run for run in agent_evaluation_runs}

    tasks = [Task(**task_raw) for task_raw in payload.tasks]
    task_map = {task.task_id: task for task in tasks}

    task_solutions_raw = []
    for solution_raw in payload.task_solutions:
        raw_copy = dict(solution_raw)
        raw_copy.setdefault("validator_hotkey", validator_round.validator_hotkey)
        miner_uid = raw_copy.get("miner_uid")
        if miner_uid is not None:
            raw_copy.setdefault("miner_hotkey", miner_hotkey_map.get(miner_uid))
        task_solutions_raw.append(raw_copy)
    task_solutions = [TaskSolution(**solution_raw) for solution_raw in task_solutions_raw]
    task_solution_map = {solution.solution_id: solution for solution in task_solutions}

    evaluation_results_raw = []
    for result_raw in payload.evaluation_results:
        raw_copy = dict(result_raw)
        raw_copy.setdefault("metadata", raw_copy.get("metadata") or {})
        raw_copy.setdefault(
            "result_id",
            f"{raw_copy.get('evaluation_id', str(uuid.uuid4()))}-result",
        )
        evaluation_results_raw.append(raw_copy)
    evaluation_results = [
        EvaluationResult(**result_raw) for result_raw in evaluation_results_raw
    ]

    evaluations: List[Evaluation] = []
    for result in evaluation_results:
        agent_run = agent_run_map.get(result.agent_run_id)
        if agent_run is None:
            raise HTTPException(
                status_code=400,
                detail=f"Evaluation result {result.evaluation_id} references unknown agent run {result.agent_run_id}",
            )
        task = task_map.get(result.task_id)
        if task is None:
            raise HTTPException(
                status_code=400,
                detail=f"Evaluation result {result.evaluation_id} references unknown task {result.task_id}",
            )
        task_solution = task_solution_map.get(result.task_solution_id)
        if task_solution is None:
            raise HTTPException(
                status_code=400,
                detail=f"Evaluation result {result.evaluation_id} references unknown solution {result.task_solution_id}",
            )
        validator_hotkey = validator_hotkey_map.get(result.validator_uid, validator_round.validator_hotkey)
        miner_hotkey = None
        if result.miner_uid is not None:
            miner_hotkey = miner_hotkey_map.get(result.miner_uid)
        evaluations.append(
            Evaluation(
                evaluation_id=result.evaluation_id,
                validator_round_id=result.validator_round_id,
                task_id=result.task_id,
                task_solution_id=result.task_solution_id,
                agent_run_id=result.agent_run_id,
                validator_uid=result.validator_uid,
                validator_hotkey=validator_hotkey,
                miner_uid=result.miner_uid,
                miner_hotkey=miner_hotkey,
                final_score=result.final_score,
                raw_score=result.raw_score,
                evaluation_time=result.evaluation_time,
                summary={
                    "test_results": result.test_results_matrix,
                    "execution_history": result.execution_history,
                },
            )
        )

    return ValidatorRoundSubmissionRequest(
        validator_identities=validator_identities,
        miner_identities=miner_identities,
        validator_round=validator_round,
        validator_snapshots=validator_snapshots,
        miner_snapshots=miner_snapshots,
        agent_evaluation_runs=agent_evaluation_runs,
        tasks=tasks,
        task_solutions=task_solutions,
        evaluations=evaluations,
        evaluation_results=evaluation_results,
    )


def _validate_round_relationships(payload: ValidatorRoundSubmissionRequest) -> None:
    """Validate referential integrity for the bulk submission."""
    validator_round: ValidatorRound = payload.validator_round
    round_id = validator_round.validator_round_id

    # Validator identities & snapshots
    validator_identity_map: Dict[tuple[int, str], Validator] = {
        (identity.uid, identity.hotkey): identity
        for identity in payload.validator_identities
    }
    if (validator_round.validator_uid, validator_round.validator_hotkey) not in validator_identity_map:
        raise HTTPException(
            status_code=400,
            detail="Primary validator identity missing from validator_identities",
        )

    for snapshot in payload.validator_snapshots:
        _require_round_match(
            snapshot.validator_round_id,
            round_id,
            "validator_snapshot.validator_round_id",
        )
        key = (snapshot.validator_uid, snapshot.validator_hotkey)
        if key not in validator_identity_map:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Validator snapshot references unknown identity: "
                    f"uid={snapshot.validator_uid}, hotkey={snapshot.validator_hotkey}"
                ),
            )

    # Miner identities & snapshots
    miner_identity_map: Dict[str, Miner] = {}
    for identity in payload.miner_identities:
        if identity.uid is not None and identity.hotkey:
            miner_identity_map[f"uid:{identity.uid}:{identity.hotkey}"] = identity
        if identity.agent_key:
            miner_identity_map[f"agent:{identity.agent_key}"] = identity

    for snapshot in payload.miner_snapshots:
        _require_round_match(
            snapshot.validator_round_id,
            round_id,
            "miner_snapshot.validator_round_id",
        )
        key = None
        if snapshot.miner_uid is not None and snapshot.miner_hotkey:
            key = f"uid:{snapshot.miner_uid}:{snapshot.miner_hotkey}"
        elif snapshot.agent_key:
            key = f"agent:{snapshot.agent_key}"
        if key is None or key not in miner_identity_map:
            raise HTTPException(
                status_code=400,
                detail="Miner snapshot references unknown or incomplete identity",
            )

    # Index core entities
    task_map = _index_tasks(payload.tasks)
    agent_run_map = _index_agent_runs(payload.agent_evaluation_runs)
    task_solution_map = _index_task_solutions(payload.task_solutions)
    evaluation_map = _index_evaluations(payload.evaluations)

    # Agent runs
    for agent_run in payload.agent_evaluation_runs:
        _require_round_match(
            agent_run.validator_round_id,
            round_id,
            "agent_run.validator_round_id",
        )
        if agent_run.validator_uid != validator_round.validator_uid:
            raise HTTPException(
                status_code=400,
                detail=f"Agent run {agent_run.agent_run_id} references unexpected validator UID",
            )

    # Tasks
    for task in payload.tasks:
        _require_round_match(
            task.validator_round_id,
            round_id,
            "task.validator_round_id",
        )

    # Task solutions
    for solution in payload.task_solutions:
        _require_round_match(
            solution.validator_round_id,
            round_id,
            "task_solution.validator_round_id",
        )
        task = task_map.get(solution.task_id)
        if not task:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Task solution {solution.solution_id} references "
                    f"unknown task {solution.task_id}"
                ),
            )
        agent_run = agent_run_map.get(solution.agent_run_id)
        if not agent_run:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Task solution {solution.solution_id} references "
                    f"unknown agent run {solution.agent_run_id}"
                ),
            )
        if not solution.validate_relationships(agent_run, task):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid relationships in task solution {solution.solution_id}",
            )

    # Evaluations
    for evaluation in payload.evaluations:
        _require_round_match(
            evaluation.validator_round_id,
            round_id,
            "evaluation.validator_round_id",
        )
        task = task_map.get(evaluation.task_id)
        if not task:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation {evaluation.evaluation_id} references "
                    f"unknown task {evaluation.task_id}"
                ),
            )
        task_solution = task_solution_map.get(evaluation.task_solution_id)
        if not task_solution:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation {evaluation.evaluation_id} references "
                    f"unknown task solution {evaluation.task_solution_id}"
                ),
            )
        agent_run = agent_run_map.get(evaluation.agent_run_id)
        if not agent_run:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation {evaluation.evaluation_id} references "
                    f"unknown agent run {evaluation.agent_run_id}"
                ),
            )
        if not evaluation.validate_relationships(agent_run, task, task_solution):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid relationships in evaluation {evaluation.evaluation_id}",
            )

    # Evaluation results
    for result in payload.evaluation_results:
        _require_round_match(
            result.validator_round_id,
            round_id,
            "evaluation_result.validator_round_id",
        )
        evaluation = evaluation_map.get(result.evaluation_id)
        if not evaluation:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation result {result.result_id} references "
                    f"unknown evaluation {result.evaluation_id}"
                ),
            )
        task = task_map.get(result.task_id)
        if not task:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation result {result.result_id} references "
                    f"unknown task {result.task_id}"
                ),
            )
        task_solution = task_solution_map.get(result.task_solution_id)
        if not task_solution:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation result {result.result_id} references "
                    f"unknown task solution {result.task_solution_id}"
                ),
            )
        agent_run = agent_run_map.get(result.agent_run_id)
        if not agent_run:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Evaluation result {result.result_id} references "
                    f"unknown agent run {result.agent_run_id}"
                ),
            )
        mismatches: List[str] = []
        if result.task_id != task.task_id:
            mismatches.append("task_id")
        if result.task_solution_id != task_solution.solution_id:
            mismatches.append("task_solution_id")
        if result.agent_run_id != agent_run.agent_run_id:
            mismatches.append("agent_run_id")
        if result.evaluation_id != evaluation.evaluation_id:
            mismatches.append("evaluation_id")
        if mismatches:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid relationships in evaluation result {result.result_id}: "
                    + ", ".join(mismatches)
                ),
            )


@router.post("/submit", response_model=ValidatorRoundSubmissionResponse)
async def submit_round_data(
    payload: Union[ValidatorRoundSubmissionRequest, LegacyRoundSubmissionPayload],
    session: AsyncSession = Depends(get_session),
):
    """
    Atomically persist a complete validator round with all related entities.
    """
    start_time = time.time()

    if isinstance(payload, LegacyRoundSubmissionPayload):
        payload = _legacy_to_submission_request(payload)

    try:
        logger.info(
            "Starting round submission for validator_round_id=%s",
            payload.validator_round.validator_round_id,
        )

        _validate_round_relationships(payload)

        service = ValidatorRoundPersistenceService(session)
        async with session.begin():
            result = await service.submit_round(payload)

        validator_uid = result.validator_uid
        saved_entities = result.saved_entities
        logger.info(
            "Saved round submission %s (runs=%d, tasks=%d, solutions=%d, evaluations=%d, evaluation_results=%d)",
            payload.validator_round.validator_round_id,
            len(saved_entities["agent_evaluation_runs"]),
            len(saved_entities["tasks"]),
            len(saved_entities["task_solutions"]),
            len(saved_entities["evaluations"]),
            len(saved_entities["evaluation_results"]),
        )

        processing_time = time.time() - start_time
        response = ValidatorRoundSubmissionResponse(
            success=True,
            message=(
                f"Successfully submitted round "
                f"{payload.validator_round.validator_round_id}"
            ),
            validator_round_id=payload.validator_round.validator_round_id,
            validator_uid=validator_uid,
            processing_time_seconds=processing_time,
            entities_saved=saved_entities,
            summary={
                "rounds": 1,
                "agent_evaluation_runs": len(payload.agent_evaluation_runs),
                "tasks": len(payload.tasks),
                "task_solutions": len(payload.task_solutions),
                "evaluations": len(payload.evaluations),
                "evaluation_results": len(payload.evaluation_results),
            },
        )

        logger.info(
            "Round submission completed in %.3fs for round %s",
            processing_time,
            payload.validator_round.validator_round_id,
        )
        return response

    except HTTPException:
        raise
    except DuplicateIdentifierError as exc:
        logger.error("Duplicate identifier in round submission: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RoundConflictError as exc:
        logger.error("Duplicate round submission blocked: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        logger.error("Validation error during round submission: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - catch-all for logging
        processing_time = time.time() - start_time
        logger.exception(
            "Error submitting round data (duration=%.3fs): %s",
            processing_time,
            exc,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to submit round data: {exc}"
        ) from exc

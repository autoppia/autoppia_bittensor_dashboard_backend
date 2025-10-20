"""
Progressive validator round ingestion endpoints aligned with the normalized models.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Union

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
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
    ValidatorRoundValidator,
)
from app.services.validator.validator_auth import require_validator_auth
from app.services.validator.validator_storage import (
    RoundConflictError,
    DuplicateIdentifierError,
    ValidatorRoundPersistenceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validator-rounds", tags=["validator-rounds"])


def _require_round_match(value: str, expected: str, field_name: str) -> str:
    if value != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} mismatch: got {value}, expected {expected}",
        )
    return value


class StartRoundRequest(BaseModel):
    validator_identity: Validator
    validator_round: ValidatorRound
    validator_snapshot: ValidatorRoundValidator

    @field_validator("validator_round")
    @classmethod
    def _ensure_round(cls, round_model: ValidatorRound) -> ValidatorRound:
        if round_model.round_number is None:
            raise ValueError("round_number is required to start a validator round")
        return round_model


class LegacyStartRoundRequest(BaseModel):
    validator_round_id: str
    round: Dict[str, Any]


def _legacy_to_start_request(payload: LegacyStartRoundRequest) -> StartRoundRequest:
    round_data = dict(payload.round)
    validators = round_data.pop("validators", []) or []
    if not validators:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="validators list is required in legacy payloads",
        )
    primary_validator = dict(validators[0])
    uid = primary_validator.get("uid")
    hotkey = primary_validator.get("hotkey")
    if uid is None or hotkey is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="validator uid and hotkey are required in legacy payloads",
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

    round_data.setdefault("validator_round_id", payload.validator_round_id)
    round_data.setdefault("validator_uid", uid)
    round_data.setdefault("validator_hotkey", hotkey)
    round_data.setdefault("validator_coldkey", primary_validator.get("coldkey"))

    validator_identity = Validator(
        uid=uid,
        hotkey=hotkey,
        coldkey=primary_validator.get("coldkey"),
    )
    validator_round = ValidatorRound(**round_data)
    validator_snapshot = ValidatorRoundValidator(
        validator_round_id=payload.validator_round_id,
        validator_uid=uid,
        validator_hotkey=hotkey,
        name=primary_validator.get("name"),
        stake=primary_validator.get("stake"),
        vtrust=primary_validator.get("vtrust"),
        image_url=primary_validator.get("image") or primary_validator.get("image_url"),
        version=primary_validator.get("version"),
    )
    return StartRoundRequest(
        validator_identity=validator_identity,
        validator_round=validator_round,
        validator_snapshot=validator_snapshot,
    )


class SetTasksRequest(BaseModel):
    tasks: list[Task] = Field(default_factory=list)


class StartAgentRunRequest(BaseModel):
    agent_run: AgentEvaluationRun
    miner_identity: Miner
    miner_snapshot: ValidatorRoundMiner


class AddEvaluationRequest(BaseModel):
    task: Task
    task_solution: TaskSolution
    evaluation: Evaluation
    evaluation_result: EvaluationResult


class LegacyStartAgentRunRequest(BaseModel):
    agent_run: Dict[str, Any]


async def _legacy_to_start_agent_run_request(
    validator_round_id: str,
    payload: LegacyStartAgentRunRequest,
    service: ValidatorRoundPersistenceService,
) -> StartAgentRunRequest:
    round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
    validator_hotkey = round_row.validator_hotkey
    validator_uid = round_row.validator_uid

    agent_run_data = dict(payload.agent_run)
    agent_run_data.setdefault("validator_round_id", validator_round_id)
    agent_run_data.setdefault("validator_uid", validator_uid)
    agent_run_data.setdefault("validator_hotkey", validator_hotkey)

    miner_info = dict(agent_run_data.get("miner_info") or {})
    if agent_run_data.get("is_sota"):
        agent_run_data.setdefault("miner_agent_key", miner_info.get("agent_key"))
    else:
        agent_run_data.setdefault("miner_uid", miner_info.get("uid"))
        agent_run_data.setdefault("miner_hotkey", miner_info.get("hotkey"))

    agent_run = AgentEvaluationRun(**agent_run_data)

    miner_identity = Miner(
        uid=miner_info.get("uid"),
        hotkey=miner_info.get("hotkey"),
        coldkey=miner_info.get("coldkey"),
        agent_key=miner_info.get("agent_key"),
    )

    miner_snapshot = ValidatorRoundMiner(
        validator_round_id=validator_round_id,
        miner_uid=miner_info.get("uid"),
        miner_hotkey=miner_info.get("hotkey"),
        miner_coldkey=miner_info.get("coldkey"),
        agent_key=miner_info.get("agent_key"),
        agent_name=miner_info.get("agent_name") or miner_info.get("name") or agent_run.agent_run_id,
        image_url=miner_info.get("agent_image") or miner_info.get("image"),
        github_url=miner_info.get("github"),
        provider=miner_info.get("provider"),
        description=miner_info.get("description"),
        is_sota=bool(miner_info.get("is_sota")),
        metadata=miner_info.get("metadata") or {},
    )

    return StartAgentRunRequest(
        agent_run=agent_run,
        miner_identity=miner_identity,
        miner_snapshot=miner_snapshot,
    )


class LegacyAddEvaluationRequest(BaseModel):
    task: Dict[str, Any]
    task_solution: Dict[str, Any]
    evaluation: Dict[str, Any] = Field(default_factory=dict)
    evaluation_result: Dict[str, Any]


async def _legacy_to_add_evaluation_request(
    validator_round_id: str,
    agent_run_id: str,
    payload: LegacyAddEvaluationRequest,
    service: ValidatorRoundPersistenceService,
) -> AddEvaluationRequest:
    round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
    agent_run_row = await service._get_agent_run_row(agent_run_id)  # type: ignore[attr-defined]
    if agent_run_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent run {agent_run_id} not found",
        )

    validator_uid = round_row.validator_uid
    validator_hotkey = round_row.validator_hotkey
    miner_uid = agent_run_row.miner_uid
    miner_hotkey = agent_run_row.miner_hotkey

    task_data = dict(payload.task)
    task_data.setdefault("validator_round_id", validator_round_id)
    task = Task(**task_data)

    task_solution_data = dict(payload.task_solution)
    task_solution_data.setdefault("validator_round_id", validator_round_id)
    task_solution_data.setdefault("validator_uid", validator_uid)
    task_solution_data.setdefault("validator_hotkey", validator_hotkey)
    if miner_uid is not None:
        task_solution_data.setdefault("miner_uid", miner_uid)
        task_solution_data.setdefault("miner_hotkey", miner_hotkey)
    task_solution = TaskSolution(**task_solution_data)

    evaluation_result_data = dict(payload.evaluation_result)
    evaluation_result_data.setdefault("validator_round_id", validator_round_id)
    evaluation_result_data.setdefault("validator_uid", validator_uid)
    evaluation_result_data.setdefault("validator_hotkey", validator_hotkey)
    if miner_uid is not None:
        evaluation_result_data.setdefault("miner_uid", miner_uid)
        evaluation_result_data.setdefault("miner_hotkey", miner_hotkey)
    evaluation_result = EvaluationResult(**evaluation_result_data)

    evaluation_data = dict(payload.evaluation or {})
    if not evaluation_data:
        evaluation_data["evaluation_id"] = evaluation_result.evaluation_id
        evaluation_data["task_id"] = task.task_id
        evaluation_data["task_solution_id"] = task_solution.solution_id
        evaluation_data["agent_run_id"] = agent_run_id
        evaluation_data["final_score"] = evaluation_result.final_score
        evaluation_data["raw_score"] = evaluation_result.raw_score
        evaluation_data["evaluation_time"] = evaluation_result.evaluation_time
        evaluation_data["summary"] = evaluation_result.meta if hasattr(evaluation_result, "meta") else {}
    evaluation_data.setdefault("validator_round_id", validator_round_id)
    evaluation_data.setdefault("validator_uid", validator_uid)
    evaluation_data.setdefault("validator_hotkey", validator_hotkey)
    if miner_uid is not None:
        evaluation_data.setdefault("miner_uid", miner_uid)
        evaluation_data.setdefault("miner_hotkey", miner_hotkey)
    evaluation = Evaluation(**evaluation_data)

    return AddEvaluationRequest(
        task=task,
        task_solution=task_solution,
        evaluation=evaluation,
        evaluation_result=evaluation_result,
    )


class FinishRoundAgentRun(BaseModel):
    agent_run_id: str
    rank: int | None = None
    weight: float | None = None


class FinishRoundRequest(BaseModel):
    status: str = Field(default="completed", description="Final status for the round")
    winners: list[Dict[str, Any]] = Field(default_factory=list)
    winner_scores: list[float] = Field(default_factory=list)
    weights: Dict[str, float] = Field(default_factory=dict)
    ended_at: float | None = Field(
        default=None, description="Epoch timestamp when the round finished"
    )
    summary: Dict[str, int] | None = Field(
        default=None, description="Optional summary metadata"
    )
    agent_runs: list[FinishRoundAgentRun] = Field(default_factory=list)


@router.post("/auth-check", dependencies=[Depends(require_validator_auth)])
async def validator_auth_check() -> dict[str, Any]:
    """Lightweight endpoint validators can call to verify auth headers before starting a round."""
    return {"message": "Validator authentication verified"}


@router.post("/start", dependencies=[Depends(require_validator_auth)])
async def start_round(
    payload: Union[StartRoundRequest, LegacyStartRoundRequest],
    session: AsyncSession = Depends(get_session),
):
    """Register a new validator round along with validator identity and snapshot."""

    if isinstance(payload, LegacyStartRoundRequest):
        payload = _legacy_to_start_request(payload)

    validator_round = payload.validator_round
    validator_identity = payload.validator_identity
    validator_snapshot = payload.validator_snapshot

    if validator_round.validator_uid != validator_identity.uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="validator_round.validator_uid must match validator_identity.uid",
        )
    if validator_round.validator_hotkey != validator_identity.hotkey:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="validator_round.validator_hotkey must match validator_identity.hotkey",
        )

    _require_round_match(
        validator_snapshot.validator_round_id,
        validator_round.validator_round_id,
        "validator_snapshot.validator_round_id",
    )
    if (
        validator_snapshot.validator_uid != validator_round.validator_uid
        or validator_snapshot.validator_hotkey != validator_round.validator_hotkey
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Validator snapshot identity does not match validator round metadata",
        )

    service = ValidatorRoundPersistenceService(session)

    try:
        async with session.begin():
            await service.start_round(
                validator_identity=validator_identity,
                validator_round=validator_round,
                validator_snapshot=validator_snapshot,
            )
    except DuplicateIdentifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RoundConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "Started validator round %s (round_number=%s, validator_uid=%s)",
        validator_round.validator_round_id,
        validator_round.round_number,
        validator_round.validator_uid,
    )
    return {
        "message": "Validator round created",
        "validator_round_id": validator_round.validator_round_id,
    }


@router.post(
    "/{validator_round_id}/tasks",
    dependencies=[Depends(require_validator_auth)],
)
async def set_tasks(
    validator_round_id: str,
    payload: SetTasksRequest,
    session: AsyncSession = Depends(get_session),
):
    """Add or replace task definitions for a validator round."""
    for task in payload.tasks:
        _require_round_match(
            task.validator_round_id,
            validator_round_id,
            "task.validator_round_id",
        )

    service = ValidatorRoundPersistenceService(session)

    try:
        async with session.begin():
            count = await service.add_tasks(validator_round_id, payload.tasks)
    except DuplicateIdentifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "Stored %d tasks for validator round %s", count, validator_round_id
    )
    return {"message": "Tasks stored", "count": count}


@router.post(
    "/{validator_round_id}/agent-runs/start",
    dependencies=[Depends(require_validator_auth)],
)
async def start_agent_run(
    validator_round_id: str,
    payload: Union[StartAgentRunRequest, LegacyStartAgentRunRequest],
    session: AsyncSession = Depends(get_session),
):
    """Register the beginning of an agent evaluation run."""
    service = ValidatorRoundPersistenceService(session)

    try:
        async with session.begin():
            request_payload = payload
            if isinstance(request_payload, LegacyStartAgentRunRequest):
                request_payload = await _legacy_to_start_agent_run_request(validator_round_id, request_payload, service)

            agent_run = request_payload.agent_run
            _require_round_match(
                agent_run.validator_round_id,
                validator_round_id,
                "agent_run.validator_round_id",
            )

            miner_snapshot = request_payload.miner_snapshot
            _require_round_match(
                miner_snapshot.validator_round_id,
                validator_round_id,
                "miner_snapshot.validator_round_id",
            )

            if agent_run.is_sota:
                if agent_run.miner_agent_key is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="miner_agent_key is required for SOTA runs",
                    )
            else:
                if agent_run.miner_uid is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="miner_uid is required for non-SOTA runs",
                    )

            await service.start_agent_run(
                validator_round_id=validator_round_id,
                agent_run=agent_run,
                miner_identity=request_payload.miner_identity,
                miner_snapshot=miner_snapshot,
            )
    except DuplicateIdentifierError as exc:
        existing_run = await service._get_agent_run_row(agent_run.agent_run_id)  # type: ignore[attr-defined]
        if existing_run is not None:
            if (
                existing_run.validator_round_id == validator_round_id
                and existing_run.validator_uid == agent_run.validator_uid
                and existing_run.validator_hotkey == agent_run.validator_hotkey
            ):
                logger.info(
                    "Agent run %s already registered; treating as idempotent registration",
                    agent_run.agent_run_id,
                )
                return {
                    "message": "Agent run registered",
                    "agent_run_id": agent_run.agent_run_id,
                }
            logger.warning(
                "agent_run_id %s already bound to validator_round %s (requested %s)",
                agent_run.agent_run_id,
                existing_run.validator_round_id,
                validator_round_id,
            )
            detail = (
                f"agent_run_id {agent_run.agent_run_id} is already registered "
                f"to validator_round {existing_run.validator_round_id}"
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RoundConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "Registered agent run %s (validator_round_id=%s)",
        agent_run.agent_run_id,
        validator_round_id,
    )
    return {"message": "Agent run registered", "agent_run_id": agent_run.agent_run_id}


@router.post(
    "/{validator_round_id}/agent-runs/{agent_run_id}/evaluations",
    dependencies=[Depends(require_validator_auth)],
)
async def add_evaluation(
    validator_round_id: str,
    agent_run_id: str,
    payload: Union[AddEvaluationRequest, LegacyAddEvaluationRequest],
    session: AsyncSession = Depends(get_session),
):
    """Persist evaluation data (task, solution, evaluation record, and artefact)."""
    service = ValidatorRoundPersistenceService(session)

    try:
        async with session.begin():
            request_payload = payload
            if isinstance(request_payload, LegacyAddEvaluationRequest):
                request_payload = await _legacy_to_add_evaluation_request(
                    validator_round_id,
                    agent_run_id,
                    request_payload,
                    service,
                )

            task = request_payload.task
            task_solution = request_payload.task_solution
            evaluation = request_payload.evaluation
            evaluation_result = request_payload.evaluation_result

            expected_fields = [
                (task.validator_round_id, "task.validator_round_id"),
                (task_solution.validator_round_id, "task_solution.validator_round_id"),
                (evaluation.validator_round_id, "evaluation.validator_round_id"),
                (evaluation_result.validator_round_id, "evaluation_result.validator_round_id"),
            ]
            for value, label in expected_fields:
                _require_round_match(value, validator_round_id, label)

            _require_round_match(task_solution.task_id, task.task_id, "task_solution.task_id")
            _require_round_match(evaluation.task_id, task.task_id, "evaluation.task_id")
            _require_round_match(
                evaluation_result.task_id, task.task_id, "evaluation_result.task_id"
            )
            _require_round_match(
                task_solution.agent_run_id, agent_run_id, "task_solution.agent_run_id"
            )
            _require_round_match(
                evaluation.agent_run_id, agent_run_id, "evaluation.agent_run_id"
            )
            _require_round_match(
                evaluation_result.agent_run_id,
                agent_run_id,
                "evaluation_result.agent_run_id",
            )
            _require_round_match(
                evaluation.task_solution_id,
                task_solution.solution_id,
                "evaluation.task_solution_id",
            )
            _require_round_match(
                evaluation_result.task_solution_id,
                task_solution.solution_id,
                "evaluation_result.task_solution_id",
            )
            _require_round_match(
                evaluation_result.evaluation_id,
                evaluation.evaluation_id,
                "evaluation_result.evaluation_id",
            )

            await service.add_evaluation(
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                task=task,
                task_solution=task_solution,
                evaluation=evaluation,
                evaluation_result=evaluation_result,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "Stored evaluation %s for task %s (agent_run_id=%s)",
        evaluation.evaluation_id,
        task.task_id,
        agent_run_id,
    )
    return {"message": "Evaluation stored", "evaluation_id": evaluation.evaluation_id}


@router.post(
    "/{validator_round_id}/finish",
    dependencies=[Depends(require_validator_auth)],
)
async def finish_round(
    validator_round_id: str,
    payload: FinishRoundRequest,
    session: AsyncSession = Depends(get_session),
):
    """Mark a validator round as finished and persist summary data."""
    end_timestamp = payload.ended_at or time.time()

    service = ValidatorRoundPersistenceService(session)

    async with session.begin():
        await service.finish_round(
            validator_round_id=validator_round_id,
            status=payload.status,
            winners=payload.winners,
            winner_scores=payload.winner_scores,
            weights=payload.weights,
            ended_at=end_timestamp,
            summary=payload.summary,
            agent_runs=[item.model_dump(exclude_none=True) for item in payload.agent_runs] or None,
        )

    logger.info("Finished validator round %s", validator_round_id)
    return {
        "message": "Validator round finalized",
        "validator_round_id": validator_round_id,
    }

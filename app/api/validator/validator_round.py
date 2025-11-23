"""
Progressive validator round ingestion endpoints aligned with the normalized models.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Union
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from app.services.validator.validator_auth import (
    require_validator_auth,
    VALIDATOR_HOTKEY_HEADER,
)
from app.data import get_validator_metadata
from app.services.chain_state import get_current_block
from app.services.round_calc import (
    compute_round_number,
    compute_boundaries_for_round,
    is_inside_window,
)
from app.config import settings
from app.utils.images import (
    resolve_agent_image,
    resolve_validator_image,
    sanitize_miner_image,
)
from app.services.snapshot_service import SnapshotService
from app.services.validator.validator_storage import (
    RoundConflictError,
    DuplicateIdentifierError,
    ValidatorRoundPersistenceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validator-rounds", tags=["validator-rounds"])


def _ensure_request_matches_round_owner(request: Request, round_row: Any) -> None:
    """Ensure authenticated validator hotkey matches the round owner."""
    header_hotkey = request.headers.get(VALIDATOR_HOTKEY_HEADER)
    if not header_hotkey:
        # When auth is disabled in tests we do not enforce the check
        return
    round_hotkey = getattr(round_row, "validator_hotkey", None)
    if round_hotkey and header_hotkey != round_hotkey:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Validator hotkey header does not match round owner",
        )


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


def _resolve_miner_snapshot_image(snapshot: ValidatorRoundMiner) -> None:
    """
    Normalize miner snapshot images so we persist full URLs and apply fallbacks.
    """
    sanitized = sanitize_miner_image(snapshot.image_url)
    info = SimpleNamespace(
        agent_image=sanitized or "",
        is_sota=bool(snapshot.is_sota),
        agent_name=(snapshot.agent_name or "").strip(),
        hotkey=snapshot.miner_hotkey,
        uid=snapshot.miner_uid,
    )
    resolved = resolve_agent_image(info, existing=sanitized or None)
    if not resolved:
        resolved = resolve_agent_image(info)
    snapshot.image_url = resolved


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
            round_data["status"] = "finished"
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
    # Use canonical directory as FALLBACK only (don't override validator-provided values)
    metadata = get_validator_metadata(uid)
    # Only use directory name if validator didn't provide one
    if not validator_snapshot.name:
        validator_snapshot.name = metadata.get("name")
    # Only use directory image as fallback
    if not validator_snapshot.image_url:
        validator_snapshot.image_url = metadata.get("image")
    # Resolve/validate the final image URL
    validator_snapshot.image_url = resolve_validator_image(
        validator_snapshot.name,
        existing=validator_snapshot.image_url,
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
    if not agent_run_data.get("is_sota"):
        agent_run_data.setdefault("miner_uid", miner_info.get("uid"))
        agent_run_data.setdefault("miner_hotkey", miner_info.get("hotkey"))

    agent_run = AgentEvaluationRun(**agent_run_data)

    miner_identity = Miner(
        uid=miner_info.get("uid"),
        hotkey=miner_info.get("hotkey"),
        coldkey=miner_info.get("coldkey"),
    )

    miner_snapshot = ValidatorRoundMiner(
        validator_round_id=validator_round_id,
        miner_uid=miner_info.get("uid"),
        miner_hotkey=miner_info.get("hotkey"),
        miner_coldkey=miner_info.get("coldkey"),
        agent_name=miner_info.get("agent_name")
        or miner_info.get("name")
        or agent_run.agent_run_id,
        image_url=miner_info.get("agent_image") or miner_info.get("image"),
        github_url=miner_info.get("github"),
        description=miner_info.get("description"),
        is_sota=bool(miner_info.get("is_sota")),
    )
    # Canonicalize miner image asset (allowlist + fallback)
    _resolve_miner_snapshot_image(miner_snapshot)

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

    # Normalize IWAP raw actions into core {type, attributes} shape
    try:
        raw_actions = task_solution_data.get("actions")
        if isinstance(raw_actions, list):
            normalized_actions = []
            for a in raw_actions:
                if not isinstance(a, dict):
                    normalized_actions.append({"type": str(a), "attributes": {}})
                    continue
                raw_type = str(a.get("type", "other") or "other")
                normalized_type = (
                    raw_type.lower().replace("action", "").replace("-", "_").strip()
                    or "other"
                )
                # Prefer semantic name for text entry over ambiguous "type"
                alias_map = {
                    "type": "input",
                    "type_text": "input",
                    "sendkeysiwa": "input",
                }
                normalized_type = alias_map.get(normalized_type, normalized_type)
                attrs = {k: v for k, v in a.items() if k != "type"}
                normalized_actions.append(
                    {"type": normalized_type, "attributes": attrs}
                )
            task_solution_data["actions"] = normalized_actions
    except Exception:
        # Leave original shape on failure
        pass

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
        evaluation_data["summary"] = (
            evaluation_result.meta if hasattr(evaluation_result, "meta") else {}
        )
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
    status: str = Field(default="finished", description="Final status for the round")
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
    request: Request,
    force: bool = Query(
        False, description="TESTING-only override to skip chain round/window checks"
    ),
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

    # Ensure payload identity matches validator auth header hotkey (if provided)
    header_hotkey = request.headers.get(VALIDATOR_HOTKEY_HEADER)
    if header_hotkey and header_hotkey != validator_identity.hotkey:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Validator header hotkey does not match payload hotkey",
        )

    # ALWAYS enforce chain-derived round constraints (no bypass allowed)
    # This ensures ALL validators use the same DZ_STARTING_BLOCK and round calculation
    current_block = get_current_block()
    if current_block is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chain state unavailable",
        )

    backend_round_number = compute_round_number(current_block)
    if validator_round.round_number != backend_round_number:
        logger.error(
            "Round number mismatch: validator sent round %s but backend expects round %s (block=%s, DZ_STARTING_BLOCK=%s)",
            validator_round.round_number,
            backend_round_number,
            current_block,
            settings.DZ_STARTING_BLOCK,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "round_number mismatch - validator must use same DZ_STARTING_BLOCK as backend",
                "expectedRoundNumber": backend_round_number,
                "receivedRoundNumber": validator_round.round_number,
                "currentBlock": current_block,
                "backendDzStartingBlock": settings.DZ_STARTING_BLOCK,
                "message": "Update your validator to use DZ_STARTING_BLOCK="
                + str(settings.DZ_STARTING_BLOCK),
            },
        )

    bounds = compute_boundaries_for_round(backend_round_number)

    # Allow testing override ONLY for window timing, not round number validation
    testing_override = settings.TESTING and bool(force)
    if testing_override:
        logger.warning(
            "TESTING override enabled: skipping window check for validator_round_id=%s with round_number=%s",
            validator_round.validator_round_id,
            validator_round.round_number,
        )
    elif not is_inside_window(current_block, bounds):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "round window not active",
                "currentBlock": current_block,
                "startBlock": bounds.start_block,
                "endBlock": bounds.end_block,
            },
        )

    # Override payload boundaries to chain-derived values unless testing override is enabled
    if not testing_override and bounds is not None:
        validator_round.round_number = backend_round_number
        validator_round.start_block = bounds.start_block
        validator_round.end_block = bounds.end_block
        validator_round.start_epoch = int(bounds.start_epoch)
        validator_round.end_epoch = int(bounds.end_epoch)
        validator_round.max_epochs = int(settings.ROUND_SIZE_EPOCHS)
        validator_round.max_blocks = settings.BLOCKS_PER_EPOCH

    # Use canonical directory as FALLBACK only (don't override validator-provided values)
    try:
        directory = get_validator_metadata(int(validator_identity.uid))  # type: ignore[arg-type]
    except Exception:
        directory = {}
    if directory:
        # Only use directory name if validator didn't provide one
        if not validator_snapshot.name:
            validator_snapshot.name = directory.get("name")
        # Only use directory image as fallback
        if not validator_snapshot.image_url:
            validator_snapshot.image_url = directory.get("image")

    # Resolve/validate the final image URL
    validator_snapshot.image_url = resolve_validator_image(
        validator_snapshot.name,
        existing=validator_snapshot.image_url,
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
        # Treat duplicate start as idempotent if it belongs to the same validator
        try:
            existing_round = await service._get_round_row(validator_round.validator_round_id)  # type: ignore[attr-defined]
        except Exception:
            existing_round = None
        if existing_round is not None:
            if (
                existing_round.validator_uid == validator_round.validator_uid
                and existing_round.validator_hotkey == validator_round.validator_hotkey
            ):
                logger.info(
                    "Validator round %s already registered; treating as idempotent",
                    validator_round.validator_round_id,
                )
                return {
                    "message": "Validator round created",
                    "validator_round_id": validator_round.validator_round_id,
                }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RoundConflictError as exc:
        # Idempotency: if a round with this (validator_uid, round_number) already exists,
        # return its validator_round_id without performing writes.
        try:
            existing = await service.get_round_by_validator_and_number(
                validator_uid=int(validator_round.validator_uid),  # type: ignore[arg-type]
                round_number=int(validator_round.round_number),  # type: ignore[arg-type]
            )
        except Exception:
            existing = None
        if existing is not None:
            logger.info(
                "Validator %s already has round_number=%s; returning existing round_id=%s idempotently",
                validator_round.validator_uid,
                validator_round.round_number,
                existing.validator_round_id,
            )
            return {
                "message": "Validator round created",
                "validator_round_id": existing.validator_round_id,
            }
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
    request: Request,
    force: bool = Query(
        False, description="TESTING-only override to skip chain round/window checks"
    ),
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
        # Validate ownership and chain window outside the transaction
        round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
        _ensure_request_matches_round_owner(request, round_row)

        testing_override = settings.TESTING and bool(force)
        if testing_override:
            logger.warning(
                "TESTING override enabled: accepting set_tasks for validator_round_id=%s without chain checks",
                validator_round_id,
            )
        else:
            current_block = get_current_block()
            if current_block is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Chain state unavailable",
                )

            stored_round_number = int(getattr(round_row, "round_number", 0) or 0)
            backend_round_number = compute_round_number(current_block)
            if stored_round_number != backend_round_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "round_number mismatch",
                        "expectedRoundNumber": backend_round_number,
                        "got": stored_round_number,
                    },
                )

            bounds = compute_boundaries_for_round(backend_round_number)
            if not is_inside_window(current_block, bounds):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "round window not active",
                        "currentBlock": current_block,
                        "startBlock": bounds.start_block,
                        "endBlock": bounds.end_block,
                    },
                )

        # Idempotent: allow existing tasks to be skipped silently
        # Session already has a transaction from get_session dependency
        count = await service.add_tasks(
            validator_round_id, payload.tasks, allow_existing=True
        )
        await session.commit()
    except DuplicateIdentifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info("Stored %d tasks for validator round %s", count, validator_round_id)
    return {"message": "Tasks stored", "count": count}


@router.post(
    "/{validator_round_id}/agent-runs/start",
    dependencies=[Depends(require_validator_auth)],
)
async def start_agent_run(
    validator_round_id: str,
    payload: Union[StartAgentRunRequest, LegacyStartAgentRunRequest],
    request: Request,
    force: bool = Query(
        False, description="TESTING-only override to skip chain round/window checks"
    ),
    session: AsyncSession = Depends(get_session),
):
    """Register the beginning of an agent evaluation run."""
    service = ValidatorRoundPersistenceService(session)

    try:
        request_payload = payload
        if isinstance(request_payload, LegacyStartAgentRunRequest):
            request_payload = await _legacy_to_start_agent_run_request(
                validator_round_id, request_payload, service
            )

        agent_run = request_payload.agent_run
        _require_round_match(
            agent_run.validator_round_id,
            validator_round_id,
            "agent_run.validator_round_id",
        )

        # Early idempotency: if this agent_run already exists for this round and validator,
        # return 200 without enforcing window checks. This enables safe replays even if
        # the validator has moved past the active window.
        existing_run = await service._get_agent_run_row(agent_run.agent_run_id)  # type: ignore[attr-defined]
        if (
            existing_run is not None
            and existing_run.validator_round_id == validator_round_id
            and (
                existing_run.validator_uid is None
                or agent_run.validator_uid is None
                or int(existing_run.validator_uid) == int(agent_run.validator_uid)
            )
            and (
                not existing_run.validator_hotkey
                or not agent_run.validator_hotkey
                or existing_run.validator_hotkey == agent_run.validator_hotkey
            )
        ):
            logger.info(
                "Agent run %s already registered (round %s); treating as idempotent",
                agent_run.agent_run_id,
                validator_round_id,
            )
            return {
                "message": "Agent run registered",
                "agent_run_id": agent_run.agent_run_id,
            }

        # Ensure agent_run validator identity matches the round's registered validator
        round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
        _ensure_request_matches_round_owner(request, round_row)
        if (
            agent_run.validator_uid is not None
            and round_row.validator_uid is not None
            and int(agent_run.validator_uid) != int(round_row.validator_uid)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="agent_run.validator_uid must match the round's validator_uid",
            )
        if (
            agent_run.validator_hotkey
            and round_row.validator_hotkey
            and agent_run.validator_hotkey != round_row.validator_hotkey
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="agent_run.validator_hotkey must match the round's validator_hotkey",
            )

        miner_snapshot = request_payload.miner_snapshot
        # Canonicalize miner image on non-legacy path as well
        _resolve_miner_snapshot_image(miner_snapshot)

        # ALWAYS enforce chain-derived round constraints (no bypass allowed)
        current_block = get_current_block()
        if current_block is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Chain state unavailable",
            )
        backend_round_number = compute_round_number(current_block)
        stored_round_number = int(getattr(round_row, "round_number", 0) or 0)
        if stored_round_number != backend_round_number:
            logger.error(
                "Round number mismatch in agent_run: stored round %s but backend expects round %s (block=%s)",
                stored_round_number,
                backend_round_number,
                current_block,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "round_number mismatch - validator must use same DZ_STARTING_BLOCK as backend",
                    "expectedRoundNumber": backend_round_number,
                    "storedRoundNumber": stored_round_number,
                    "currentBlock": current_block,
                    "backendDzStartingBlock": settings.DZ_STARTING_BLOCK,
                },
            )

        bounds = compute_boundaries_for_round(backend_round_number)

        # Allow testing override ONLY for window timing, not round number validation
        testing_override = settings.TESTING and bool(force)
        if testing_override:
            logger.warning(
                "TESTING override enabled: skipping window check for validator_round_id=%s",
                validator_round_id,
            )
        elif not is_inside_window(current_block, bounds):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "round window not active",
                    "currentBlock": current_block,
                    "startBlock": bounds.start_block,
                    "endBlock": bounds.end_block,
                },
            )
        _require_round_match(
            miner_snapshot.validator_round_id,
            validator_round_id,
            "miner_snapshot.validator_round_id",
        )

        if not agent_run.is_sota:
            if agent_run.miner_uid is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_uid is required for non-SOTA runs",
                )
            # For non-SOTA, ensure miner uid/hotkey are consistent between all payload parts
            identity = request_payload.miner_identity
            if identity.uid is None or not identity.hotkey:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_identity must include uid and hotkey for non-SOTA runs",
                )
            expected_uid = agent_run.miner_uid
            expected_hotkey = agent_run.miner_hotkey
            if expected_uid != identity.uid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_identity.uid must match agent_run.miner_uid",
                )
            if (
                expected_hotkey
                and identity.hotkey
                and expected_hotkey != identity.hotkey
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_identity.hotkey must match agent_run.miner_hotkey",
                )
            # Snapshot consistency (if provided)
            if (
                miner_snapshot.miner_uid is not None
                and miner_snapshot.miner_uid != expected_uid
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_snapshot.miner_uid must match agent_run.miner_uid",
                )
            if (
                miner_snapshot.miner_hotkey is not None
                and expected_hotkey is not None
                and miner_snapshot.miner_hotkey != expected_hotkey
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_snapshot.miner_hotkey must match agent_run.miner_hotkey",
                )

        # Persist only inside the transaction block
        async with session.begin():
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from exc
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
    request: Request,
    force: bool = Query(
        False, description="TESTING-only override to skip chain round/window checks"
    ),
    session: AsyncSession = Depends(get_session),
):
    """Persist evaluation data (task, solution, evaluation record, and artefact)."""
    service = ValidatorRoundPersistenceService(session)

    try:
        request_payload = payload
        if isinstance(request_payload, LegacyAddEvaluationRequest):
            request_payload = await _legacy_to_add_evaluation_request(
                validator_round_id,
                agent_run_id,
                request_payload,
                service,
            )

        # Heuristic: if actions reached here as AddEvaluationRequest but lost fields
        # (attributes empty), rebuild attributes from raw JSON body before persisting.
        try:
            if not isinstance(payload, LegacyAddEvaluationRequest):
                raw_json = await request.json()
                raw_ts = (raw_json or {}).get("task_solution") or {}
                raw_actions = (
                    raw_ts.get("actions") if isinstance(raw_ts, dict) else None
                )
                ts = getattr(request_payload, "task_solution", None)
                if raw_actions and ts and isinstance(ts.actions, list):
                    from app.models.core import (
                        Action as CoreAction,
                        TaskSolution as CoreTaskSolution,
                    )

                    def _norm_type(t: str) -> str:
                        key = (t or "other").lower().replace("action", "").replace(
                            "-", "_"
                        ).strip() or "other"
                        if key in {"type", "type_text", "sendkeysiwa"}:
                            return "input"
                        return key

                    new_actions = []
                    for idx, ra in enumerate(raw_actions):
                        if isinstance(ra, dict):
                            rtype = _norm_type(str(ra.get("type", "other") or "other"))
                            attrs = {k: v for k, v in ra.items() if k != "type"}
                            new_actions.append(CoreAction(type=rtype, attributes=attrs))
                        else:
                            new_actions.append(CoreAction(type=str(ra), attributes={}))

                    # Replace only if current attrs look empty
                    if all(
                        (
                            getattr(a, "attributes", None) in (None, {}, [])
                            for a in ts.actions
                        )
                    ):
                        request_payload = AddEvaluationRequest(
                            task=request_payload.task,
                            task_solution=CoreTaskSolution(
                                **{
                                    **request_payload.task_solution.model_dump(
                                        mode="json"
                                    ),
                                    "actions": [
                                        a.model_dump(mode="json") for a in new_actions
                                    ],
                                }
                            ),
                            evaluation=request_payload.evaluation,
                            evaluation_result=request_payload.evaluation_result,
                        )
        except Exception:
            # Non-fatal: fall back to original payload
            pass

        task = request_payload.task
        task_solution = request_payload.task_solution
        evaluation = request_payload.evaluation
        evaluation_result = request_payload.evaluation_result

        expected_fields = [
            (task.validator_round_id, "task.validator_round_id"),
            (task_solution.validator_round_id, "task_solution.validator_round_id"),
            (evaluation.validator_round_id, "evaluation.validator_round_id"),
            (
                evaluation_result.validator_round_id,
                "evaluation_result.validator_round_id",
            ),
        ]
        for value, label in expected_fields:
            _require_round_match(value, validator_round_id, label)

        _require_round_match(
            task_solution.task_id, task.task_id, "task_solution.task_id"
        )
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

        # Cross-check validator identity on payloads matches the round
        round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
        _ensure_request_matches_round_owner(request, round_row)
        check_pairs = [
            (
                task_solution.validator_uid,
                task_solution.validator_hotkey,
                "task_solution",
            ),
            (evaluation.validator_uid, evaluation.validator_hotkey, "evaluation"),
            (evaluation_result.validator_uid, None, "evaluation_result"),
        ]
        for uid_value, hotkey_value, label in check_pairs:
            if uid_value is not None and int(uid_value) != int(round_row.validator_uid):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{label}.validator_uid must match the round's validator_uid",
                )
            if hotkey_value and hotkey_value != round_row.validator_hotkey:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{label}.validator_hotkey must match the round's validator_hotkey",
                )

        # Early idempotency: if the entire bundle already exists for this round/run,
        # return success before enforcing window checks to allow safe replays.
        try:
            existing_solution = await service.get_task_solution_row(
                task_solution.solution_id
            )
            existing_eval = await service.get_evaluation_row(evaluation.evaluation_id)
            existing_result = await service.get_evaluation_result_row(
                evaluation_result.result_id
            )
        except Exception:
            existing_solution = existing_eval = existing_result = None
        if (
            existing_solution
            and existing_eval
            and existing_result
            and str(existing_solution.validator_round_id) == str(validator_round_id)
            and str(existing_eval.validator_round_id) == str(validator_round_id)
            and str(existing_result.validator_round_id) == str(validator_round_id)
            and str(existing_solution.agent_run_id) == str(agent_run_id)
            and str(existing_eval.agent_run_id) == str(agent_run_id)
            and str(existing_result.agent_run_id) == str(agent_run_id)
        ):
            logger.info(
                "Evaluation %s already stored for round %s (agent_run_id=%s); treating as idempotent",
                existing_eval.evaluation_id,
                validator_round_id,
                agent_run_id,
            )
            return {
                "message": "Evaluation stored",
                "evaluation_id": existing_eval.evaluation_id,
            }

        # Enforce chain-derived round window for this submission unless testing override is enabled
        testing_override = settings.TESTING and bool(force)
        if testing_override:
            logger.warning(
                "TESTING override enabled: accepting add_evaluation for validator_round_id=%s without chain checks",
                validator_round_id,
            )
        else:
            current_block = get_current_block()
            if current_block is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Chain state unavailable",
                )
            backend_round_number = compute_round_number(current_block)
            stored_round_number = int(getattr(round_row, "round_number", 0) or 0)
            if stored_round_number != backend_round_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "round_number mismatch",
                        "expectedRoundNumber": backend_round_number,
                        "got": stored_round_number,
                    },
                )
            bounds = compute_boundaries_for_round(backend_round_number)
            if not is_inside_window(current_block, bounds):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "round window not active",
                        "currentBlock": current_block,
                        "startBlock": bounds.start_block,
                        "endBlock": bounds.end_block,
                    },
                )

        # Persist inside a short transaction
        async with session.begin():
            await service.upsert_evaluation_bundle(
                validator_round_id=validator_round_id,
                agent_run_id=agent_run_id,
                task=task,
                task_solution=task_solution,
                evaluation=evaluation,
                evaluation_result=evaluation_result,
            )
    except DuplicateIdentifierError as exc:
        # Conflicting duplicate (belongs to another round/run), surface as 409
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
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

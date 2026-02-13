"""
Progressive validator round ingestion endpoints aligned with the normalized models.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict
from types import SimpleNamespace

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
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
    is_inside_window,
)
from app.config import settings
from app.utils.images import (
    resolve_agent_image,
    resolve_validator_image,
    sanitize_miner_image,
)

# Snapshot functionality removed
# from app.services.snapshot_service import SnapshotService
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
    # Access validator_hotkey through validator_snapshot (1:1 relationship)
    round_hotkey = None
    if hasattr(round_row, "validator_snapshot") and round_row.validator_snapshot:
        round_hotkey = round_row.validator_snapshot.validator_hotkey
    elif hasattr(round_row, "validator_hotkey"):
        # Fallback for backwards compatibility
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
        # Validate season and round fields are present
        if round_model.season_number is None:
            raise ValueError("season_number is required to start a validator round")
        if round_model.round_number_in_season is None:
            raise ValueError("round_number_in_season is required to start a validator round")

        # Validate that the season and round match the start_block
        from app.services.round_calc import compute_season_number

        expected_season = compute_season_number(round_model.start_block)
        if round_model.season_number != expected_season:
            raise ValueError(f"season_number mismatch: got {round_model.season_number}, expected {expected_season} for start_block {round_model.start_block}")

        return round_model


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
    evaluation_result: Dict[str, Any] | None = None


class FinishRoundAgentRun(BaseModel):
    agent_run_id: str
    rank: int | None = None
    weight: float | None = None
    # FASE 1: Nuevos campos opcionales
    miner_name: str | None = None
    avg_reward: float | None = None  # Average reward (eval_score + time_score)
    avg_evaluation_time: float | None = None
    tasks_attempted: int | None = None
    tasks_completed: int | None = None
    tasks_failed: int | None = None


class RoundMetadata(BaseModel):
    """Round timing and metadata."""

    round_number: int
    started_at: float
    ended_at: float
    start_block: int
    end_block: int
    start_epoch: float
    end_epoch: float
    tasks_total: int
    tasks_completed: int
    miners_responded_handshake: int
    miners_active: int


class FinishRoundRequest(BaseModel):
    status: str = Field(default="finished", description="Final status for the round")
    ended_at: float | None = Field(default=None, description="Epoch timestamp when the round finished")
    agent_runs: list[FinishRoundAgentRun] = Field(default_factory=list)
    round_metadata: RoundMetadata | None = Field(default=None, alias="round")
    local_evaluation: Dict[str, Any] | None = None
    post_consensus_evaluation: Dict[str, Any] | None = None
    # IPFS data
    ipfs_uploaded: Dict[str, Any] | None = None
    ipfs_downloaded: Dict[str, Any] | None = None


@router.post("/auth-check", dependencies=[Depends(require_validator_auth)])
async def validator_auth_check() -> dict[str, Any]:
    """Lightweight endpoint validators can call to verify auth headers before starting a round."""
    return {"message": "Validator authentication verified"}


@router.post("/start", dependencies=[Depends(require_validator_auth)])
async def start_round(
    payload: StartRoundRequest,
    request: Request,
    force: bool = Query(False, description="TESTING-only override to skip chain round/window checks"),
    session: AsyncSession = Depends(get_session),
):
    """Register a new validator round along with validator identity and snapshot."""

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
    if validator_snapshot.validator_uid != validator_round.validator_uid or validator_snapshot.validator_hotkey != validator_round.validator_hotkey:
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

    # Enforce chain-derived round constraints
    # In TESTING mode, allow bypass if chain state is unavailable
    current_block = get_current_block()
    if current_block is None:
        if settings.TESTING and bool(force):
            # In testing mode with force flag, use start_block as fallback
            logger.warning(
                "TESTING mode: Chain state unavailable, using start_block=%s as current_block fallback",
                validator_round.start_block,
            )
            current_block = validator_round.start_block
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Chain state unavailable",
            )

    # Calculate boundaries from start_block (round boundaries are based on start_block)
    from app.services.round_calc import _round_blocks, block_to_epoch

    round_blocks = _round_blocks()
    calculated_start_block = validator_round.start_block
    calculated_end_block = calculated_start_block + round_blocks

    # Allow testing override ONLY for window timing
    testing_override = settings.TESTING and bool(force)
    if testing_override:
        logger.warning(
            "TESTING override enabled: skipping window check for validator_round_id=%s (season=%s, round_in_season=%s)",
            validator_round.validator_round_id,
            validator_round.season_number,
            validator_round.round_number_in_season,
        )
    elif not (calculated_start_block < current_block <= calculated_end_block):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "round window not active",
                "currentBlock": current_block,
                "startBlock": calculated_start_block,
                "endBlock": calculated_end_block,
            },
        )

    # Override payload boundaries to chain-derived values unless testing override is enabled
    if not testing_override:
        validator_round.start_block = calculated_start_block
        validator_round.end_block = calculated_end_block
        validator_round.start_epoch = int(block_to_epoch(calculated_start_block))
        validator_round.end_epoch = int(block_to_epoch(calculated_end_block))

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

    # Copy coldkey from validator_round to validator_snapshot if not already set
    if validator_snapshot.validator_coldkey is None and validator_round.validator_coldkey:
        validator_snapshot.validator_coldkey = validator_round.validator_coldkey

    service = ValidatorRoundPersistenceService(session)

    try:
        # Session already has transaction from get_session
        await service.start_round(
            validator_identity=validator_identity,
            validator_round=validator_round,
            validator_snapshot=validator_snapshot,
        )
        await session.commit()
    except DuplicateIdentifierError as exc:
        # Treat duplicate start as idempotent if it belongs to the same validator
        try:
            existing_round = await service._get_round_row(validator_round.validator_round_id)  # type: ignore[attr-defined]
        except Exception:
            existing_round = None
        if existing_round is not None:
            if (
                existing_round.validator_snapshot
                and existing_round.validator_snapshot.validator_uid == validator_round.validator_uid
                and existing_round.validator_snapshot.validator_hotkey == validator_round.validator_hotkey
            ):
                logger.info(
                    "Validator round %s already registered; treating as idempotent",
                    validator_round.validator_round_id,
                )
                return {
                    "message": "Validator round created",
                    "validator_round_id": validator_round.validator_round_id,
                }
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RoundConflictError as exc:
        # Si ya existe un round con ese season_number y round_number_in_season para este validator,
        # BORRAR todos los datos del round anterior y crear uno nuevo
        try:
            # Try to find existing round by season and round_in_season
            from sqlalchemy import select
            from app.db.models import ValidatorRoundORM, ValidatorRoundValidatorORM

            stmt = (
                select(ValidatorRoundORM)
                .join(
                    ValidatorRoundValidatorORM,
                    ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
                )
                .where(
                    ValidatorRoundValidatorORM.validator_uid == validator_round.validator_uid,
                    ValidatorRoundORM.season_number == validator_round.season_number,
                    ValidatorRoundORM.round_number_in_season == validator_round.round_number_in_season,
                )
            )
            existing = await session.scalar(stmt)
        except Exception:
            existing = None
        if existing is not None:
            logger.warning(
                "Validator %s (hotkey=%s) already has season=%s, round_in_season=%s with round_id=%s; deleting ALL data for this validator and season/round to allow new start",
                validator_round.validator_uid,
                validator_round.validator_hotkey,
                validator_round.season_number,
                validator_round.round_number_in_season,
                existing.validator_round_id,
            )

            # Borrar el round anterior (cascade borra automáticamente todos los datos relacionados:
            # - tasks, agent_runs, evaluations, evaluation_results
            # - validator_snapshots, miner_snapshots
            await session.delete(existing)
            await session.flush()  # Ejecutar el delete antes de continuar

            logger.info(
                "Deleted old round %s for validator %s (season=%s, round_in_season=%s); proceeding with new round creation",
                existing.validator_round_id,
                validator_round.validator_uid,
                validator_round.season_number,
                validator_round.round_number_in_season,
            )

            # Ahora crear el nuevo round
            try:
                await service.start_round(
                    validator_identity=validator_identity,
                    validator_round=validator_round,
                    validator_snapshot=validator_snapshot,
                )
                await session.commit()
            except Exception as inner_exc:
                await session.rollback()
                logger.error(
                    "Failed to create new round after deleting old one: %s",
                    inner_exc,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create new round after deleting old one: {inner_exc}",
                ) from inner_exc

            logger.info(
                "Successfully replaced round for validator %s (season=%s, round_in_season=%s): old_round_id=%s -> new_round_id=%s",
                validator_round.validator_uid,
                validator_round.season_number,
                validator_round.round_number_in_season,
                existing.validator_round_id,
                validator_round.validator_round_id,
            )
            return {
                "message": "Validator round created (replaced existing round)",
                "validator_round_id": validator_round.validator_round_id,
            }
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info(
        "Started validator round %s (season=%s, round_in_season=%s, validator_uid=%s)",
        validator_round.validator_round_id,
        validator_round.season_number,
        validator_round.round_number_in_season,
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
    force: bool = Query(False, description="TESTING-only override to skip chain round/window checks"),
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
                if settings.TESTING and bool(force):
                    # In testing mode with force flag, use start_block as fallback
                    logger.warning(
                        "TESTING mode: Chain state unavailable, using start_block=%s as current_block fallback for set_tasks",
                        round_row.start_block,
                    )
                    current_block = round_row.start_block
                else:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Chain state unavailable",
                    )

            # Calculate boundaries from start_block (no longer using round_number)
            from app.services.round_calc import _round_blocks, block_to_epoch

            round_blocks = _round_blocks()
            calculated_start_block = round_row.start_block
            calculated_end_block = calculated_start_block + round_blocks

            bounds = type(
                "RoundBoundaries",
                (),
                {
                    "start_block": calculated_start_block,
                    "end_block": calculated_end_block,
                    "start_epoch": block_to_epoch(calculated_start_block),
                    "end_epoch": block_to_epoch(calculated_end_block),
                },
            )()
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
        count = await service.add_tasks(validator_round_id, payload.tasks, allow_existing=True)
        await session.commit()
    except DuplicateIdentifierError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info("Stored %d tasks for validator round %s", count, validator_round_id)
    return {"message": "Tasks stored", "count": count}


@router.post(
    "/{validator_round_id}/agent-runs/start",
    dependencies=[Depends(require_validator_auth)],
)
async def start_agent_run(
    validator_round_id: str,
    payload: StartAgentRunRequest,
    request: Request,
    force: bool = Query(False, description="TESTING-only override to skip chain round/window checks"),
    session: AsyncSession = Depends(get_session),
):
    """Register the beginning of an agent evaluation run."""
    service = ValidatorRoundPersistenceService(session)

    try:
        request_payload = payload

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
        # validator_uid and validator_hotkey removed from agent_evaluation_runs
        # Validation is done via validator_round_id matching
        if existing_run is not None and existing_run.validator_round_id == validator_round_id:
            logger.info(
                "Agent run %s already registered (round %s); treating as idempotent",
                agent_run.agent_run_id,
                validator_round_id,
            )
            return {
                "message": "Agent run registered",
                "agent_run_id": agent_run.agent_run_id,
            }

        # CRITICAL: Check if there's already an agent_run for this miner in this round
        # An agent run should be unique per (validator_round_id, miner_uid)
        # This prevents creating multiple agent runs when the validator calls start_agent_run multiple times
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
            result_existing = await session.execute(stmt_existing)
            existing_for_miner = result_existing.scalar_one_or_none()

            if existing_for_miner:
                # There's already an agent_run for this miner in this round
                # Return the existing one instead of creating a duplicate
                logger.warning(
                    f"Agent run already exists for miner_uid={agent_run.miner_uid} in validator_round_id={validator_round_id}. "
                    f"Existing agent_run_id={existing_for_miner.agent_run_id}, requested agent_run_id={agent_run.agent_run_id}. "
                    f"Returning existing agent run (idempotent)."
                )
                return {
                    "message": "Agent run registered",
                    "agent_run_id": existing_for_miner.agent_run_id,
                }

        # Ensure round exists and request matches round owner
        # validator_uid and validator_hotkey removed from agent_run - validation done via validator_round_id
        round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
        _ensure_request_matches_round_owner(request, round_row)
        if not round_row.validator_snapshot:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Validator snapshot not found for round",
            )

        miner_snapshot = request_payload.miner_snapshot
        # Canonicalize miner image on non-legacy path as well
        _resolve_miner_snapshot_image(miner_snapshot)

        # Enforce chain-derived round constraints
        # In TESTING mode, allow bypass if chain state is unavailable
        current_block = get_current_block()
        if current_block is None:
            if settings.TESTING and bool(force):
                # In testing mode with force flag, use start_block as fallback
                logger.warning(
                    "TESTING mode: Chain state unavailable, using start_block=%s as current_block fallback for start_agent_run",
                    round_row.start_block,
                )
                current_block = round_row.start_block
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Chain state unavailable",
                )
        # Calculate boundaries from start_block (no longer using round_number)
        from app.services.round_calc import _round_blocks, block_to_epoch

        round_blocks = _round_blocks()
        calculated_start_block = round_row.start_block
        calculated_end_block = calculated_start_block + round_blocks

        bounds = type(
            "RoundBoundaries",
            (),
            {
                "start_block": calculated_start_block,
                "end_block": calculated_end_block,
                "start_epoch": block_to_epoch(calculated_start_block),
                "end_epoch": block_to_epoch(calculated_end_block),
            },
        )()

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

        # is_sota now comes from miner_snapshot, not agent_run
        if not request_payload.miner_snapshot.is_sota:
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
            if expected_hotkey and identity.hotkey and expected_hotkey != identity.hotkey:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_identity.hotkey must match agent_run.miner_hotkey",
                )
            # Snapshot consistency (if provided)
            if miner_snapshot.miner_uid is not None and miner_snapshot.miner_uid != expected_uid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_snapshot.miner_uid must match agent_run.miner_uid",
                )
            if miner_snapshot.miner_hotkey is not None and expected_hotkey is not None and miner_snapshot.miner_hotkey != expected_hotkey:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="miner_snapshot.miner_hotkey must match agent_run.miner_hotkey",
                )

        # Persist and commit
        await service.start_agent_run(
            validator_round_id=validator_round_id,
            agent_run=agent_run,
            miner_identity=request_payload.miner_identity,
            miner_snapshot=miner_snapshot,
        )
        await session.commit()
    except DuplicateIdentifierError as exc:
        existing_run = await service._get_agent_run_row(agent_run.agent_run_id)  # type: ignore[attr-defined]
        # validator_uid and validator_hotkey removed from agent_evaluation_runs
        if existing_run is not None:
            if existing_run.validator_round_id == validator_round_id:
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
            detail = f"agent_run_id {agent_run.agent_run_id} is already registered to validator_round {existing_run.validator_round_id}"
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RoundConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info(
        "Registered agent run %s (validator_round_id=%s)",
        agent_run.agent_run_id,
        validator_round_id,
    )
    return {"message": "Agent run registered", "agent_run_id": agent_run.agent_run_id}


@router.post(
    "/{validator_round_id}/agent-runs/{agent_run_id}/evaluations/batch",
    dependencies=[Depends(require_validator_auth)],
)
async def add_evaluations_batch(
    validator_round_id: str,
    agent_run_id: str,
    request: Request,
    payload: list[AddEvaluationRequest] = Body(..., description="List of evaluation requests"),
    force: bool = Query(False, description="TESTING-only override to skip chain round/window checks"),
    session: AsyncSession = Depends(get_session),
):
    """Persist multiple evaluation data (tasks, solutions, and evaluations) in a single transaction."""
    service = ValidatorRoundPersistenceService(session)

    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Batch cannot be empty")

    # Process each evaluation in the batch. Use a savepoint per item so one failure
    # does not abort the whole transaction (InFailedSQLTransactionError on next query).
    evaluations_created = 0
    errors = []

    try:
        for idx, eval_request in enumerate(payload):
            try:
                async with session.begin_nested():
                    # Merge evaluation_result (if provided) into evaluation so reward/time fields are not dropped
                    request_payload = eval_request
                    eval_result_payload: Dict[str, Any] | None = None
                    if isinstance(getattr(request_payload, "evaluation_result", None), dict):
                        eval_result_payload = request_payload.evaluation_result  # type: ignore[assignment]

                    if eval_result_payload:
                        merged_eval_data = request_payload.evaluation.model_dump(mode="json", exclude_none=True)
                        merged_eval_data.update(eval_result_payload)
                        request_payload = AddEvaluationRequest(
                            task=request_payload.task,
                            task_solution=request_payload.task_solution,
                            evaluation=Evaluation(**merged_eval_data),
                            evaluation_result=eval_result_payload,
                        )

                    task = request_payload.task
                    task_solution = request_payload.task_solution
                    evaluation = request_payload.evaluation

                    expected_fields = [
                        (task.validator_round_id, "task.validator_round_id"),
                        (task_solution.validator_round_id, "task_solution.validator_round_id"),
                        (evaluation.validator_round_id, "evaluation.validator_round_id"),
                    ]
                    for value, label in expected_fields:
                        _require_round_match(value, validator_round_id, f"[batch {idx}] {label}")

                    _require_round_match(task_solution.task_id, task.task_id, f"[batch {idx}] task_solution.task_id")
                    _require_round_match(evaluation.task_id, task.task_id, f"[batch {idx}] evaluation.task_id")
                    _require_round_match(task_solution.agent_run_id, agent_run_id, f"[batch {idx}] task_solution.agent_run_id")
                    _require_round_match(evaluation.agent_run_id, agent_run_id, f"[batch {idx}] evaluation.agent_run_id")
                    _require_round_match(
                        evaluation.task_solution_id,
                        task_solution.solution_id,
                        f"[batch {idx}] evaluation.task_solution_id",
                    )

                    round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
                    _ensure_request_matches_round_owner(request, round_row)
                    check_pairs = [
                        (
                            task_solution.validator_uid,
                            task_solution.validator_hotkey,
                            "task_solution",
                        ),
                        (evaluation.validator_uid, evaluation.validator_hotkey, "evaluation"),
                    ]
                    if not round_row.validator_snapshot:
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Validator snapshot not found for round",
                        )
                    for uid_value, hotkey_value, label in check_pairs:
                        if uid_value is not None and int(uid_value) != int(round_row.validator_snapshot.validator_uid):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"[batch {idx}] {label}.validator_uid must match the round's validator_uid",
                            )
                        if hotkey_value and hotkey_value != round_row.validator_snapshot.validator_hotkey:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"[batch {idx}] {label}.validator_hotkey must match the round's validator_hotkey",
                            )

                    await service.add_evaluation(
                        validator_round_id=validator_round_id,
                        agent_run_id=agent_run_id,
                        task=task,
                        task_solution=task_solution,
                        evaluation=evaluation,
                    )
                    evaluations_created += 1

            except DuplicateIdentifierError as exc:
                # Skip duplicates (idempotency); savepoint was rolled back, duplicate already in DB
                logger.info(f"Batch evaluation {idx} already exists: {exc}")
                evaluations_created += 1
                continue
            except Exception as exc:
                error_msg = f"Batch evaluation {idx} failed: {str(exc)}"
                logger.error(error_msg, exc_info=True)
                errors.append(error_msg)
                # Savepoint rolled back by begin_nested(); main transaction still valid, continue

        # Commit all changes in a single transaction
        # All evaluations that were successfully added (via add_evaluation) are now committed
        # This includes all flushes done inside add_evaluation() calls
        await session.commit()

        # Final refresh of agent_run to ensure stats are up-to-date after commit
        # This is a safety measure, though stats should already be updated by add_evaluation()
        try:
            agent_run_row = await service._get_agent_run_row(agent_run_id)  # type: ignore[attr-defined]
            if agent_run_row:
                await session.refresh(agent_run_row, ["evaluations", "task_solutions"])
                # Stats are already updated by add_evaluation(), but this ensures consistency
        except Exception:
            # Non-critical: stats should already be correct from add_evaluation()
            pass

        result = {
            "message": f"Batch evaluations processed: {evaluations_created} created",
            "evaluations_created": evaluations_created,
            "total_requested": len(payload),
        }

        if errors:
            result["errors"] = errors
            result["message"] += f", {len(errors)} failed"

        return result

    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        logger.exception("Failed to process batch evaluations")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to process batch evaluations: {str(exc)}") from exc


@router.post(
    "/{validator_round_id}/agent-runs/{agent_run_id}/evaluations",
    dependencies=[Depends(require_validator_auth)],
)
async def add_evaluation(
    validator_round_id: str,
    agent_run_id: str,
    payload: AddEvaluationRequest,
    request: Request,
    force: bool = Query(False, description="TESTING-only override to skip chain round/window checks"),
    session: AsyncSession = Depends(get_session),
):
    """Persist evaluation data (task, solution, and evaluation with artefacts)."""
    service = ValidatorRoundPersistenceService(session)
    raw_json: Dict[str, Any] | None = None
    try:
        raw_json = await request.json()
    except Exception:
        raw_json = None

    try:
        request_payload = payload
        # Merge evaluation_result (if provided) into evaluation so reward/time fields are not dropped
        eval_result_payload: Dict[str, Any] | None = None
        if isinstance(getattr(request_payload, "evaluation_result", None), dict):
            eval_result_payload = request_payload.evaluation_result  # type: ignore[assignment]
        elif isinstance(raw_json, dict):
            maybe_eval_result = raw_json.get("evaluation_result")
            if isinstance(maybe_eval_result, dict):
                eval_result_payload = maybe_eval_result

        if eval_result_payload:
            merged_eval_data = request_payload.evaluation.model_dump(mode="json", exclude_none=True)
            # evaluation_result values (e.g., reward, stats) take precedence
            merged_eval_data.update(eval_result_payload)
            request_payload = AddEvaluationRequest(
                task=request_payload.task,
                task_solution=request_payload.task_solution,
                evaluation=Evaluation(**merged_eval_data),
                evaluation_result=eval_result_payload,
            )

        # Heuristic: if actions reached here as AddEvaluationRequest but lost fields
        # (attributes empty), rebuild attributes from raw JSON body before persisting.
        try:
            raw_ts = (raw_json or {}).get("task_solution") or {}
            raw_actions = raw_ts.get("actions") if isinstance(raw_ts, dict) else None
            ts = getattr(request_payload, "task_solution", None)
            if raw_actions and ts and isinstance(ts.actions, list):
                from app.models.core import (
                    Action as CoreAction,
                    TaskSolution as CoreTaskSolution,
                )

                def _norm_type(t: str) -> str:
                    key = (t or "other").lower().replace("action", "").replace("-", "_").strip() or "other"
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
                if all((getattr(a, "attributes", None) in (None, {}, []) for a in ts.actions)):
                    request_payload = AddEvaluationRequest(
                        task=request_payload.task,
                        task_solution=CoreTaskSolution(
                            **{
                                **request_payload.task_solution.model_dump(mode="json"),
                                "actions": [a.model_dump(mode="json") for a in new_actions],
                            }
                        ),
                        evaluation=request_payload.evaluation,
                        evaluation_result=getattr(request_payload, "evaluation_result", None),
                    )
        except Exception:
            # Non-fatal: fall back to original payload
            pass

        task = request_payload.task
        task_solution = request_payload.task_solution
        evaluation = request_payload.evaluation

        expected_fields = [
            (task.validator_round_id, "task.validator_round_id"),
            (task_solution.validator_round_id, "task_solution.validator_round_id"),
            (evaluation.validator_round_id, "evaluation.validator_round_id"),
        ]
        for value, label in expected_fields:
            _require_round_match(value, validator_round_id, label)

        _require_round_match(task_solution.task_id, task.task_id, "task_solution.task_id")
        _require_round_match(evaluation.task_id, task.task_id, "evaluation.task_id")
        _require_round_match(task_solution.agent_run_id, agent_run_id, "task_solution.agent_run_id")
        _require_round_match(evaluation.agent_run_id, agent_run_id, "evaluation.agent_run_id")
        _require_round_match(
            evaluation.task_solution_id,
            task_solution.solution_id,
            "evaluation.task_solution_id",
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
        ]
        if not round_row.validator_snapshot:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Validator snapshot not found for round",
            )
        for uid_value, hotkey_value, label in check_pairs:
            if uid_value is not None and int(uid_value) != int(round_row.validator_snapshot.validator_uid):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{label}.validator_uid must match the round's validator_uid",
                )
            if hotkey_value and hotkey_value != round_row.validator_snapshot.validator_hotkey:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{label}.validator_hotkey must match the round's validator_hotkey",
                )

        # Early idempotency: if the entire bundle already exists for this round/run,
        # return success before enforcing window checks to allow safe replays.
        try:
            existing_solution = await service.get_task_solution_row(task_solution.solution_id)
            existing_eval = await service.get_evaluation_row(evaluation.evaluation_id)
        except Exception:
            existing_solution = existing_eval = None
        if (
            existing_solution
            and existing_eval
            and str(existing_solution.validator_round_id) == str(validator_round_id)
            and str(existing_eval.validator_round_id) == str(validator_round_id)
            and str(existing_solution.agent_run_id) == str(agent_run_id)
            and str(existing_eval.agent_run_id) == str(agent_run_id)
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
            # Calculate boundaries from start_block (no longer using round_number)
            from app.services.round_calc import _round_blocks, block_to_epoch

            round_blocks = _round_blocks()
            calculated_start_block = round_row.start_block
            calculated_end_block = calculated_start_block + round_blocks

            bounds = type(
                "RoundBoundaries",
                (),
                {
                    "start_block": calculated_start_block,
                    "end_block": calculated_end_block,
                    "start_epoch": block_to_epoch(calculated_start_block),
                    "end_epoch": block_to_epoch(calculated_end_block),
                },
            )()
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

        # Persist and commit
        await service.upsert_evaluation_bundle(
            validator_round_id=validator_round_id,
            agent_run_id=agent_run_id,
            task=task,
            task_solution=task_solution,
            evaluation=evaluation,
        )
        await session.commit()
    except DuplicateIdentifierError as exc:
        # Conflicting duplicate (belongs to another round/run), surface as 409
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
    request: Request,
    force: bool = Query(False, description="TESTING-only override to skip validation"),
    session: AsyncSession = Depends(get_session),
):
    """
    Mark a validator round as finished and update all agent_runs with final metrics.

    This endpoint:
    - Updates validator_round status to 'finished'
    - Sets ended_at for the round and all agent_runs
    - Updates average_score, rank, weight for each agent_run
    - Saves winners and weights in the round metadata
    """
    service = ValidatorRoundPersistenceService(session)

    try:
        # Validate ownership
        round_row = await service._ensure_round_exists(validator_round_id)  # type: ignore[attr-defined]
        _ensure_request_matches_round_owner(request, round_row)

        # Normalize status to match ValidatorRound literal type
        normalized_status = payload.status.lower()
        if normalized_status in {"completed", "complete"}:
            normalized_status = "finished"
        elif normalized_status not in {
            "active",
            "finished",
            "pending",
            "evaluating_finished",
        }:
            normalized_status = "finished"

        # Call the service method
        await service.finish_round(
            validator_round_id=validator_round_id,
            status=normalized_status,
            ended_at=payload.ended_at or time.time(),
            agent_runs=(
                [
                    {
                        "agent_run_id": ar.agent_run_id,
                        "rank": ar.rank,
                        "weight": ar.weight,
                        "miner_name": ar.miner_name,
                        "avg_reward": ar.avg_reward,
                        "avg_evaluation_time": ar.avg_evaluation_time,
                        "tasks_attempted": ar.tasks_attempted,
                        "tasks_completed": ar.tasks_completed,
                        "tasks_failed": ar.tasks_failed,
                    }
                    for ar in payload.agent_runs
                ]
                if payload.agent_runs
                else None
            ),
            round_metadata=(payload.round_metadata.model_dump() if payload.round_metadata else None),
            local_evaluation=payload.local_evaluation,
            post_consensus_evaluation=payload.post_consensus_evaluation,
            ipfs_uploaded=payload.ipfs_uploaded,
            ipfs_downloaded=payload.ipfs_downloaded,
        )
        await session.commit()

        # Determine number of winners from post_consensus_evaluation
        n_winners = 0
        if payload.post_consensus_evaluation:
            miners = payload.post_consensus_evaluation.get("miners", [])
            n_winners = len([m for m in miners if m.get("weight", 0) > 0])

        logger.info(
            "Finished round %s with %d winners, %d agent_runs updated",
            validator_round_id,
            n_winners,
            len(payload.agent_runs) if payload.agent_runs else 0,
        )

        return {
            "message": "Round finished successfully",
            "validator_round_id": validator_round_id,
            "status": normalized_status,
        }

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

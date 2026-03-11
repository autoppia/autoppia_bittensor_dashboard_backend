import logging
import time

from fastapi import Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.validator.common import _ensure_request_matches_round_owner, _require_round_match
from app.api.validator.schemas import (
    FinishRoundRequest,
    SetTasksRequest,
    StartAgentRunRequest,
    StartRoundRequest,
    SyncRuntimeConfigRequest,
    _resolve_miner_snapshot_image,
)
from app.config import settings
from app.db.models import AgentEvaluationRunORM, EvaluationORM, TaskORM
from app.db.session import get_session
from app.services.chain_state import get_current_block
from app.services.round_calc import is_inside_window
from app.services.validator.validator_auth import VALIDATOR_HOTKEY_HEADER
from app.services.validator.validator_storage import (
    DuplicateIdentifierError,
    RoundConflictError,
    ValidatorRoundPersistenceService,
)
from app.services.validator_directory import get_validator_metadata
from app.utils.images import resolve_validator_image

logger = logging.getLogger(__name__)


def _tasks_per_season_from_validator_config(config: object) -> int | None:
    if not isinstance(config, dict):
        return None
    round_cfg = config.get("round")
    if not isinstance(round_cfg, dict):
        return None
    raw = round_cfg.get("tasks_per_season")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def _ensure_config_season_round_cache_loaded(session: AsyncSession) -> None:
    """
    Ensure this worker has config_season_round in memory.

    With multiple Uvicorn workers, runtime-config may be written by one worker while
    another still has an empty in-process cache. Load from DB on-demand before using
    round boundary helpers.
    """
    from app.services.round_config_service import get_config_season_round, refresh_config_season_round_cache

    try:
        get_config_season_round()
        return
    except RuntimeError:
        pass

    await refresh_config_season_round_cache(session)


async def sync_runtime_config(
    payload: SyncRuntimeConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """
    Persist runtime round config into DB.
    Only the configured main validator is allowed to update config_season_round.
    """
    validator_identity = payload.validator_identity
    runtime_cfg = payload.runtime_config

    header_hotkey = request.headers.get(VALIDATOR_HOTKEY_HEADER)
    if header_hotkey and header_hotkey != validator_identity.hotkey:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Validator header hotkey does not match payload hotkey",
        )

    from app.services.round_config_service import load_config_season_round_from_db, upsert_config_season_round

    # Ensure singleton runtime config row exists even right after truncate/reset.
    if settings.MAIN_VALIDATOR_UID is not None:
        await session.execute(
            text(
                """
                INSERT INTO config_app_runtime (
                    id,
                    main_validator_uid,
                    main_validator_hotkey,
                    created_at,
                    updated_at
                )
                VALUES (
                    1,
                    :main_validator_uid,
                    :main_validator_hotkey,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    main_validator_uid = EXCLUDED.main_validator_uid,
                    main_validator_hotkey = COALESCE(EXCLUDED.main_validator_hotkey, config_app_runtime.main_validator_hotkey),
                    updated_at = NOW()
                """
            ),
            {
                "main_validator_uid": int(settings.MAIN_VALIDATOR_UID),
                "main_validator_hotkey": (settings.MAIN_VALIDATOR_HOTKEY or "").strip() or None,
            },
        )

    updated = await upsert_config_season_round(
        session=session,
        validator_uid=int(validator_identity.uid),
        round_size_epochs=float(runtime_cfg.round_size_epochs),
        season_size_epochs=float(runtime_cfg.season_size_epochs),
        minimum_start_block=int(runtime_cfg.minimum_start_block),
        blocks_per_epoch=int(runtime_cfg.blocks_per_epoch or 360),
    )
    if updated:
        min_version = (runtime_cfg.minimum_validator_version or "").strip()
        if min_version:
            await session.execute(
                text(
                    """
                    UPDATE config_app_runtime
                    SET minimum_validator_version = :minimum_validator_version,
                        updated_at = NOW()
                    WHERE id = 1
                    """
                ),
                {"minimum_validator_version": min_version},
            )
    await session.commit()

    current_cfg = await load_config_season_round_from_db(session)
    cfg_payload = None
    if current_cfg is not None:
        cfg_payload = {
            "round_size_epochs": float(current_cfg.round_size_epochs),
            "season_size_epochs": float(current_cfg.season_size_epochs),
            "minimum_start_block": int(current_cfg.minimum_start_block),
            "blocks_per_epoch": int(current_cfg.blocks_per_epoch),
        }

    current_min_version_row = (
        await session.execute(
            text(
                """
                SELECT minimum_validator_version
                FROM config_app_runtime
                WHERE id = 1
                LIMIT 1
                """
            )
        )
    ).first()
    current_min_version = None
    if current_min_version_row and current_min_version_row[0]:
        current_min_version = str(current_min_version_row[0]).strip() or None

    return {
        "message": "Runtime config synced" if updated else "Runtime config ignored (only main validator can update it)",
        "updated": bool(updated),
        "config_season_round": cfg_payload,
        "minimum_validator_version": current_min_version,
    }


async def start_round(
    payload: StartRoundRequest,
    request: Request,
    response: Response,
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

    config_tasks = _tasks_per_season_from_validator_config(validator_snapshot.config)
    if config_tasks is not None and int(validator_round.n_tasks or 0) != config_tasks:
        logger.warning(
            "start_round n_tasks mismatch for validator_round_id=%s: payload n_tasks=%s, validator_config.round.tasks_per_season=%s. Using validator_config value.",
            validator_round.validator_round_id,
            validator_round.n_tasks,
            config_tasks,
        )
        validator_round.n_tasks = int(config_tasks)

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

    # Ensure this worker sees latest config_season_round before using round boundary helpers.
    try:
        await _ensure_config_season_round_cache_loaded(session)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"config_season_round unavailable: {exc}",
        ) from exc

    # Calculate boundaries from start_block (round boundaries are based on start_block)
    from app.services.round_calc import _round_blocks, block_to_epoch

    try:
        round_blocks = _round_blocks()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"config_season_round unavailable: {exc}",
        ) from exc
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
        if (
            existing_round is not None
            and existing_round.validator_snapshot
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
        detail = str(exc)
        detail_l = detail.lower()
        authority_guard = "only main validator can open a new season/round before fallback grace elapses" in detail_l or "fallback start denied" in detail_l
        if authority_guard:
            # Persist validator-local round start in shadow mode so non-main validators
            # do not lose round telemetry while canonical round authority stays on main.
            await service.upsert_shadow_round_start(
                validator_round=validator_round,
                validator_snapshot=validator_snapshot,
            )
            await session.commit()
            logger.warning(
                "start_round accepted in SHADOW mode for validator_round_id=%s (validator_uid=%s): %s",
                validator_round.validator_round_id,
                validator_round.validator_uid,
                detail,
            )
            response.status_code = status.HTTP_202_ACCEPTED
            return {
                "message": "Validator round accepted in shadow mode",
                "validator_round_id": validator_round.validator_round_id,
                "shadow_mode": True,
                "reason": detail,
            }

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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
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

        incoming_task_ids = [task.task_id for task in payload.tasks]
        if len(set(incoming_task_ids)) != len(incoming_task_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate task_id values in payload",
            )

        # Primary source of truth: sender validator config embedded in its own start_round payload.
        # This avoids races where a non-main validator starts before main and round_row.n_tasks
        # still reflects stale/default values.
        expected_tasks = _tasks_per_season_from_validator_config(getattr(round_row, "config", None)) or 0
        if expected_tasks <= 0:
            expected_tasks = int(round_row.n_tasks or 0)
        if expected_tasks <= 0:
            expected_tasks = len(payload.tasks)

        # Canonical cap: if main validator already published round config for this season/round,
        # require all validators to align with that task count.
        canonical_tasks_row = (
            await session.execute(
                text(
                    """
                    SELECT (rv.config->'round'->>'tasks_per_season')::INTEGER AS tasks_per_season
                    FROM round_validators rv
                    WHERE rv.season_number = :season_number
                      AND rv.round_number_in_season = :round_number_in_season
                      AND COALESCE(rv.is_main_validator, FALSE) = TRUE
                    ORDER BY rv.started_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "season_number": int(round_row.season_number or 0),
                    "round_number_in_season": int(round_row.round_number_in_season or 0),
                },
            )
        ).first()
        canonical_tasks = None
        if canonical_tasks_row and canonical_tasks_row[0]:
            try:
                canonical_tasks = int(canonical_tasks_row[0])
            except (TypeError, ValueError):
                canonical_tasks = None
        if canonical_tasks is not None and canonical_tasks > 0:
            expected_tasks = canonical_tasks

        if len(payload.tasks) != expected_tasks:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid task count for round",
                    "expectedTasks": expected_tasks,
                    "receivedTasks": len(payload.tasks),
                    "validatorRoundId": validator_round_id,
                },
            )

        testing_override = settings.TESTING and bool(force)
        if testing_override:
            logger.warning(
                "TESTING override enabled: accepting set_tasks for validator_round_id=%s without chain checks",
                validator_round_id,
            )
        else:
            try:
                await _ensure_config_season_round_cache_loaded(session)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"config_season_round unavailable: {exc}",
                ) from exc

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

            try:
                round_blocks = _round_blocks()
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"config_season_round unavailable: {exc}",
                ) from exc
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

        # Strong idempotency: same payload can be replayed safely; different payload can
        # replace existing tasks only before agent runs/evaluations start.
        existing_task_ids = set((await session.execute(select(TaskORM.task_id).where(TaskORM.validator_round_id == validator_round_id))).scalars())
        incoming_task_id_set = set(incoming_task_ids)
        if existing_task_ids:
            if existing_task_ids == incoming_task_id_set:
                logger.info(
                    "set_tasks idempotent replay for validator_round_id=%s (tasks=%d)",
                    validator_round_id,
                    len(existing_task_ids),
                )
                await session.commit()
                return {"message": "Tasks stored", "count": 0, "idempotent": True}

            has_runs = bool((await session.execute(select(func.count()).select_from(AgentEvaluationRunORM).where(AgentEvaluationRunORM.validator_round_id == validator_round_id))).scalar_one())
            has_evaluations = bool((await session.execute(select(func.count()).select_from(EvaluationORM).where(EvaluationORM.validator_round_id == validator_round_id))).scalar_one())
            if has_runs or has_evaluations:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("Tasks already locked for this round (agent runs/evaluations exist). Refusing to replace task set."),
                )

            logger.warning(
                "Replacing task set for validator_round_id=%s before evaluation start: old=%d new=%d",
                validator_round_id,
                len(existing_task_ids),
                len(incoming_task_id_set),
            )
            await session.execute(delete(TaskORM).where(TaskORM.validator_round_id == validator_round_id))
            await session.flush()

        # Session already has a transaction from get_session dependency
        count = await service.add_tasks(validator_round_id, payload.tasks, allow_existing=False)
        await session.commit()
    except DuplicateIdentifierError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info("Stored %d tasks for validator round %s", count, validator_round_id)
    return {"message": "Tasks stored", "count": count}


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
        try:
            await _ensure_config_season_round_cache_loaded(session)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"config_season_round unavailable: {exc}",
            ) from exc

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
        valid_statuses = {"active", "finished", "pending", "evaluating_finished"}
        if normalized_status in {"completed", "complete"} or normalized_status not in valid_statuses:
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
                        "zero_reason": ar.zero_reason,
                    }
                    for ar in payload.agent_runs
                ]
                if payload.agent_runs
                else None
            ),
            round_metadata=(payload.round_metadata.model_dump() if payload.round_metadata else None),
            validator_summary=payload.validator_summary,
            local_evaluation=payload.local_evaluation,
            post_consensus_evaluation=payload.post_consensus_evaluation,
            ipfs_uploaded=payload.ipfs_uploaded,
            ipfs_downloaded=payload.ipfs_downloaded,
            s3_logs_url=payload.s3_logs_url,
            validator_state=payload.validator_state,
        )
        await session.commit()

        logger.info(
            "Finished round %s, %d agent_runs updated",
            validator_round_id,
            len(payload.agent_runs) if payload.agent_runs else 0,
        )

        return {
            "message": "Round finished successfully",
            "validator_round_id": validator_round_id,
            "status": normalized_status,
        }

    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

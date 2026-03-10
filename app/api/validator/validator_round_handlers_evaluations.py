from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.validator.common import _ensure_request_matches_round_owner, _require_round_match
from app.api.validator.schemas import AddEvaluationRequest
from app.config import settings
from app.db.session import get_session
from app.models.core import Action as CoreAction, Evaluation, TaskSolution as CoreTaskSolution
from app.services.chain_state import get_current_block
from app.services.round_calc import block_to_epoch, is_inside_window, _round_blocks
from app.services.validator.validator_storage import DuplicateIdentifierError, ValidatorRoundPersistenceService

logger = logging.getLogger(__name__)


async def add_evaluations_batch(
    validator_round_id: str,
    agent_run_id: str,
    request: Request,
    payload: Annotated[list[AddEvaluationRequest], Body(..., description="List of evaluation requests")],
    force: Annotated[bool, Query(False, description="TESTING-only override to skip chain round/window checks")] = False,
    session: Annotated[AsyncSession, Depends(get_session)],
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
                    eval_result_payload: dict[str, Any] | None = None
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
                logger.info("Batch evaluation %s already exists: %s", idx, exc)
                evaluations_created += 1
                continue
            except Exception as exc:  # noqa: BLE001 - per-item failure, collect and continue
                error_msg = "Batch evaluation %s failed: %s" % (idx, exc)
                logger.error("%s", error_msg, exc_info=True)
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
        except Exception:  # noqa: BLE001 - non-critical, stats already correct from add_evaluation
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
    except Exception as exc:  # noqa: BLE001 - catch-all at batch boundary, return 500
        await session.rollback()
        logger.exception("Failed to process batch evaluations")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process batch evaluations: %s" % (exc,)) from exc


async def add_evaluation(
    validator_round_id: str,
    agent_run_id: str,
    payload: AddEvaluationRequest,
    request: Request,
    force: Annotated[bool, Query(False, description="TESTING-only override to skip chain round/window checks")] = False,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Persist evaluation data (task, solution, and evaluation with artefacts)."""
    service = ValidatorRoundPersistenceService(session)
    raw_json: dict[str, Any] | None = None
    try:
        raw_json = await request.json()
    except Exception:  # noqa: BLE001 - optional raw body for heuristic fallback
        raw_json = None

    try:
        request_payload = payload
        # Merge evaluation_result (if provided) into evaluation so reward/time fields are not dropped
        eval_result_payload: dict[str, Any] | None = None
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
        except Exception:  # noqa: BLE001 - non-fatal: fall back to original payload
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
        except Exception:  # noqa: BLE001 - idempotency check: missing row -> None
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

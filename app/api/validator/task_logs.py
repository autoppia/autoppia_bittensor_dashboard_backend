"""
Backoffice endpoint to store per-task execution logs in S3 and metadata in DB.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvaluationRunORM, TaskExecutionLogORM
from app.db.session import get_session
from app.services.media_storage import (
    GifStorageConfigError,
    build_public_url,
    store_task_log,
)
from app.services.validator.validator_auth import require_validator_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/task-logs", tags=["task-logs"])


class TaskExecutionLogUploadRequest(BaseModel):
    task_id: str = Field(..., description="IWAP task_id")
    agent_run_id: str = Field(..., description="Agent run id")
    validator_round_id: str = Field(..., description="Validator round id")
    season: Optional[int] = Field(None, description="Season number")
    round_in_season: Optional[int] = Field(None, description="Round number within season")
    miner_uid: Optional[int] = Field(None, description="Miner UID")
    validator_uid: Optional[int] = Field(None, description="Validator UID")
    payload: dict[str, Any] = Field(..., description="Full task execution payload")


class TaskExecutionLogUploadResponse(BaseModel):
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None


async def _ensure_agent_run_exists(
    session: AsyncSession,
    *,
    agent_run_id: str,
    validator_round_id: str,
    miner_uid: Optional[int],
) -> None:
    """
    Ensure FK target miner_evaluation_runs(agent_run_id) exists.

    Task logs can arrive before /start-agent-run under high concurrency. In that
    case we create a lightweight placeholder row and let the normal run flow
    update it later.
    """
    round_validator_link = await session.execute(
        text(
            """
            SELECT round_validator_id
            FROM round_validators
            WHERE validator_round_id = :validator_round_id
            LIMIT 1
            """
        ),
        {"validator_round_id": validator_round_id},
    )
    if round_validator_link.scalar_one_or_none() is None:
        raise LookupError(f"round_validator_not_linked:{validator_round_id}")

    stmt = select(AgentEvaluationRunORM.id, AgentEvaluationRunORM.validator_round_id).where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
    existing = (await session.execute(stmt)).first()
    if existing:
        existing_round_id = existing[1]
        if existing_round_id and existing_round_id != validator_round_id:
            logger.warning(
                "task-log agent_run_id=%s already exists for validator_round_id=%s (incoming=%s)",
                agent_run_id,
                existing_round_id,
                validator_round_id,
            )
        return

    placeholder = AgentEvaluationRunORM(
        agent_run_id=agent_run_id,
        validator_round_id=validator_round_id,
        miner_uid=miner_uid,
        started_at=float(time.time()),
        total_tasks=0,
        success_tasks=0,
        failed_tasks=0,
        meta={"placeholder": True, "source": "task_logs"},
    )
    try:
        async with session.begin_nested():
            session.add(placeholder)
            await session.flush()
            logger.info(
                "Created placeholder agent_run for early task-log: agent_run_id=%s validator_round_id=%s",
                agent_run_id,
                validator_round_id,
            )
    except IntegrityError:
        # Concurrent request created the same agent_run_id first.
        logger.debug("Placeholder agent_run already created concurrently: %s", agent_run_id)


async def _upsert_task_log_metadata(
    session: AsyncSession,
    *,
    task_id: str,
    agent_run_id: str,
    validator_round_id: str,
    validator_uid: Optional[int],
    miner_uid: Optional[int],
    season: Optional[int],
    round_in_season: Optional[int],
    object_key: str,
    raw_size: int,
) -> None:
    stmt = select(TaskExecutionLogORM).where(
        TaskExecutionLogORM.task_id == task_id,
        TaskExecutionLogORM.agent_run_id == agent_run_id,
    )
    existing = await session.scalar(stmt)
    if existing:
        existing.validator_round_id = validator_round_id
        existing.validator_uid = validator_uid
        existing.miner_uid = miner_uid
        existing.season = season
        existing.round_in_season = round_in_season
        existing.payload_ref = object_key
        existing.payload_size = raw_size
        return

    session.add(
        TaskExecutionLogORM(
            task_id=task_id,
            agent_run_id=agent_run_id,
            validator_round_id=validator_round_id,
            validator_uid=validator_uid,
            miner_uid=miner_uid,
            season=season,
            round_in_season=round_in_season,
            payload_ref=object_key,
            payload_size=raw_size,
        )
    )


async def _queue_pending_task_log(
    session: AsyncSession,
    *,
    task_id: str,
    agent_run_id: str,
    validator_round_id: str,
    validator_uid: Optional[int],
    miner_uid: Optional[int],
    season: Optional[int],
    round_in_season: Optional[int],
    object_key: str,
    raw_size: int,
    error_message: str,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO task_execution_logs_pending (
                task_id,
                agent_run_id,
                validator_round_id,
                validator_uid,
                miner_uid,
                season,
                round_in_season,
                payload_ref,
                payload_size,
                last_error,
                retry_count,
                created_at,
                updated_at
            )
            VALUES (
                :task_id,
                :agent_run_id,
                :validator_round_id,
                :validator_uid,
                :miner_uid,
                :season,
                :round_in_season,
                :payload_ref,
                :payload_size,
                :last_error,
                0,
                NOW(),
                NOW()
            )
            ON CONFLICT (task_id, agent_run_id) DO UPDATE SET
                validator_round_id = EXCLUDED.validator_round_id,
                validator_uid = EXCLUDED.validator_uid,
                miner_uid = EXCLUDED.miner_uid,
                season = EXCLUDED.season,
                round_in_season = EXCLUDED.round_in_season,
                payload_ref = EXCLUDED.payload_ref,
                payload_size = EXCLUDED.payload_size,
                last_error = EXCLUDED.last_error,
                updated_at = NOW()
            """
        ),
        {
            "task_id": task_id,
            "agent_run_id": agent_run_id,
            "validator_round_id": validator_round_id,
            "validator_uid": validator_uid,
            "miner_uid": miner_uid,
            "season": season,
            "round_in_season": round_in_season,
            "payload_ref": object_key,
            "payload_size": int(raw_size or 0),
            "last_error": error_message[:1000],
        },
    )


async def _flush_pending_task_logs(session: AsyncSession, *, validator_round_id: str, limit: int = 25) -> int:
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT
                        id,
                        task_id,
                        agent_run_id,
                        validator_round_id,
                        validator_uid,
                        miner_uid,
                        season,
                        round_in_season,
                        payload_ref,
                        payload_size
                    FROM task_execution_logs_pending
                    WHERE validator_round_id = :validator_round_id
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                {"validator_round_id": validator_round_id, "limit": int(limit)},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return 0

    flushed = 0
    for row in rows:
        pending_id = int(row["id"])
        try:
            async with session.begin_nested():
                await _ensure_agent_run_exists(
                    session,
                    agent_run_id=str(row["agent_run_id"]),
                    validator_round_id=str(row["validator_round_id"]),
                    miner_uid=int(row["miner_uid"]) if row["miner_uid"] is not None else None,
                )
                await _upsert_task_log_metadata(
                    session,
                    task_id=str(row["task_id"]),
                    agent_run_id=str(row["agent_run_id"]),
                    validator_round_id=str(row["validator_round_id"]),
                    validator_uid=int(row["validator_uid"]) if row["validator_uid"] is not None else None,
                    miner_uid=int(row["miner_uid"]) if row["miner_uid"] is not None else None,
                    season=int(row["season"]) if row["season"] is not None else None,
                    round_in_season=int(row["round_in_season"]) if row["round_in_season"] is not None else None,
                    object_key=str(row["payload_ref"]),
                    raw_size=int(row["payload_size"] or 0),
                )
                await session.execute(
                    text("DELETE FROM task_execution_logs_pending WHERE id = :id"),
                    {"id": pending_id},
                )
            flushed += 1
        except Exception as exc:  # noqa: BLE001
            await session.execute(
                text(
                    """
                    UPDATE task_execution_logs_pending
                    SET retry_count = retry_count + 1,
                        last_error = :last_error,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": pending_id, "last_error": str(exc)[:1000]},
            )
    return flushed


@router.post("", response_model=TaskExecutionLogUploadResponse)
async def upload_task_execution_log(
    request: TaskExecutionLogUploadRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_validator_auth),
) -> TaskExecutionLogUploadResponse:
    """
    Store a per-task execution log in S3 (gzipped JSON) and save metadata in DB.
    """
    try:
        payload_bytes = json.dumps(request.payload, ensure_ascii=False).encode("utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payload JSON: {type(exc).__name__}",
        ) from exc

    raw_size = len(payload_bytes)
    compressed = gzip.compress(payload_bytes)

    try:
        object_key = await store_task_log(
            task_id=request.task_id,
            agent_run_id=request.agent_run_id,
            data=compressed,
            season=request.season,
            round_in_season=request.round_in_season,
            validator_round_id=request.validator_round_id,
        )
    except GifStorageConfigError as exc:
        logger.error("Task log upload failed (S3 not configured): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="S3 not configured for task logs",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Task log upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload task log to S3",
        ) from exc

    payload_url = build_public_url(object_key)
    queued_for_retry = False
    queued_reason: Optional[str] = None
    flushed_pending = 0

    try:
        await _ensure_agent_run_exists(
            session,
            agent_run_id=request.agent_run_id,
            validator_round_id=request.validator_round_id,
            miner_uid=request.miner_uid,
        )
        await _upsert_task_log_metadata(
            session,
            task_id=request.task_id,
            agent_run_id=request.agent_run_id,
            validator_round_id=request.validator_round_id,
            validator_uid=request.validator_uid,
            miner_uid=request.miner_uid,
            season=request.season,
            round_in_season=request.round_in_season,
            object_key=object_key,
            raw_size=raw_size,
        )
        await session.execute(
            text(
                """
                DELETE FROM task_execution_logs_pending
                WHERE task_id = :task_id AND agent_run_id = :agent_run_id
                """
            ),
            {"task_id": request.task_id, "agent_run_id": request.agent_run_id},
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        queued_for_retry = True
        queued_reason = str(exc)
        logger.warning(
            "Task log metadata queued for retry (task_id=%s, agent_run_id=%s, validator_round_id=%s): %s",
            request.task_id,
            request.agent_run_id,
            request.validator_round_id,
            exc,
        )
        try:
            await _queue_pending_task_log(
                session,
                task_id=request.task_id,
                agent_run_id=request.agent_run_id,
                validator_round_id=request.validator_round_id,
                validator_uid=request.validator_uid,
                miner_uid=request.miner_uid,
                season=request.season,
                round_in_season=request.round_in_season,
                object_key=object_key,
                raw_size=raw_size,
                error_message=str(exc),
            )
            await session.commit()
        except Exception as queue_exc:  # noqa: BLE001
            await session.rollback()
            logger.error("Failed to queue pending task log metadata: %s", queue_exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist task log metadata",
            ) from queue_exc

    if not queued_for_retry:
        try:
            flushed_pending = await _flush_pending_task_logs(
                session,
                validator_round_id=request.validator_round_id,
            )
            if flushed_pending > 0:
                await session.commit()
        except Exception as flush_exc:  # noqa: BLE001
            await session.rollback()
            logger.warning("Failed to flush pending task logs for validator_round_id=%s: %s", request.validator_round_id, flush_exc)

    return TaskExecutionLogUploadResponse(
        success=True,
        data={
            "objectKey": object_key,
            "url": payload_url,
            "payloadBytes": raw_size,
            "queuedForRetry": queued_for_retry,
            "queuedReason": queued_reason,
            "flushedPending": int(flushed_pending or 0),
        },
    )

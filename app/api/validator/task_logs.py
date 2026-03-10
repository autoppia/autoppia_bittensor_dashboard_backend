"""
Backoffice endpoint to store per-task execution logs in S3 and metadata in DB.
"""

from __future__ import annotations

import gzip
import json
import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TaskExecutionLogORM
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


@router.post("", response_model=TaskExecutionLogUploadResponse)
async def upload_task_execution_log(
    request: TaskExecutionLogUploadRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[dict, Depends(require_validator_auth)],
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

    try:
        stmt = select(TaskExecutionLogORM).where(
            TaskExecutionLogORM.task_id == request.task_id,
            TaskExecutionLogORM.agent_run_id == request.agent_run_id,
        )
        existing = await session.scalar(stmt)
        if existing:
            existing.validator_round_id = request.validator_round_id
            existing.validator_uid = request.validator_uid
            existing.miner_uid = request.miner_uid
            existing.season = request.season
            existing.round_in_season = request.round_in_season
            existing.payload_ref = object_key
            existing.payload_size = raw_size
        else:
            session.add(
                TaskExecutionLogORM(
                    task_id=request.task_id,
                    agent_run_id=request.agent_run_id,
                    validator_round_id=request.validator_round_id,
                    validator_uid=request.validator_uid,
                    miner_uid=request.miner_uid,
                    season=request.season,
                    round_in_season=request.round_in_season,
                    payload_ref=object_key,
                    payload_size=raw_size,
                )
            )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.error("Failed to persist task log metadata: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist task log metadata",
        ) from exc

    return TaskExecutionLogUploadResponse(
        success=True,
        data={
            "objectKey": object_key,
            "url": payload_url,
            "payloadBytes": raw_size,
        },
    )

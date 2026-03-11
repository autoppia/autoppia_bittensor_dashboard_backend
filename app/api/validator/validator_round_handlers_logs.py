from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.validator.common import _ensure_request_matches_round_owner
from app.api.validator.schemas import ValidatorRoundLogUploadRequest, ValidatorRoundLogUploadResponse
from app.db.session import get_session
from app.services.media_storage import GifStorageConfigError, build_public_url, store_validator_round_log
from app.services.validator.validator_auth import VALIDATOR_HOTKEY_HEADER
from app.services.validator.validator_storage import ValidatorRoundPersistenceService

logger = logging.getLogger(__name__)


async def upload_round_log(
    validator_round_id: str,
    payload: ValidatorRoundLogUploadRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ValidatorRoundLogUploadResponse:
    service = ValidatorRoundPersistenceService(session)

    if payload.validator_round_id != validator_round_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"payload validator_round_id mismatch: got {payload.validator_round_id}, expected {validator_round_id}",
        )

    # Round may not exist if IWAP was reset mid-round; create minimal round so log upload can succeed
    try:
        round_row = await service.ensure_round_exists_or_create_minimal_for_round_log(
            validator_round_id=validator_round_id,
            season=payload.season,
            round_in_season=payload.round_in_season,
            validator_uid=payload.validator_uid,
            validator_hotkey=payload.validator_hotkey,
            owner_hotkey_from_request=request.headers.get(VALIDATOR_HOTKEY_HEADER),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    _ensure_request_matches_round_owner(request, round_row)
    await session.commit()

    try:
        data = payload.content.encode("utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid log content encoding: {type(exc).__name__}",
        ) from exc

    try:
        object_key = await store_validator_round_log(
            validator_round_id=validator_round_id,
            data=data,
            season=payload.season,
            round_in_season=payload.round_in_season,
            validator_uid=payload.validator_uid,
            validator_hotkey=payload.validator_hotkey,
        )
    except GifStorageConfigError as exc:
        logger.error("Round log upload failed (S3 not configured): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="S3 not configured for round log uploads",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Round log upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload round log to S3",
        ) from exc

    payload_url = build_public_url(object_key)
    try:
        # Persist S3 URL immediately so operator can inspect logs before round finish.
        round_row.s3_logs_url = payload_url
        if isinstance(round_row.validator_summary, dict):
            summary = dict(round_row.validator_summary)
            summary["s3_logs_url"] = payload_url
            round_row.validator_summary = summary

        # Keep canonical table aligned during active rounds.
        await session.execute(
            text(
                """
                UPDATE round_validators
                SET s3_logs_url = :s3_logs_url, updated_at = NOW()
                WHERE validator_round_id = :validator_round_id
                """
            ),
            {
                "validator_round_id": validator_round_id,
                "s3_logs_url": payload_url,
            },
        )
        await session.commit()
    except Exception:
        logger.exception(
            "Round log uploaded to S3 but failed to persist s3_logs_url for validator_round_id=%s",
            validator_round_id,
        )
        await session.rollback()

    return ValidatorRoundLogUploadResponse(
        success=True,
        data={
            "objectKey": object_key,
            "url": payload_url,
            "payloadBytes": len(data),
            "validator_round_id": validator_round_id,
            "validator_uid": payload.validator_uid,
            "validator_hotkey": payload.validator_hotkey,
        },
    )

"""Media storage utilities for handling GIF uploads and task logs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import boto3
from botocore.client import BaseClient

from app.config import settings

logger = logging.getLogger(__name__)


class GifStorageConfigError(RuntimeError):
    """Raised when required S3 configuration is missing."""


@lru_cache(maxsize=1)
def _get_s3_client() -> BaseClient:
    """Return a cached S3 client configured from environment settings."""
    if not settings.AWS_S3_BUCKET:
        raise GifStorageConfigError("AWS_S3_BUCKET is not configured")

    session = boto3.session.Session(
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        aws_session_token=settings.AWS_SESSION_TOKEN or None,
        region_name=settings.AWS_REGION or None,
    )

    return session.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL or None,
    )


def get_s3_client() -> BaseClient:
    """Expose the cached S3 client for callers that need direct access."""
    return _get_s3_client()


def reset_s3_client_cache() -> None:
    """Clear the cached client (useful for tests)."""
    _get_s3_client.cache_clear()


def _gif_prefix() -> str:
    return settings.AWS_S3_GIF_PREFIX.strip("/")


def _task_log_prefix() -> str:
    return settings.AWS_S3_TASK_LOG_PREFIX.strip("/")


def build_gif_key(evaluation_id: str) -> str:
    """Return the object key used for storing an evaluation GIF."""
    filename = f"{evaluation_id}.gif"
    prefix = _gif_prefix()
    if prefix:
        return f"{prefix}/{filename}"
    return filename


def build_task_log_key(
    task_id: str,
    agent_run_id: str,
    *,
    season: Optional[int] = None,
    round_in_season: Optional[int] = None,
    validator_round_id: Optional[str] = None,
) -> str:
    """Return the object key used for storing a per-task execution log."""
    safe_task_id = str(task_id).replace("/", "_")
    safe_agent_run_id = str(agent_run_id).replace("/", "_")
    filename = f"{safe_task_id}_{safe_agent_run_id}.json.gz"
    parts = [_task_log_prefix()]
    if season is not None and round_in_season is not None:
        parts.append(f"season={season}")
        parts.append(f"round={round_in_season}")
    if validator_round_id:
        parts.append(f"validator_round_id={validator_round_id}")
    parts.append(filename)
    return "/".join([p for p in parts if p])


def build_public_url(object_key: str) -> str:
    """Construct the public URL for an object key."""
    normalized = object_key.lstrip("/")
    public_base = settings.AWS_S3_PUBLIC_BASE_URL or settings.ASSET_BASE_URL
    if public_base:
        return f"{public_base.rstrip('/')}/{normalized}"

    bucket = settings.AWS_S3_BUCKET
    region = settings.AWS_REGION or "us-east-1"
    if region == "us-east-1":
        base = f"https://{bucket}.s3.amazonaws.com"
    else:
        base = f"https://{bucket}.s3.{region}.amazonaws.com"
    return f"{base}/{normalized}"


async def store_gif(evaluation_id: str, data: bytes) -> str:
    """Upload GIF bytes to S3 and return the object key."""
    client = get_s3_client()
    object_key = build_gif_key(evaluation_id)
    bucket = settings.AWS_S3_BUCKET

    logger.debug("Uploading evaluation %s GIF to s3://%s/%s", evaluation_id, bucket, object_key)
    await asyncio.to_thread(
        client.put_object,
        Bucket=bucket,
        Key=object_key,
        Body=data,
        ContentType="image/gif",
    )

    return object_key


async def store_task_log(
    *,
    task_id: str,
    agent_run_id: str,
    data: bytes,
    season: Optional[int] = None,
    round_in_season: Optional[int] = None,
    validator_round_id: Optional[str] = None,
) -> str:
    """Upload a gzipped JSON task log to S3 and return the object key."""
    client = get_s3_client()
    object_key = build_task_log_key(
        task_id,
        agent_run_id,
        season=season,
        round_in_season=round_in_season,
        validator_round_id=validator_round_id,
    )
    bucket = settings.AWS_S3_BUCKET
    timestamp = datetime.now(timezone.utc).isoformat()

    logger.debug(
        "Uploading task log for task %s to s3://%s/%s (bytes=%d)",
        task_id,
        bucket,
        object_key,
        len(data),
    )
    await asyncio.to_thread(
        client.put_object,
        Bucket=bucket,
        Key=object_key,
        Body=data,
        ContentType="application/json",
        ContentEncoding="gzip",
        Metadata={"uploaded_at": timestamp},
    )

    return object_key


__all__ = [
    "GifStorageConfigError",
    "build_gif_key",
    "build_task_log_key",
    "build_public_url",
    "get_s3_client",
    "reset_s3_client_cache",
    "store_gif",
    "store_task_log",
]

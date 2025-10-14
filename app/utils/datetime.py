"""
Datetime helper utilities shared across models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def to_datetime(value: Any) -> Optional[datetime]:
    """Coerce numeric timestamps or ISO strings to timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid datetime value: {value}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise ValueError(f"Unsupported datetime type: {type(value)!r}")

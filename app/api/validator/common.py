from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from app.services.validator.validator_auth import VALIDATOR_HOTKEY_HEADER


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

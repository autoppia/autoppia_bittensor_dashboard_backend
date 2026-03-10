"""
Auth-check endpoint handler for validator round API.
"""

from __future__ import annotations

from pydantic import BaseModel


class ValidatorAuthCheckResponse(BaseModel):
    """Response for GET /auth-check (Sonar: avoid raw dict)."""

    message: str = "Validator authentication verified"


def validator_auth_check() -> ValidatorAuthCheckResponse:
    """Lightweight endpoint validators can call to verify auth headers before starting a round."""
    return ValidatorAuthCheckResponse()

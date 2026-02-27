from __future__ import annotations

from typing import Any


async def validator_auth_check() -> dict[str, Any]:
    """Lightweight endpoint validators can call to verify auth headers before starting a round."""
    return {"message": "Validator authentication verified"}

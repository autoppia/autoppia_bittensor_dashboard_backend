from __future__ import annotations

import contextlib
import os
from typing import Optional

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.services.validator.validator_auth import (
    VALIDATOR_HOTKEY_HEADER,
    VALIDATOR_SIGNATURE_HEADER,
    ValidatorAuthService,
    get_validator_auth_service,
)

# Skip this file unless explicitly enabled (hits live subtensor network)
if os.getenv("RUN_LIVE_TESTS", "0").lower() not in ("1", "true", "yes", "on"):
    import pytest  # noqa: E402

    pytest.skip("Skipping live stake tests (set RUN_LIVE_TESTS=1 to run)", allow_module_level=True)


class _SigNoop_RealStake_Service:
    """Stub service that bypasses signature verification but uses real stake logic."""

    def __init__(self) -> None:
        self._real = ValidatorAuthService()

    def verify_signature(self, *, hotkey: str, signature_b64: str) -> None:  # noqa: ARG002
        return None

    def has_minimum_stake(self, hotkey: str) -> bool:
        return self._real.has_minimum_stake(hotkey)


@contextlib.asynccontextmanager
async def _auth_live_stake_override():
    original_disabled = settings.AUTH_DISABLED
    settings.AUTH_DISABLED = False

    original_dep = app.dependency_overrides.get(get_validator_auth_service)
    app.dependency_overrides[get_validator_auth_service] = lambda: _SigNoop_RealStake_Service()
    try:
        yield
    finally:
        settings.AUTH_DISABLED = original_disabled
        if original_dep is None:
            with contextlib.suppress(KeyError):
                del app.dependency_overrides[get_validator_auth_service]
        else:
            app.dependency_overrides[get_validator_auth_service] = original_dep


def _find_high_stake_validator_hotkey(threshold: float) -> Optional[str]:
    import bittensor as bt  # type: ignore

    network = settings.SUBTENSOR_NETWORK or "finney"
    netuid = int(settings.VALIDATOR_NETUID)
    mg = bt.subtensor(network=network).metagraph(netuid=netuid)
    mask = getattr(mg, "validator_permit", None)
    if mask is None:
        return None
    for uid, is_val in enumerate(mask):
        if not is_val:
            continue
        if float(mg.stake[uid]) > threshold:
            return str(mg.hotkeys[uid])
    return None


@pytest.mark.asyncio
async def test_auth_check_uses_live_stake_threshold():
    # Arrange: find a validator hotkey above the configured threshold
    threshold = float(settings.MIN_VALIDATOR_STAKE or 0.0)
    hotkey = _find_high_stake_validator_hotkey(threshold)
    assert hotkey, "Expected to find a validator with stake above threshold"

    async with _auth_live_stake_override():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Should pass with current threshold
            headers = {
                VALIDATOR_HOTKEY_HEADER: hotkey,
                VALIDATOR_SIGNATURE_HEADER: "c2ln",  # ignored by stub
            }
            resp = await client.post("/api/v1/validator-rounds/auth-check", headers=headers)
            assert resp.status_code == 200, resp.text

            # Now bump threshold above the chosen hotkey to force 403
            prev = settings.MIN_VALIDATOR_STAKE
            try:
                settings.MIN_VALIDATOR_STAKE = 9_999_999_999.0
                resp = await client.post("/api/v1/validator-rounds/auth-check", headers=headers)
                assert resp.status_code == 403, resp.text
                assert "below the required minimum" in resp.json().get("detail", "")
            finally:
                settings.MIN_VALIDATOR_STAKE = prev

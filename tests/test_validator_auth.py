from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.main import app
from app.config import settings
from app.services.validator.validator_auth import (
    get_validator_auth_service,
    VALIDATOR_HOTKEY_HEADER,
    VALIDATOR_SIGNATURE_HEADER,
)


class _StubAuthService:
    def __init__(self, *, min_stake: float = 1.0, should_fail_signature: bool = False, should_fail_stake: bool = False):
        self.min_stake = float(min_stake)
        self.should_fail_signature = should_fail_signature
        self.should_fail_stake = should_fail_stake

    def verify_signature(self, *, hotkey: str, signature_b64: str) -> None:  # noqa: ARG002
        if self.should_fail_signature:
            raise AssertionError("signature fail (stub)")

    def ensure_minimum_stake(self, hotkey: str) -> float:  # noqa: ARG002
        if self.should_fail_stake:
            raise AssertionError("stake too low (stub)")
        return self.min_stake


@contextlib.asynccontextmanager
async def _auth_override(*, disabled: bool = False, stub: _StubAuthService | None = None):
    """Temporarily enforce auth and inject a stub service for tests."""
    # Force-enable or -disable auth for the test duration
    original_disabled = settings.AUTH_DISABLED
    settings.AUTH_DISABLED = bool(disabled)

    # Override dependency to avoid network/bittensor
    original_dep = app.dependency_overrides.get(get_validator_auth_service)
    if stub is None:
        stub = _StubAuthService()
    app.dependency_overrides[get_validator_auth_service] = lambda: stub

    try:
        yield
    finally:
        # Restore
        settings.AUTH_DISABLED = original_disabled
        if original_dep is None:
            with contextlib.suppress(KeyError):
                del app.dependency_overrides[get_validator_auth_service]
        else:
            app.dependency_overrides[get_validator_auth_service] = original_dep


@pytest.mark.asyncio
async def test_auth_check_requires_headers_when_enabled(client):
    async with _auth_override(disabled=False):
        resp = await client.post("/api/v1/validator-rounds/auth-check")
        assert resp.status_code == 401
        assert "headers are required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_check_passes_with_stubbed_validation(client):
    async with _auth_override(disabled=False, stub=_StubAuthService(min_stake=float(settings.MIN_VALIDATOR_STAKE) or 0.0)):
        headers = {
            VALIDATOR_HOTKEY_HEADER: "5FStubbedHotkey11111111111111111111111111111",
            VALIDATOR_SIGNATURE_HEADER: "c3R1Yi1zaWduYXR1cmU=",  # base64('stub-signature')
        }
        resp = await client.post("/api/v1/validator-rounds/auth-check", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["message"].lower().startswith("validator authentication verified")


@pytest.mark.asyncio
async def test_start_round_rejects_when_header_hotkey_mismatch(client):
    async with _auth_override(disabled=False):
        headers = {
            VALIDATOR_HOTKEY_HEADER: "5FHeaderHotkeyMismatchxxxxxxxxxxxxxxxxxxxx",
            VALIDATOR_SIGNATURE_HEADER: "c2ln",
        }
        payload = {
            "validator_round_id": "auth_round_001",
            "round": {
                "validator_round_id": "auth_round_001",
                "round": 1,
                "validators": [
                    {
                        "uid": 42,
                        "hotkey": "5FPayloadHotkeyYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
                        "coldkey": None,
                        "stake": 100.0,
                        "vtrust": 0.9,
                        "name": "Auth Test",
                        "version": "0.0.1",
                    }
                ],
                "start_block": 1,
                "start_epoch": 1,
                "n_tasks": 1,
                "n_miners": 1,
                "n_winners": 1,
                "started_at": 1_700_000_000.0,
                "status": "in_progress",
            },
        }
        resp = await client.post("/api/v1/validator-rounds/start", json=payload, headers=headers)
        assert resp.status_code == 400
        assert "does not match" in resp.json()["detail"].lower()


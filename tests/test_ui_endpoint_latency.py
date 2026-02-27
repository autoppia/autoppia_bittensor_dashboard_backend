from __future__ import annotations

import time

import pytest


@pytest.mark.asyncio
async def test_ui_get_endpoints_are_fast(client, monkeypatch):
    """Seed data then assert representative UI GET endpoints respond quickly.

    This also implicitly verifies that no on-demand Subtensor fetch happens
    during UI calls, since the chain state is estimated from cache only.
    """
    # Seed a minimal round using existing helper
    from .test_validator_endpoints import _make_submission_payload, submit_round_via_validator_endpoints

    payload = _make_submission_payload("801")
    submit_resp = await submit_round_via_validator_endpoints(client, payload)
    assert submit_resp.status_code == 200

    # Measure a few representative UI endpoints
    endpoints = [
        ("/api/v1/overview/network-status", {}),
        ("/api/v1/rounds", {}),
        ("/api/v1/rounds/current", {}),
    ]

    # Resolve a round id for progress endpoint
    current_round_resp = await client.get("/api/v1/rounds/current")
    assert current_round_resp.status_code == 200
    current_round = current_round_resp.json()["data"]["round"]
    round_id = current_round.get("id") or current_round.get("round") or current_round.get("roundNumber")
    if round_id is None:
        round_id = int(payload["round"]["round"])  # fallback to seeded value

    endpoints.append((f"/api/v1/rounds/{round_id}/progress", {}))

    # Assert each call completes under a generous threshold
    MAX_SECONDS = 0.75
    for path, params in endpoints:
        t0 = time.perf_counter()
        resp = await client.get(path, params=params or None)
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        assert elapsed <= MAX_SECONDS, f"GET {path} took {elapsed:.3f}s (> {MAX_SECONDS}s)"

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ui_endpoints_do_not_fetch_chain(client, monkeypatch):
    """UI GET endpoints must not trigger real chain fetches.

    We simulate this by forcing any call to get_current_block() to raise if
    invoked. Endpoints should use get_current_block_estimate() instead.
    """
    # Seed one round so UI endpoints have data
    from tests.test_validator_endpoints import _make_submission_payload, submit_round_via_validator_endpoints

    payload = _make_submission_payload("901")
    submit_resp = await submit_round_via_validator_endpoints(client, payload)
    # This test validates GET behavior only; some environments require
    # validator auth headers for POST seed endpoints.
    assert submit_resp.status_code in {200, 401}

    # Make get_current_block explode if called
    import app.services.chain_state as chain_state

    def _bomb():  # pragma: no cover - defensive helper
        raise RuntimeError("should not fetch chain on GET")

    monkeypatch.setattr(chain_state, "get_current_block", _bomb, raising=True)

    # Representative UI GETs
    ui_paths = [
        "/api/v1/overview/network-status",
        "/api/v1/rounds",
        "/api/v1/rounds/current",
    ]

    for path in ui_paths:
        r = await client.get(path)
        assert r.status_code in {200, 404}

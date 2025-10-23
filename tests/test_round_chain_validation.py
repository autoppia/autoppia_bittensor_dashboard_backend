from __future__ import annotations

import pytest


def _block_for_round(settings, rnd: int, *, position: str = "inside") -> int:
    """Return a current_block representing the requested round position.

    position:
      - "inside": strictly within window (start < block <= end)
      - "before": at or before start (<= start)
      - "after": strictly after end (> end)
    """
    dz = int(settings.DZ_STARTING_BLOCK)
    blocks_per_round = int(settings.ROUND_SIZE_EPOCHS * settings.BLOCKS_PER_EPOCH)
    start = dz + (rnd - 1) * blocks_per_round
    end = dz + rnd * blocks_per_round
    if position == "inside":
        return start + 1
    if position == "before":
        return start
    if position == "after":
        return end + 1
    raise ValueError("Unknown position")


def _mk_round_payload(round_number: int, *, uid: int = 1001) -> dict:
    vid = f"round_chain_{round_number}_{uid}"
    return {
        "validator_round_id": vid,
        "round": {
            "validator_round_id": vid,
            "round": round_number,
            "validators": [
                {
                    "uid": uid,
                    "hotkey": "5FHeaderHotkey111111111111111111111111111111",
                    "coldkey": None,
                    "stake": 100.0,
                    "vtrust": 0.9,
                    "name": "V",
                    "version": "0.0.1",
                }
            ],
            # Will be overridden by backend; provided for compatibility
            "start_block": 1,
            "start_epoch": 1,
            "n_tasks": 1,
            "n_miners": 1,
            "n_winners": 1,
            "started_at": 1_700_000_000.0,
            "status": "in_progress",
        },
    }


@pytest.mark.asyncio
async def test_start_round_rejects_round_number_mismatch(client, monkeypatch):
    from app.config import settings
    from app.services import chain_state as chain

    backend_round = 5
    # Force chain to backend_round
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, backend_round, position="inside"))

    # Payload claims a different round
    payload = _mk_round_payload(backend_round + 1)
    resp = await client.post("/api/v1/validator-rounds/start", json=payload)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["expectedRoundNumber"] == backend_round
    assert detail["got"] == backend_round + 1


@pytest.mark.asyncio
async def test_start_round_must_be_inside_window(client, monkeypatch):
    from app.config import settings
    from app.services import chain_state as chain

    rnd = 3
    # Force chain to be before the round window
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="before"))

    payload = _mk_round_payload(rnd)
    resp = await client.post("/api/v1/validator-rounds/start", json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "round window not active"


@pytest.mark.asyncio
async def test_finish_round_rejects_not_started_or_already_finished(client, monkeypatch):
    from app.config import settings
    from app.services import chain_state as chain

    rnd = 2
    # First, accept start inside the window
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="inside"))
    payload = _mk_round_payload(rnd)
    sr = await client.post("/api/v1/validator-rounds/start", json=payload)
    assert sr.status_code == 200

    vrid = payload["validator_round_id"]

    # Not started yet (set current before start)
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="before"))
    fr = await client.post(f"/api/v1/validator-rounds/{vrid}/finish", json={"status": "completed", "winners": [], "winner_scores": [], "weights": {}})
    assert fr.status_code == 409
    assert fr.json()["detail"]["error"] == "round not started"

    # Already finished (set current after end)
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="after"))
    fr2 = await client.post(f"/api/v1/validator-rounds/{vrid}/finish", json={"status": "completed", "winners": [], "winner_scores": [], "weights": {}})
    assert fr2.status_code == 409
    assert fr2.json()["detail"]["error"] == "round already finished"


@pytest.mark.asyncio
async def test_rounds_list_hides_not_started_rounds(client, monkeypatch):
    from app.config import settings
    from app.services import chain_state as chain

    rnd = 7
    # Start round while inside window so it's stored
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="inside"))
    payload = _mk_round_payload(rnd)
    assert (await client.post("/api/v1/validator-rounds/start", json=payload)).status_code == 200

    # Now pretend chain is before start: list should filter it out
    monkeypatch.setattr(chain, "get_current_block", lambda: _block_for_round(settings, rnd, position="before"))
    res = await client.get("/api/v1/rounds?limit=10&page=1&sortBy=round&sortOrder=desc")
    assert res.status_code == 200
    data = res.json()
    # When chain before start, no started rounds
    assert isinstance(data, dict)
    assert data.get("data", {}).get("rounds", []) == []


@pytest.mark.asyncio
async def test_round_progress_includes_chain_fields(client, monkeypatch):
    from app.config import settings
    from app.services import chain_state as chain

    rnd = 4
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _block_for_round(settings, rnd, position="inside"))
    payload = _mk_round_payload(rnd)
    assert (await client.post("/api/v1/validator-rounds/start", json=payload)).status_code == 200

    vrid = payload["validator_round_id"]
    pr = await client.get(f"/api/v1/rounds/{vrid}/progress")
    assert pr.status_code == 200
    body = pr.json()
    assert body["success"] is True
    prog = body["data"]["progress"]
    # Chain-derived fields
    assert "startEpoch" in prog and "endEpoch" in prog and "currentEpoch" in prog
    assert 0.0 <= prog.get("progress", 0.0) <= 1.0

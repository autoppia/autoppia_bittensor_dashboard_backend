from __future__ import annotations

import pytest


def _round_window(cfg: dict, round_number: int) -> tuple[int, int]:
    round_blocks = int(float(cfg["round_size_epochs"]) * int(cfg["blocks_per_epoch"]))
    start_block = int(cfg["minimum_start_block"]) + (round_number - 1) * round_blocks
    end_block = start_block + round_blocks
    return start_block, end_block


def _block_for_round(cfg: dict, round_number: int, *, position: str = "inside") -> int:
    start_block, end_block = _round_window(cfg, round_number)
    if position == "inside":
        return start_block
    if position == "before":
        return start_block - 1
    if position == "after":
        return end_block + 1
    raise ValueError(f"Unknown position: {position}")


def _mk_round_payload(cfg: dict, round_number: int, *, uid: int = 1001) -> dict:
    start_block, _ = _round_window(cfg, round_number)
    hotkey = cfg["main_validator_hotkey"]
    validator_round_id = f"round_chain_{round_number}_{uid}"
    return {
        "validator_identity": {
            "uid": uid,
            "hotkey": hotkey,
            "coldkey": None,
        },
        "validator_round": {
            "validator_round_id": validator_round_id,
            "season_number": 1,
            "round_number_in_season": round_number,
            "validator_uid": uid,
            "validator_hotkey": hotkey,
            "validator_coldkey": None,
            "start_block": start_block,
            "end_block": None,
            "start_epoch": 1,
            "end_epoch": None,
            "started_at": 1_700_000_000.0 + float(round_number),
            "ended_at": None,
            "n_tasks": 1,
            "status": "active",
            "metadata": {},
        },
        "validator_snapshot": {
            "validator_round_id": validator_round_id,
            "validator_uid": uid,
            "validator_hotkey": hotkey,
            "validator_coldkey": None,
            "name": "Validator",
            "stake": 100.0,
            "vtrust": 0.9,
            "image_url": None,
            "version": "20.0.0",
            "config": {
                "round": {
                    "tasks_per_season": 1,
                }
            },
        },
    }


async def _start_round(configured_client, cfg: dict, round_number: int) -> dict:
    payload = _mk_round_payload(cfg, round_number)
    response = await configured_client.post("/api/v1/validator-rounds/start", json=payload)
    assert response.status_code == 200, response.text
    return payload


@pytest.mark.asyncio
async def test_start_round_rejects_before_window(configured_client, seeded_runtime_round_config, monkeypatch):
    cfg = seeded_runtime_round_config
    payload = _mk_round_payload(cfg, 3)
    monkeypatch.setattr(
        "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
        lambda: _block_for_round(cfg, 3, position="before"),
    )

    response = await configured_client.post("/api/v1/validator-rounds/start", json=payload)

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"] == "round window not active"
    assert body["detail"]["currentBlock"] == _block_for_round(cfg, 3, position="before")
    assert body["detail"]["startBlock"] == payload["validator_round"]["start_block"]


@pytest.mark.asyncio
async def test_start_round_accepts_exact_start_block(configured_client, seeded_runtime_round_config, monkeypatch):
    cfg = seeded_runtime_round_config
    payload = _mk_round_payload(cfg, 4)
    start_block, _ = _round_window(cfg, 4)
    monkeypatch.setattr(
        "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
        lambda: start_block,
    )

    response = await configured_client.post("/api/v1/validator-rounds/start", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validator_round_id"] == payload["validator_round"]["validator_round_id"]
    assert payload["validator_round"]["start_block"] == start_block


@pytest.mark.asyncio
async def test_start_round_accepts_exact_end_block(configured_client, seeded_runtime_round_config, monkeypatch):
    cfg = seeded_runtime_round_config
    payload = _mk_round_payload(cfg, 5)
    _, end_block = _round_window(cfg, 5)
    monkeypatch.setattr(
        "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
        lambda: end_block,
    )

    response = await configured_client.post("/api/v1/validator-rounds/start", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["validator_round_id"] == payload["validator_round"]["validator_round_id"]


@pytest.mark.asyncio
async def test_rounds_list_returns_active_round_payload(configured_client, seeded_runtime_round_config, monkeypatch):
    cfg = seeded_runtime_round_config
    monkeypatch.setattr(
        "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
        lambda: _block_for_round(cfg, 7, position="inside"),
    )
    monkeypatch.setattr(
        "app.services.chain_state.get_current_block",
        lambda: _block_for_round(cfg, 7, position="inside"),
    )
    payload = await _start_round(configured_client, cfg, 7)

    response = await configured_client.get("/api/v1/rounds?limit=10&page=1&sortBy=round&sortOrder=desc")

    assert response.status_code == 200, response.text
    body = response.json()
    rounds = body["data"]["rounds"]
    assert len(rounds) == 1
    round_payload = rounds[0]
    assert round_payload["roundKey"] == "1/7"
    assert round_payload["id"] == 10007
    assert round_payload["status"] == "active"
    assert round_payload["startBlock"] == payload["validator_round"]["start_block"]
    assert round_payload["current"] is True


@pytest.mark.asyncio
async def test_round_progress_includes_chain_fields(configured_client, seeded_runtime_round_config, monkeypatch):
    cfg = seeded_runtime_round_config
    current_block = _block_for_round(cfg, 6, position="inside")
    monkeypatch.setattr(
        "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
        lambda: current_block,
    )
    monkeypatch.setattr(
        "app.api.ui.rounds.get_current_block_estimate",
        lambda: current_block,
    )
    payload = await _start_round(configured_client, cfg, 6)
    start_block, end_block = _round_window(cfg, 6)

    response = await configured_client.get("/api/v1/rounds/1/6/progress")

    assert response.status_code == 200, response.text
    body = response.json()
    progress = body["data"]["progress"]
    assert progress["roundId"] == 10006
    assert progress["season"] == 1
    assert progress["roundInSeason"] == 6
    assert progress["currentBlock"] == current_block
    assert progress["startBlock"] == start_block == payload["validator_round"]["start_block"]
    assert progress["endBlock"] == end_block
    assert 0.0 <= progress["progress"] <= 1.0

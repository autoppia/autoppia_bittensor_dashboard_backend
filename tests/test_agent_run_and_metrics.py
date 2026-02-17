from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import AgentEvaluationRunORM
from app.services.validator.validator_auth import (
    get_validator_auth_service,
    VALIDATOR_HOTKEY_HEADER,
    VALIDATOR_SIGNATURE_HEADER,
)


class _StubAuthService:
    def verify_signature(self, *, hotkey: str, signature_b64: str) -> None:  # noqa: ARG002
        return None

    def ensure_minimum_stake(self, hotkey: str) -> float:  # noqa: ARG002
        return 1.0


def _headers() -> dict[str, str]:
    return {
        VALIDATOR_HOTKEY_HEADER: "5FHeaderHotkey111111111111111111111111111111",
        VALIDATOR_SIGNATURE_HEADER: "c2ln",
    }


async def _start_minimal_round(
    client,
    *,
    round_id: str,
    validator_uid: int = 1001,
    round_number: int = 1,
    force: bool = True,
):
    payload = {
        "validator_round_id": round_id,
        "round": {
            "validator_round_id": round_id,
            "round": round_number,
            "validators": [
                {
                    "uid": validator_uid,
                    "hotkey": "5FHeaderHotkey111111111111111111111111111111",
                    "coldkey": None,
                    "stake": 100.0,
                    "vtrust": 0.9,
                    "name": "V",
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
    url = "/api/v1/validator-rounds/start"
    if force:
        url = f"{url}?force=true"
    resp = await client.post(url, json=payload, headers=_headers())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_start_agent_run_non_sota_requires_identity(client, monkeypatch):
    # Enforce auth and stub service
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    round_id = "run_non_sota_requirements"
    await _start_minimal_round(client, round_id=round_id)

    # Missing miner_identity.uid/hotkey
    bad_payload = {
        "agent_run": {
            "agent_run_id": "run_A",
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 501,
            "is_sota": False,
        },
        "miner_identity": {"uid": None, "hotkey": None},
        "miner_snapshot": {"validator_round_id": round_id, "agent_name": "A"},
    }
    resp = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=bad_payload,
        headers=_headers(),
    )
    assert resp.status_code == 400
    # Pydantic validation enforces uid/hotkey for non-SOTA miners
    detail = resp.json()["detail"]
    assert "uid" in detail.lower() or "hotkey" in detail.lower() or "miner_identity" in detail

    # Consistent identity
    good_payload = {
        "agent_run": {
            "agent_run_id": "run_B",
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 501,
            "miner_hotkey": "miner_hotkey_501",
            "is_sota": False,
        },
        "miner_identity": {"uid": 501, "hotkey": "miner_hotkey_501"},
        "miner_snapshot": {
            "validator_round_id": round_id,
            "miner_uid": 501,
            "miner_hotkey": "miner_hotkey_501",
            "agent_name": "B",
        },
    }
    resp2 = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=good_payload,
        headers=_headers(),
    )
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_start_agent_run_sota_allowed(client, monkeypatch):
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    round_id = "run_sota_requirements"
    await _start_minimal_round(client, round_id=round_id)

    # SOTA run without miner_uid/hotkey is allowed
    ok_payload = {
        "agent_run": {
            "agent_run_id": "sota_run_C",
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "is_sota": True,
        },
        "miner_identity": {},
        "miner_snapshot": {"validator_round_id": round_id, "agent_name": "Bench"},
    }
    resp3 = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=ok_payload,
        headers=_headers(),
    )
    assert resp3.status_code == 200


@pytest.mark.asyncio
async def test_start_agent_run_idempotent_on_duplicate_same_round(client, db_session, monkeypatch):
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    round_id = "idempotent_run"
    await _start_minimal_round(client, round_id=round_id)

    payload = {
        "agent_run": {
            "agent_run_id": "duplicate_run",
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 123,
            "miner_hotkey": "miner_hotkey_123",
            "is_sota": False,
        },
        "miner_identity": {"uid": 123, "hotkey": "miner_hotkey_123"},
        "miner_snapshot": {
            "validator_round_id": round_id,
            "miner_uid": 123,
            "miner_hotkey": "miner_hotkey_123",
            "agent_name": "M",
        },
    }

    r1 = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=payload,
        headers=_headers(),
    )
    assert r1.status_code == 200

    r2 = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=payload,
        headers=_headers(),
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["message"].lower().startswith("agent run registered")

    row = await db_session.scalar(select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == "duplicate_run"))
    assert row is not None


@pytest.mark.asyncio
async def test_add_evaluation_relationship_mismatch_rejected(client, monkeypatch):
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    round_id = "rel_mismatch"
    await _start_minimal_round(client, round_id=round_id)

    # Define a task
    task = {
        "task_id": "task1",
        "validator_round_id": round_id,
        "is_web_real": False,
        "web_project_id": None,
        "url": "https://example.com/1",
        "prompt": "P",
        "specifications": {},
        "tests": [],
        "use_case": {"name": "X"},
    }
    r_tasks = await client.post(
        f"/api/v1/validator-rounds/{round_id}/tasks?force=true",
        json={"tasks": [task]},
        headers=_headers(),
    )
    assert r_tasks.status_code == 200

    # Start agent run
    run_id = "run_rel"
    start_run = {
        "agent_run": {
            "agent_run_id": run_id,
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 1,
            "miner_hotkey": "miner_hotkey_1",
            "is_sota": False,
        },
        "miner_identity": {"uid": 1, "hotkey": "miner_hotkey_1"},
        "miner_snapshot": {
            "validator_round_id": round_id,
            "miner_uid": 1,
            "miner_hotkey": "miner_hotkey_1",
            "agent_name": "M",
        },
    }
    r_start = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=start_run,
        headers=_headers(),
    )
    assert r_start.status_code == 200

    # Mismatched task_solution.task_id
    bad_eval = {
        "task": task,
        "task_solution": {
            "solution_id": "sol1",
            "task_id": "another_task",
            "validator_round_id": round_id,
            "agent_run_id": run_id,
            "validator_uid": 1001,
        },
        "evaluation_result": {
            "evaluation_id": "eval1",
            "task_id": task["task_id"],  # does not match task_solution.task_id
            "task_solution_id": "sol1",
            "validator_round_id": round_id,
            "agent_run_id": run_id,
            "validator_uid": 1001,
            "final_score": 0.8,
            "test_results_matrix": [[{"success": True}]],
            "execution_history": [],
            "feedback": None,
            "web_agent_id": None,
            "raw_score": 0.8,
            "evaluation_time": 1.0,
            "stats": None,
            "gif_recording": None,
        },
    }
    r_eval = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/{run_id}/evaluations?force=true",
        json=bad_eval,
        headers=_headers(),
    )
    assert r_eval.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_agent_run_id_in_different_round_conflicts(client, monkeypatch):
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    # Helpers to set chain block inside a given round window
    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def _inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    # Start round 1 with chain inside round 1
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _inside_round(1))
    await _start_minimal_round(client, round_id="round_A", round_number=1)

    # Start round 2 with chain inside round 2
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _inside_round(2))
    await _start_minimal_round(client, round_id="round_B", round_number=2)

    payload_A = {
        "agent_run": {
            "agent_run_id": "DUP_RUN",
            "validator_round_id": "round_A",
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 1,
            "miner_hotkey": "m1",
            "is_sota": False,
        },
        "miner_identity": {"uid": 1, "hotkey": "m1"},
        "miner_snapshot": {
            "validator_round_id": "round_A",
            "miner_uid": 1,
            "miner_hotkey": "m1",
            "agent_name": "M",
        },
    }
    # Chain must match round 1 window for starting agent run on round_A
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _inside_round(1))
    rA = await client.post(
        "/api/v1/validator-rounds/round_A/agent-runs/start",
        json=payload_A,
        headers=_headers(),
    )
    assert rA.status_code == 200

    payload_B = {
        "agent_run": {
            "agent_run_id": "DUP_RUN",
            "validator_round_id": "round_B",
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 2,
            "miner_hotkey": "m2",
            "is_sota": False,
        },
        "miner_identity": {"uid": 2, "hotkey": "m2"},
        "miner_snapshot": {
            "validator_round_id": "round_B",
            "miner_uid": 2,
            "miner_hotkey": "m2",
            "agent_name": "M",
        },
    }
    # Chain must match round 2 window for starting agent run on round_B
    monkeypatch.setattr("app.api.validator.validator_round.get_current_block", lambda: _inside_round(2))
    rB = await client.post(
        "/api/v1/validator-rounds/round_B/agent-runs/start",
        json=payload_B,
        headers=_headers(),
    )
    assert rB.status_code == 409


@pytest.mark.asyncio
async def test_finish_round_computes_run_metrics_and_top_miners(client, db_session, monkeypatch):
    from app.config import settings as _settings
    from app.main import app

    monkeypatch.setattr(_settings, "AUTH_DISABLED", False)
    app.dependency_overrides[get_validator_auth_service] = lambda: _StubAuthService()

    round_id = "metrics_round"
    await _start_minimal_round(client, round_id=round_id)

    # One task
    task = {
        "task_id": "task_metrics",
        "validator_round_id": round_id,
        "is_web_real": False,
        "web_project_id": None,
        "url": "https://example.com/metrics",
        "prompt": "Compute",
        "specifications": {},
        "tests": [],
        "use_case": {"name": "X"},
    }
    r_tasks = await client.post(
        f"/api/v1/validator-rounds/{round_id}/tasks?force=true",
        json={"tasks": [task]},
        headers=_headers(),
    )
    assert r_tasks.status_code == 200

    # Start run
    run_id = "run_metrics"
    start_run = {
        "agent_run": {
            "agent_run_id": run_id,
            "validator_round_id": round_id,
            "validator_uid": 1001,
            "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
            "miner_uid": 501,
            "miner_hotkey": "miner_hotkey_501",
            "is_sota": False,
        },
        "miner_identity": {"uid": 501, "hotkey": "miner_hotkey_501"},
        "miner_snapshot": {
            "validator_round_id": round_id,
            "miner_uid": 501,
            "miner_hotkey": "miner_hotkey_501",
            "agent_name": "M",
        },
    }
    r_start = await client.post(
        f"/api/v1/validator-rounds/{round_id}/agent-runs/start?force=true",
        json=start_run,
        headers=_headers(),
    )
    assert r_start.status_code == 200

    # Add two evaluations with final_score 0.6 and 0.8, times 4.0 and 6.0, raw_score 0.6/0.8
    for idx, score in enumerate([0.6, 0.8], start=1):
        payload = {
            "task": task,
            "task_solution": {
                "solution_id": f"solM_{idx}",
                "task_id": task["task_id"],
                "validator_round_id": round_id,
                "agent_run_id": run_id,
                "miner_uid": 501,
                "validator_uid": 1001,
                "validator_hotkey": "5FHeaderHotkey111111111111111111111111111111",
                "actions": [],
            },
            "evaluation_result": {
                "evaluation_id": f"evalM_{idx}",
                "task_id": task["task_id"],
                "task_solution_id": f"solM_{idx}",
                "validator_round_id": round_id,
                "agent_run_id": run_id,
                "miner_uid": 501,
                "validator_uid": 1001,
                "final_score": score,
                "test_results_matrix": [[{"success": True}]],
                "execution_history": [],
                "feedback": None,
                "web_agent_id": None,
                "raw_score": score,
                "evaluation_time": 2.0 + 2.0 * idx,
                "stats": None,
                "gif_recording": None,
            },
        }
        r_eval = await client.post(
            f"/api/v1/validator-rounds/{round_id}/agent-runs/{run_id}/evaluations?force=true",
            json=payload,
            headers=_headers(),
        )
        assert r_eval.status_code == 200

    # Finish round and verify computed metrics
    r_finish = await client.post(
        f"/api/v1/validator-rounds/{round_id}/finish?force=true",
        json={
            "status": "completed",
            "winners": [{"miner_uid": 501}],
            "winner_scores": [0.8],
            "weights": {"501": 1.0},
            "ended_at": 1_700_000_999.0,
        },
        headers=_headers(),
    )
    assert r_finish.status_code == 200

    row = await db_session.scalar(select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == run_id))
    assert row is not None
    # average of [0.6, 0.8]
    assert row.average_score == pytest.approx(0.7)
    # total_reward sums raw/derived reward values; we used raw_score=score
    assert row.total_reward == pytest.approx(1.4)
    # average_execution_time average of [4.0, 6.0] = 5.0
    assert row.average_execution_time == pytest.approx(5.0)

    # Verify top miners endpoint ranks this miner
    r_top = await client.get(f"/api/v1/rounds/{1}/miners/top")
    assert r_top.status_code == 200
    top = r_top.json()
    assert top["success"] is True
    assert top["data"]["miners"]

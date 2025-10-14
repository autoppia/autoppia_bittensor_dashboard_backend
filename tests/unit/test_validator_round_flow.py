import asyncio
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import mock_mongo


@pytest.fixture
def client(tmp_path):
    mock_mongo._mock_client = mock_mongo.MockMongoClient(data_dir=str(tmp_path))
    return TestClient(app)


def _round_payload(round_id: str, validator_uid: int) -> dict:
    return {
        "validator_round_id": str(uuid4()),  # placeholder, replaced in tests
        "round": {
            "round_id": round_id,
            "validators": [
                {
                    "uid": validator_uid,
                    "hotkey": "validator-hotkey",
                    "stake": 1000.0,
                    "vtrust": 0.9,
                }
            ],
            "start_block": 1,
            "start_epoch": 1,
            "n_tasks": 2,
            "n_miners": 1,
            "n_winners": 1,
        },
    }


def test_start_round_requires_identifier(client):
    payload = _round_payload("round-1", 11)
    payload["validator_round_id"] = ""
    response = client.post("/v1/validator-rounds/start", json=payload)
    assert response.status_code == 422


def test_progressive_round_flow(client):
    validator_round_id = str(uuid4())
    round_id = "round-42"
    validator_uid = 77
    miner_uid = 101

    start_payload = _round_payload(round_id, validator_uid)
    start_payload["validator_round_id"] = validator_round_id
    response = client.post("/v1/validator-rounds/start", json=start_payload)
    assert response.status_code == 200

    task_id = str(uuid4())
    agent_run_id = str(uuid4())
    solution_id = str(uuid4())
    evaluation_id = str(uuid4())

    task_definition = {
        "task_id": task_id,
        "round_id": round_id,
        "agent_run_id": None,
        "scope": "local",
        "url": "https://example.com",
        "prompt": "Open the homepage",
    }
    response = client.post(
        f"/v1/validator-rounds/{validator_round_id}/tasks",
        json={"tasks": [task_definition]},
    )
    assert response.status_code == 200

    agent_run_payload = {
        "agent_run": {
            "agent_run_id": agent_run_id,
            "round_id": round_id,
            "validator_uid": validator_uid,
            "miner_uid": miner_uid,
        }
    }
    response = client.post(
        f"/v1/validator-rounds/{validator_round_id}/agent-runs/start",
        json=agent_run_payload,
    )
    assert response.status_code == 200

    evaluation_payload = {
        "task": {
            "task_id": task_id,
            "round_id": round_id,
            "agent_run_id": agent_run_id,
            "scope": "local",
            "url": "https://example.com",
            "prompt": "Open the homepage",
        },
        "task_solution": {
            "solution_id": solution_id,
            "task_id": task_id,
            "round_id": round_id,
            "agent_run_id": agent_run_id,
            "miner_uid": miner_uid,
            "validator_uid": validator_uid,
            "actions": [],
        },
        "evaluation_result": {
            "evaluation_id": evaluation_id,
            "task_id": task_id,
            "task_solution_id": solution_id,
            "round_id": round_id,
            "agent_run_id": agent_run_id,
            "miner_uid": miner_uid,
            "validator_uid": validator_uid,
            "final_score": 0.9,
            "test_results_matrix": [[{"success": True, "extra_data": None}]],
            "execution_history": [],
        },
    }
    response = client.post(
        f"/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations",
        json=evaluation_payload,
    )
    assert response.status_code == 200

    finish_payload = {
        "status": "completed",
        "winners": [{"miner_uid": miner_uid, "score": 0.9}],
        "winner_scores": [0.9],
        "weights": {str(miner_uid): 0.5},
        "ended_at": time.time(),
    }
    response = client.post(
        f"/v1/validator-rounds/{validator_round_id}/finish",
        json=finish_payload,
    )
    assert response.status_code == 200

    db = mock_mongo.get_mock_db()

    round_doc = asyncio.run(db.rounds.find_one({"validator_round_id": validator_round_id}))
    assert round_doc is not None
    assert round_doc["status"] == "completed"
    assert round_doc["winners"] == finish_payload["winners"]

    task_doc = asyncio.run(db.tasks.find_one({"task_id": task_id}))
    assert task_doc is not None
    assert task_doc["agent_run_id"] == agent_run_id

    agent_run_doc = asyncio.run(db.agent_evaluation_runs.find_one({"agent_run_id": agent_run_id}))
    assert agent_run_doc is not None
    assert agent_run_doc["validator_round_id"] == validator_round_id

    evaluation_doc = asyncio.run(db.evaluation_results.find_one({"evaluation_id": evaluation_id}))
    assert evaluation_doc is not None
    assert evaluation_doc["task_id"] == task_id
